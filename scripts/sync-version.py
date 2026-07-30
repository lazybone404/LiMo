#!/usr/bin/env python3
"""同步版本号 — 从根目录 VERSION 文件写入所有需要的位置。

用法:
    python scripts/sync-version.py          # 同步所有位置
    python scripts/sync-version.py 0.2.0    # 先更新 VERSION 再同步
    python scripts/sync-version.py --check  # 仅检查是否一致（CI 用）
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"


def read_version() -> str:
    return VERSION_FILE.read_text().strip()


def write_version(version: str) -> None:
    VERSION_FILE.write_text(version.strip() + "\n")


def replace_in_file(path: Path, pattern: str, replacement: str, count: int = 0) -> int:
    """用正则替换文件内容。返回替换次数。"""
    text = path.read_text(encoding="utf-8")
    new_text, n = re.subn(pattern, replacement, text, count=count)
    if n > 0:
        path.write_text(new_text, encoding="utf-8")
    return n


def sync_all(version: str) -> list[str]:
    """同步所有位置，返回修改过的文件列表。"""
    changed: list[str] = []

    # 1. frontend/package.json
    p = ROOT / "frontend" / "package.json"
    n = replace_in_file(p, r'"version":\s*"[^"]*"', f'"version": "{version}"', count=1)
    if n: changed.append(str(p.relative_to(ROOT)))

    # 2. frontend/src-tauri/tauri.conf.json
    p = ROOT / "frontend" / "src-tauri" / "tauri.conf.json"
    n = replace_in_file(p, r'"version":\s*"[^"]*"', f'"version": "{version}"', count=1)
    if n: changed.append(str(p.relative_to(ROOT)))

    # 3. frontend/src-tauri/Cargo.toml
    p = ROOT / "frontend" / "src-tauri" / "Cargo.toml"
    n = replace_in_file(p, r'^version\s*=\s*"[^"]*"', f'version = "{version}"', count=1)
    if n: changed.append(str(p.relative_to(ROOT)))

    # 4. backend/pyproject.toml
    p = ROOT / "backend" / "pyproject.toml"
    n = replace_in_file(p, r'^version\s*=\s*"[^"]*"', f'version = "{version}"', count=1)
    if n: changed.append(str(p.relative_to(ROOT)))

    # 5. backend/main.py — FastAPI(title="LiMo", version="...")
    p = ROOT / "backend" / "main.py"
    n1 = replace_in_file(p, r'FastAPI\(title="LiMo",\s*version="[^"]*"',
                         f'FastAPI(title="LiMo", version="{version}"', count=1)
    n2 = replace_in_file(p, r'"version":\s*"[^"]*",\s*"started_at"',
                         f'"version": "{version}", "started_at"', count=1)
    if n1 or n2: changed.append(str(p.relative_to(ROOT)))

    # 6. backend/app/routers/capabilities.py
    p = ROOT / "backend" / "app" / "routers" / "capabilities.py"
    n = replace_in_file(p, r'"version":\s*"[^"]*"',
                         f'"version": "{version}"', count=1)
    if n: changed.append(str(p.relative_to(ROOT)))

    # 7. docs/STATUS.md — 文档版本号
    p = ROOT / "docs" / "STATUS.md"
    n = replace_in_file(p, r'\*\*版本\*\*:\s*[^\s]+',
                         f'**版本**: {version}', count=1)
    if n: changed.append(str(p.relative_to(ROOT)))

    return changed


def check_sync(version: str) -> bool:
    """检查所有位置是否一致。返回 True 表示一致。"""
    errors = []

    checks = [
        (ROOT / "frontend" / "package.json",
         r'"version":\s*"([^"]*)"', "package.json"),
        (ROOT / "frontend" / "src-tauri" / "tauri.conf.json",
         r'"version":\s*"([^"]*)"', "tauri.conf.json"),
        (ROOT / "frontend" / "src-tauri" / "Cargo.toml",
         r'^version\s*=\s*"([^"]*)"', "Cargo.toml"),
        (ROOT / "backend" / "pyproject.toml",
         r'^version\s*=\s*"([^"]*)"', "pyproject.toml"),
        (ROOT / "backend" / "main.py",
         r'FastAPI\(title="LiMo",\s*version="([^"]*)"', "main.py (FastAPI)"),
        (ROOT / "backend" / "main.py",
         r'"version":\s*"([^"]*)",\s*"started_at"', "main.py (/health)"),
        (ROOT / "backend" / "app" / "routers" / "capabilities.py",
         r'"version":\s*"([^"]*)"', "capabilities.py"),
        (ROOT / "docs" / "STATUS.md",
         r'\*\*版本\*\*:\s*([^\s]+)', "STATUS.md"),
    ]

    for path, pattern, label in checks:
        m = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
        if not m:
            errors.append(f"  {label}: 未找到版本号")
        elif m.group(1) != version:
            errors.append(f"  {label}: {m.group(1)} ≠ {version}")

    if errors:
        print(f"版本不一致 (VERSION={version}):")
        for e in errors:
            print(e)
        return False
    print(f"所有位置版本一致: {version}")
    return True


def bump_version(version: str, level: str) -> str:
    """升级语义化版本号。level: major | minor | patch"""
    parts = version.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid semver: {version}")
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

    if level == "major":
        return f"{major + 1}.0.0"
    elif level == "minor":
        return f"{major}.{minor + 1}.0"
    elif level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    else:
        raise ValueError(f"Unknown bump level: {level} (use major/minor/patch)")


def git_tag(version: str, push: bool = False) -> None:
    """创建并推送 Git tag。"""
    import subprocess
    tag = f"v{version}"
    subprocess.run(["git", "tag", "-a", tag, "-m", f"Release {tag}"], check=True)
    print(f"Git tag: {tag}")
    if push:
        subprocess.run(["git", "push", "origin", tag], check=True)
        print(f"Pushed {tag} to origin")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        ok = check_sync(read_version())
        sys.exit(0 if ok else 1)

    current = read_version()
    arg = sys.argv[1] if len(sys.argv) > 1 else ""

    if arg in ("patch", "minor", "major"):
        # 语义化版本升级
        new_version = bump_version(current, arg)
        print(f"{current} → {new_version}  ({arg})")
        write_version(new_version)
    elif arg and arg != current:
        # 手动指定版本号
        new_version = arg
        write_version(new_version)
        print(f"VERSION: {current} → {new_version}")
    else:
        # 仅同步（不修改版本号）
        new_version = current

    changed = sync_all(new_version)
    if changed:
        print(f"Synced {len(changed)} files:")
        for f in changed:
            print(f"  OK {f}")
    else:
        print("All files up to date.")

    # 版本变更时打 tag
    if new_version != current:
        do_tag = "--tag" in sys.argv
        if do_tag:
            git_tag(new_version, push="--push" in sys.argv)
        else:
            print(f"Hint: add --tag to create git tag v{new_version}")


if __name__ == "__main__":
    main()
