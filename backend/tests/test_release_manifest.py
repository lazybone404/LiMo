"""`scripts/release.py` 的判据测试。

重点是**每条判据都真会红**：这套链路的失效形态几乎全是「发版侧一切正常，客户端装不上」，
所以测试不能只证明「干净时返回空列表」，还要逐条证明「坏掉时会说出来」。

脚本不是包，按 `test_check_models.py` 的先例从路径加载。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release.py"
_spec = importlib.util.spec_from_file_location("release_script", _SCRIPT)
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

VERSION = "1.2.3"
SLUG = "owner/repo"
PUBKEY = "dW50cnVzdGVk...PUBKEY"
SIGNATURE = "untrusted comment: signature\nRWQ...SIG"
MSI_NAME = f"LiMo_{VERSION}_x64_en-US.msi"
# MSI 用的是**同一把私钥对 MSI 字节**签出来的，所以它与 exe 的签名是两份不同的内容。
MSI_SIGNATURE = "untrusted comment: signature\nRWQ...MSI-SIG"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def project(tmp_path: Path):
    """造一个「除被测那一处外全部正确」的发版目录。"""
    _write(tmp_path / "VERSION", VERSION + "\n")
    _write(
        tmp_path / "frontend/src-tauri/Cargo.toml",
        f'[package]\nname = "limo"\nrepository = "https://github.com/{SLUG}"\n',
    )
    _write(
        tmp_path / "frontend/src-tauri/tauri.conf.json",
        json.dumps({"plugins": {"updater": {"pubkey": PUBKEY}}}),
    )
    keys = tmp_path / "keys"
    _write(keys / "limo-updater.key.pub", PUBKEY + "\n")

    exe = release.installer_path(tmp_path, VERSION)
    _write(exe, "installer-bytes")
    _write(release.signature_path(exe), SIGNATURE)

    # 两种格式都发，且各自要签名（2026-11-12）：MSI 现在有自己的更新键，
    # 少了它的 `.sig` 就等于「用 .msi 装的那半用户永远收不到更新」。
    msi = tmp_path / release.BUNDLE_MSI_DIR / MSI_NAME
    _write(msi, "msi-bytes")
    _write(release.signature_path(msi), MSI_SIGNATURE)

    exe_url = release.download_url(SLUG, VERSION, exe.name)
    manifest = release.build_manifest(
        version=VERSION,
        notes="notes",
        platforms={
            release.PLATFORM_KEY_NSIS: {"signature": SIGNATURE, "url": exe_url},
            release.PLATFORM_KEY_MSI: {
                "signature": MSI_SIGNATURE,
                "url": release.download_url(SLUG, VERSION, MSI_NAME),
            },
            release.PLATFORM_KEY: {"signature": SIGNATURE, "url": exe_url},
        },
        pub_date="2026-01-01T00:00:00Z",
    )
    _write(tmp_path / release.MANIFEST_PATH, json.dumps(manifest, ensure_ascii=False))

    return tmp_path, keys


def _problems(project) -> list[str]:
    root, keys = project
    return release.collect_problems(root, VERSION, SLUG, keys_dir=keys)


def test_clean_project_has_no_problems(project):
    assert _problems(project) == []


def test_missing_installer_is_reported(project):
    root, keys = project
    release.installer_path(root, VERSION).unlink()
    problems = release.collect_problems(root, VERSION, SLUG, keys_dir=keys)
    assert any("安装包不存在" in p for p in problems)


def test_missing_msi_is_reported(project):
    """两种格式都发：MSI 没被上传时 Release 页面看不出少了东西，只能在这里拦。"""
    root, keys = project
    for p in (root / release.BUNDLE_MSI_DIR).glob("*.msi"):
        p.unlink()
    problems = release.collect_problems(root, VERSION, SLUG, keys_dir=keys)
    assert any("MSI 安装包不存在" in p for p in problems)


def test_missing_msi_signature_is_reported(project):
    """MSI 有包没签名 = 用 .msi 装的用户下载完验签失败，而发版侧一切正常。"""
    root, keys = project
    release.signature_path(root / release.BUNDLE_MSI_DIR / MSI_NAME).unlink()
    problems = release.collect_problems(root, VERSION, SLUG, keys_dir=keys)
    assert any("MSI 签名文件不存在" in p for p in problems)


def test_manifest_missing_msi_key_is_reported(project):
    """清单里没有 `windows-x86_64-msi` 键时，MSI 客户端会回落到兜底键拿到 NSIS 包 ——
    两份并存，控制面板两条。这个回落是插件写死的，我们只能靠清单齐全来避免。"""
    root, keys = project
    manifest_path = root / release.MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["platforms"][release.PLATFORM_KEY_MSI]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    problems = release.collect_problems(root, VERSION, SLUG, keys_dir=keys)
    assert any(f"platforms.{release.PLATFORM_KEY_MSI}" in p for p in problems)


def test_msi_key_must_carry_msi_signature(project):
    """把 exe 的签名填进 msi 键：地址对、文件在，只有验签会失败。"""
    root, keys = project
    manifest_path = root / release.MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["platforms"][release.PLATFORM_KEY_MSI]["signature"] = SIGNATURE
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    problems = release.collect_problems(root, VERSION, SLUG, keys_dir=keys)
    assert any(
        f"platforms.{release.PLATFORM_KEY_MSI}.signature" in p for p in problems
    )


def test_missing_signature_is_reported(project):
    root, keys = project
    release.signature_path(release.installer_path(root, VERSION)).unlink()
    problems = release.collect_problems(root, VERSION, SLUG, keys_dir=keys)
    assert any("签名文件不存在" in p for p in problems)


def test_signature_mismatch_is_reported(project):
    """重建安装包后忘了 --write：签名与产物对不上，客户端验签失败。"""
    root, keys = project
    manifest_path = root / release.MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["platforms"][release.PLATFORM_KEY_NSIS]["signature"] = "stale-signature"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    problems = release.collect_problems(root, VERSION, SLUG, keys_dir=keys)
    assert any("signature" in p and "不一致" in p for p in problems)


def test_manifest_version_mismatch_is_reported(project):
    root, keys = project
    manifest_path = root / release.MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "9.9.9"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    problems = release.collect_problems(root, VERSION, SLUG, keys_dir=keys)
    assert any("version" in p and "不一致" in p for p in problems)


def test_manifest_url_basename_must_match_artifact(project):
    root, keys = project
    manifest_path = root / release.MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["platforms"][release.PLATFORM_KEY_NSIS]["url"] = (
        f"https://github.com/{SLUG}/releases/download/v{VERSION}/something-else.exe"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    problems = release.collect_problems(root, VERSION, SLUG, keys_dir=keys)
    assert any("文件名" in p for p in problems)


def test_pubkey_mismatch_is_reported(project):
    """签得出来、客户端验不过 —— 只有公钥对不上时才会发生。"""
    root, keys = project
    _write(keys / "limo-updater.key.pub", "another-pubkey")
    problems = release.collect_problems(root, VERSION, SLUG, keys_dir=keys)
    assert any("公钥不一致" in p for p in problems)


def test_missing_manifest_is_reported(project):
    root, keys = project
    (root / release.MANIFEST_PATH).unlink()
    problems = release.collect_problems(root, VERSION, SLUG, keys_dir=keys)
    assert any("清单不存在" in p for p in problems)


@pytest.mark.parametrize("raw", ["v1.2.3", "1.2.3", "V1.2.3"])
def test_manifest_version_never_carries_v_prefix(raw):
    manifest = release.build_manifest(
        version=raw,
        notes="n",
        platforms={
            release.PLATFORM_KEY: {
                "signature": "s",
                "url": "https://example.com/a.exe",
            }
        },
    )
    assert manifest["version"] == "1.2.3"


def test_manifest_notes_drops_title_and_range_lines():
    """应用内那块是纯文本摘要，标题与「发布日期／范围」两行只会占地方。"""
    raw = (
        "# v1.2.3\n\n"
        "发布日期：2026-11-08　｜　范围：`v0.5.21..HEAD`\n\n"
        "安装版现在可以自己更新了。\n\n"
        "## 新增\n\n- 一条用户看得懂的变化\n"
    )
    notes = release.manifest_notes(raw)
    assert "# v1.2.3" not in notes
    assert "发布日期" not in notes
    # 小节标题与正文必须原样保留 —— 裁过头就成了「摘要里只剩一句话」。
    assert "## 新增" in notes
    assert notes.startswith("安装版现在可以自己更新了。")


def test_print_upload_lists_all_five_artifacts(project, capsys):
    """CLAUDE.md 发布门禁第 5 条：NSIS / 它的 `.sig` / MSI / 它的 `.sig` / `latest.json`。

    漏任何一个 `.sig` 时 Release 页面看起来完全正常，对应那一半客户端却在下载完成后
    验签失败 —— 这条测试钉住的就是「打印出来的命令里到底有哪几个文件」。
    """
    root, _keys = project
    assert release.cmd_print_upload(root) == 0
    out = capsys.readouterr().out
    assert "LiMo_1.2.3_x64-setup.exe" in out
    assert "LiMo_1.2.3_x64-setup.exe.sig" in out
    assert MSI_NAME in out
    assert f"{MSI_NAME}.sig" in out
    assert "dist-release/latest.json" in out
    # 五个文件必须都在 `gh release create` 那一条命令里，而不是补在半个命令后面。
    command = out.split("gh release create", 1)[1].split("--title", 1)[0]
    for name in (
        "LiMo_1.2.3_x64-setup.exe",
        "LiMo_1.2.3_x64-setup.exe.sig",
        MSI_NAME,
        f"{MSI_NAME}.sig",
        "latest.json",
    ):
        assert name in command


def test_print_upload_refuses_without_msi_signature(project, capsys):
    """MSI 缺签名时同样一个字都不打印 —— 半条命令会诱导发一个「一半用户装不上」的版本。"""
    root, _keys = project
    release.signature_path(root / release.BUNDLE_MSI_DIR / MSI_NAME).unlink()

    assert release.cmd_print_upload(root) == 1
    captured = capsys.readouterr()
    assert "MSI 签名文件缺失" in captured.err
    assert "gh release create" not in captured.out


def test_print_upload_refuses_without_signature(project, capsys):
    root, _keys = project
    release.signature_path(release.installer_path(root, VERSION)).unlink()

    assert release.cmd_print_upload(root) == 1
    captured = capsys.readouterr()
    assert "签名文件缺失" in captured.err
    # 命令一个字都不该打印：它是要直接贴进终端的，打半条等于诱导发一个装不上的版本。
    assert "gh release create" not in captured.out


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/owner/repo.git", "owner/repo"),
        ("https://github.com/owner/repo", "owner/repo"),
        ("git@github.com:owner/repo.git", "owner/repo"),
        ("https://gitee.com/owner/repo", None),
    ],
)
def test_repo_slug_from_url(url, expected):
    assert release.repo_slug_from_url(url) == expected
