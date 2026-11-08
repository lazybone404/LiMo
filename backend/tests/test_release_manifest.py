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

    manifest = release.build_manifest(
        version=VERSION,
        notes="notes",
        signature=SIGNATURE,
        url=release.download_url(SLUG, VERSION, exe.name),
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
    manifest["platforms"][release.PLATFORM_KEY]["signature"] = "stale-signature"
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
    manifest["platforms"][release.PLATFORM_KEY]["url"] = (
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
        version=raw, notes="n", signature="s", url="https://example.com/a.exe"
    )
    assert manifest["version"] == "1.2.3"


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
