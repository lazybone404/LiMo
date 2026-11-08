#!/usr/bin/env python3
"""发版工具：生成并校验自动更新清单 `latest.json`。

**它不联网、不上传。** 上传是对外动作，按「打包 ≠ 发布」由人执行 `gh release create`。

用法::

    python scripts/release.py --write           # 由构建产物生成 dist-release/latest.json
    python scripts/release.py --check           # 发版门禁（退出码 0/1/2）
    python scripts/release.py --print-upload    # 打印 gh release create 命令

退出码三态（与 check-secrets.py / migrate-secrets.py 一致）：

- ``0`` 通过
- ``1`` 发现问题（缺文件、签名不符、版本不一致……）
- ``2`` 脚本自身出错（读不到文件、JSON 坏了、git 不可用）

为什么 `--check` 要逐条比对而不是只看文件在不在：这套链路的失效形态几乎全是
「发版侧一切正常，客户端装不上」。见 `CHECK_ORDER` 里每条判据的说明。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BUNDLE_NSIS_DIR = Path("frontend/src-tauri/target/release/bundle/nsis")
TAURI_CONF = Path("frontend/src-tauri/tauri.conf.json")
CARGO_TOML = Path("frontend/src-tauri/Cargo.toml")
MANIFEST_PATH = Path("dist-release/latest.json")
KEYS_DIR = ROOT.parent / ".release-keys"

# 客户端只看这一个键（`frontend/src-tauri/src/updater.rs` 里 `platforms` 的 target 名）。
PLATFORM_KEY = "windows-x86_64"


def read_version(root: Path = ROOT) -> str:
    raw = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not raw:
        raise RuntimeError("VERSION 文件为空")
    return raw


def installer_path(root: Path, version: str) -> Path:
    """按 tauri-bundler 的命名推导：`{productName}_{version}_{arch}-setup.exe`。"""
    return root / BUNDLE_NSIS_DIR / f"LiMo_{version}_x64-setup.exe"


def signature_path(exe: Path) -> Path:
    return exe.with_name(exe.name + ".sig")


def repo_slug_from_url(url: str) -> str | None:
    """`https://github.com/o/r.git` / `git@github.com:o/r.git` → `o/r`。"""
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/*$", url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def git_origin_slug(root: Path = ROOT) -> str | None:
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return repo_slug_from_url(out.stdout)


def cargo_repository(root: Path = ROOT) -> str | None:
    text = (root / CARGO_TOML).read_text(encoding="utf-8")
    m = re.search(r'^\s*repository\s*=\s*"([^"]*)"', text, re.MULTILINE)
    return m.group(1) if m else None


def tauri_pubkey(root: Path = ROOT) -> str:
    conf = json.loads((root / TAURI_CONF).read_text(encoding="utf-8"))
    return (conf.get("plugins", {}).get("updater", {}).get("pubkey") or "").strip()


def release_notes_path(root: Path, version: str) -> Path:
    return root / f"docs/changelog/releases/v{version}.md"


def manifest_notes(raw: str) -> str:
    """把 Release 正文裁成「应用内卡片能读的样子」。

    发版文件以 `# v0.5.23` 开头，第二段还有一行「发布日期：…｜范围：`v0.5.21..HEAD`」。
    应用内那一段是纯文本渲染的摘要，这两行只会占掉显示空间 —— 版本号卡片自己已经写了。
    Release 页面上的正文用的仍是**原文件**（`gh release create --notes-file`），
    与这里不是同一份，措辞上的取舍只影响应用内。
    """
    lines = [ln for ln in raw.splitlines() if not ln.lstrip().startswith("# ")]
    lines = [ln for ln in lines if not ln.startswith("发布日期：")]
    return "\n".join(lines).strip()


def build_manifest(version: str, notes: str, signature: str, url: str,
                   pub_date: str | None = None) -> dict:
    """纯函数：只拼结构，不读文件。测试直接喂参数。"""
    return {
        "version": version.lstrip("vV"),
        "notes": notes,
        "pub_date": pub_date or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": {
            PLATFORM_KEY: {
                "signature": signature,
                "url": url,
            }
        },
    }


def download_url(slug: str, version: str, exe_name: str) -> str:
    return f"https://github.com/{slug}/releases/download/v{version}/{exe_name}"


def collect_problems(root: Path, version: str, slug: str | None,
                     keys_dir: Path | None = None) -> list[str]:
    """发版门禁的全部判据。纯文件系统读取，不联网、不跑子进程。

    版本一致性（sync-version.py --check）不在这里 —— 那是另一个脚本的判据，
    由 main() 调子进程执行，**不复刻**（模式 #54）。
    """
    problems: list[str] = []

    if not slug:
        problems.append("无法从 git remote 推导仓库地址（需要形如 github.com/owner/repo）")
        return problems

    exe = installer_path(root, version)
    if not exe.exists():
        problems.append(f"安装包不存在：{exe.relative_to(root)}（先跑 tauri build）")
        return problems  # 后面的判据都依赖它，没有就没法查

    sig_path = signature_path(exe)
    if not sig_path.exists():
        problems.append(
            f"签名文件不存在：{sig_path.relative_to(root)}"
            "（构建时漏了 --config src-tauri/tauri.release.conf.json）"
        )
        return problems
    signature = sig_path.read_text(encoding="utf-8").strip()
    if not signature:
        problems.append(f"签名文件为空：{sig_path.relative_to(root)}")
        return problems

    # 公钥三处：配置里有、且与密钥目录里的 .pub 一致。
    # 不一致的表现是「签得出来，客户端验不过」，只在真实升级时暴露。
    pubkey = tauri_pubkey(root)
    keys_dir = keys_dir or KEYS_DIR
    if not pubkey:
        problems.append("tauri.conf.json 的 plugins.updater.pubkey 为空")
    else:
        pubs = sorted(keys_dir.glob("*.pub")) if keys_dir.is_dir() else []
        if not pubs:
            problems.append(f"找不到公钥文件：{keys_dir}/*.pub（发版机应持有签名密钥）")
        else:
            key_text = pubs[0].read_text(encoding="utf-8").strip()
            if key_text != pubkey:
                problems.append(
                    f"公钥不一致：tauri.conf.json 与 {pubs[0].name} 不同"
                    "（用这把私钥签的包，客户端验不过）"
                )

    manifest_file = root / MANIFEST_PATH
    if not manifest_file.exists():
        problems.append(f"清单不存在：{MANIFEST_PATH}（先跑 --write）")
        return problems

    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        problems.append(f"清单不是合法 JSON：{MANIFEST_PATH}（{e}）")
        return problems

    mv = str(manifest.get("version", ""))
    if mv != version:
        problems.append(f"清单 version={mv!r} 与 VERSION={version!r} 不一致")
    if mv.startswith(("v", "V")):
        # semver 解析会直接失败，客户端只会得到「没有更新」，不报错。
        problems.append(f"清单 version 不能带 v 前缀：{mv!r}")

    platform = (manifest.get("platforms") or {}).get(PLATFORM_KEY) or {}
    if not platform:
        problems.append(f"清单缺少 platforms.{PLATFORM_KEY}")
        return problems

    if str(platform.get("signature", "")).strip() != signature:
        problems.append(
            "清单 signature 与 .sig 文件不一致"
            "（重新构建后忘了重跑 --write：客户端验签失败，更新装不上）"
        )

    url_name = str(platform.get("url", "")).rsplit("/", 1)[-1]
    if url_name != exe.name:
        problems.append(f"清单 url 的文件名 {url_name!r} 与产物 {exe.name!r} 不一致")
    expected_url = download_url(slug, version, exe.name)
    if str(platform.get("url", "")).strip() != expected_url:
        problems.append(f"清单 url 不是预期的直连地址：{platform.get('url')!r}")

    return problems


def cmd_write(root: Path = ROOT) -> int:
    version = read_version(root)
    exe = installer_path(root, version)
    if not exe.exists():
        print(f"[release] 安装包不存在：{exe.relative_to(root)}", file=sys.stderr)
        return 1
    sig_path = signature_path(exe)
    if not sig_path.exists():
        print(
            f"[release] 签名文件不存在：{sig_path.relative_to(root)}\n"
            "          构建时需带 --config src-tauri/tauri.release.conf.json",
            file=sys.stderr,
        )
        return 1
    signature = sig_path.read_text(encoding="utf-8").strip()
    if not signature:
        print(f"[release] 签名文件为空：{sig_path.relative_to(root)}", file=sys.stderr)
        return 1

    slug = git_origin_slug(root)
    if not slug:
        print("[release] 无法从 git remote 推导仓库地址", file=sys.stderr)
        return 1

    notes_file = release_notes_path(root, version)
    notes = notes_file.read_text(encoding="utf-8") if notes_file.exists() else f"LiMo v{version}"
    notes = manifest_notes(notes)

    manifest = build_manifest(
        version=version,
        notes=notes,
        signature=signature,
        url=download_url(slug, version, exe.name),
    )

    out = root / MANIFEST_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[release] 已写入 {MANIFEST_PATH}")
    print(f"[release] 版本 {version}  签名 {len(signature)} 字符")
    return 0


def cmd_check(root: Path = ROOT) -> int:
    problems: list[str] = []

    # 版本一致性：调 sync-version.py，不在本脚本重写七处比对（同源）。
    sync = subprocess.run(
        [sys.executable, str(root / "scripts/sync-version.py"), "--check"],
        cwd=root, capture_output=True, text=True,
    )
    if sync.returncode != 0:
        problems.append("版本号不一致（scripts/sync-version.py --check 未通过）")
        tail = (sync.stdout + sync.stderr).strip().splitlines()
        problems.extend(f"    {line}" for line in tail[:6])

    version = read_version(root)
    slug = git_origin_slug(root)

    repo = cargo_repository(root)
    repo_slug = repo_slug_from_url(repo) if repo else None
    if not repo:
        problems.append("Cargo.toml 缺少 repository 字段（客户端用它拼清单地址）")
    elif slug and repo_slug != slug:
        problems.append(
            f"Cargo.toml 的 repository（{repo_slug}）与 git remote（{slug}）不一致"
            "（客户端会去检查另一个仓库）"
        )

    problems.extend(collect_problems(root, version, slug))

    if problems:
        print("[release] 发版门禁未通过：", file=sys.stderr)
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        return 1

    print(f"[release] 发版门禁通过：v{version}")
    return 0


def cmd_print_upload(root: Path = ROOT) -> int:
    version = read_version(root)
    exe = installer_path(root, version)
    if not exe.exists():
        print(f"[release] 安装包不存在：{exe.relative_to(root)}", file=sys.stderr)
        return 1

    # 签名文件缺失就**不打印命令**：这条命令是要直接贴进终端的，缺参数的后果是
    # 一个看起来发布成功、客户端却装不上的 Release。宁可现在报错。
    sig = signature_path(exe)
    if not sig.exists() or not sig.read_text(encoding="utf-8").strip():
        print(f"[release] 签名文件缺失或为空：{sig.relative_to(root)}"
              "（构建时漏了 --config src-tauri/tauri.release.conf.json）", file=sys.stderr)
        return 1

    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    notes = release_notes_path(root, version)
    notes_arg = f"--notes-file {notes.relative_to(root).as_posix()}" if notes.exists() else f'--notes "LiMo v{version}"'

    # ⚠️ 三样缺一不可（CLAUDE.md 发布门禁第 5 条）：安装包、`.sig`、`latest.json`。
    # 漏了 `.sig` 不影响 Release 页面上的「已发布」，但客户端拿不到签名就无法装 ——
    # 表现为「提示有新版本，下载完成后什么都没发生」。
    print("# 上传是对外动作，确认前先跑一遍 --check")
    print("# ⚠️ 三样都要传：安装包 / .sig / latest.json（缺 .sig = 客户端下载完验签失败）")
    print(f"gh release create v{version} \\")
    print(f"  {exe.relative_to(root).as_posix()} \\")
    print(f"  {sig.relative_to(root).as_posix()} \\")
    print(f"  {MANIFEST_PATH.as_posix()} \\")
    print(f"  --title \"LiMo {version}\" {notes_arg}")
    print()
    print(f"# {exe.name} SHA256")
    print(digest)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成并校验自动更新清单 latest.json")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="由构建产物生成 latest.json")
    group.add_argument("--check", action="store_true", help="发版门禁")
    group.add_argument("--print-upload", action="store_true", help="打印 gh release create 命令")
    args = parser.parse_args(argv)

    try:
        if args.write:
            return cmd_write()
        if args.print_upload:
            return cmd_print_upload()
        return cmd_check()
    except Exception as e:  # noqa: BLE001 —— 脚本自身出错必须与「查出问题」区分开
        import traceback
        traceback.print_exc()
        print(f"[release] 脚本自身出错：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
