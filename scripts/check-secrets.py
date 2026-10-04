#!/usr/bin/env python3
"""凭据卫生自检 —— 确认 LiMo 自己落盘的文件里没有明文凭据。

用法：
    python scripts/check-secrets.py --check
    python scripts/check-secrets.py --check --root "D:/somewhere" --json
    python scripts/check-secrets.py --selftest          # 自证扫描器有区分力

**定位：卫生，不是安全边界。** 它回答的是「这份配置被复制走 / 同步进网盘 / 打进反馈
附件时，里面有没有能直接用的凭据」，不是「本机有没有被入侵」。

两条判据，**各自能独立失败**：
  ① 结构 —— `config.yaml`（含启动备份 `.bak`）的敏感字段**非空** ⇒ 明文还在盘上。
  ② 值   —— 已知凭据值出现在某个被扫文件的内容里 ⇒ 有环节把它写了出去
            （日志 / 导出产物 / sqlite / ……）。密钥库 `secrets.bin` 也走这条：
            DPAPI 生效时它**不可能**含明文，命中等价于「加密没生效」。

⚠️ 输出**绝不回显凭据值**，只给「哪个文件、第几行、哪一类字段」—— 自检自己要是把
值打出来，它就成了新的泄漏通道。

⚠️ 敏感字段的清单**不在这里重写一份**，从 `backend/app/core/config_io.py` 读
（同一份表还驱动着 save/load 两侧的剥离与回填，抄一份必然漂移）。
"""

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

#: 低于这个长度的值不参与内容扫描 —— 短串在日志/正文里到处都是，命中全是噪声。
MIN_SECRET_LEN = 8

#: 单文件扫描上限。超大文件（视频、模型、数据包）跳过：凭据不会藏在里面，
#: 而读它们会让自检从「秒回」变成「等一分钟」。
MAX_FILE_BYTES = 32 * 1024 * 1024

#: 目录级跳过 —— 仓库 root 被当作 --root 时（防「开发期把 key 写进代码」）会用到。
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", "target"}

#: 这两个文件由**结构判据**负责，值判据跳过它们（否则同一件事报两遍）。
CONFIG_FILE_NAMES = {"config.yaml", "config.yaml.bak"}


# ── 从 backend 取唯一真相源 ────────────────────────────────────────

@dataclass
class Backend:
    """backend 侧被本脚本复用的东西：敏感字段表、config.yaml 的路径拼法、密钥库读写。

    ⚠️ 一律**取用**而非复写。那张表同时驱动 `save_config` 的剥离、`load_config` 的
    回填与这里的检查；自检若另抄一份，两边迟早对不上，而「对不上」的表现恰恰是
    「自检全绿、盘上却有明文」—— 最坏的一种失败（自检本身成了噪声源）。
    """
    config_io: Any
    secrets: Any
    keystore: Path

    @property
    def ref_key(self) -> str:
        return self.config_io.SECRETS_REF_KEY

    @property
    def scalars(self) -> tuple:
        return self.config_io.SECRET_SCALARS

    @property
    def lists(self) -> tuple:
        return self.config_io.SECRET_LISTS

    def config_file(self, cfg: dict | None = None) -> Path:
        return self.config_io.config_file_path(cfg)


def load_backend() -> Backend:
    sys.path.insert(0, str(ROOT / "backend"))
    from app.core import config_io
    from app.core import secrets as secrets_mod
    return Backend(config_io=config_io, secrets=secrets_mod,
                   keystore=secrets_mod.secrets_path())


def _dig(cfg: dict, path: str):
    cur = cfg
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


# ── 扫描 ───────────────────────────────────────────────────────────

@dataclass
class Finding:
    kind: str      # "plaintext-field" | "value-leak"
    where: str     # 文件路径（相对 root，便于贴进 issue）
    detail: str    # 字段标签 / 行号 —— **不含值**
    hint: str = ""


def _line_of(raw: bytes, needle: bytes) -> str:
    """命中位置的行号。二进制里算不出有意义行号，如实说。"""
    idx = raw.find(needle)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "二进制文件"
    return f"第 {text.count(chr(10), 0, idx) + 1} 行"


def _read_config(path: Path) -> dict | None:
    """读**磁盘原貌**的 config.yaml。

    ⚠️ 刻意**不走 `load_config()`** —— 那个函数会从密钥库回填凭据，回填后的内存
    配置处处非空，自检就永远"没问题"了。这里要看的就是盘上那份。
    """
    import yaml
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return None


def scan_config_structure(cfg: dict, label: str, be: Backend) -> list[Finding]:
    """判据①：敏感字段非空 ⇒ 明文还在盘上。"""
    out = []
    if not cfg:
        return out
    for path in be.scalars:
        val = _dig(cfg, path)
        if val:
            out.append(Finding("plaintext-field", label, f"{path}（{len(str(val))} 字符）",
                               "该字段应为空，真实值应在 DPAPI 密钥库里"))
    for key, id_field, val_field in be.lists:
        for item in cfg.get(key) or []:
            if isinstance(item, dict) and item.get(val_field):
                out.append(Finding("plaintext-field", label,
                                   f"{key}[{item.get(id_field)}].{val_field}",
                                   "该字段应为空，真实值应在 DPAPI 密钥库里"))
    return out


def known_secrets(cfg: dict, be: Backend) -> dict[str, str]:
    """收集「已知凭据值」，标签 → 值。标签会进报告，**值不会**。

    两个来源都要：盘上明文的那份（还没迁移）+ 密钥库里的那份（已迁移）。
    """
    out: dict[str, str] = {}
    for path in be.scalars:
        val = _dig(cfg, path)
        if val:
            out[f"config.yaml:{path}"] = str(val)
    for key, id_field, val_field in be.lists:
        for item in cfg.get(key) or []:
            if isinstance(item, dict) and item.get(val_field):
                out[f"config.yaml:{key}[{item.get(id_field)}]"] = str(item[val_field])

    if be.keystore.exists():
        try:
            data = be.secrets.load_secrets()
        except be.secrets.SecretsUnavailable as e:
            # 解不开 ≠ 没有凭据，只是这一轮拿不到值 —— 如实记一笔，别静默当空。
            print(f"  （密钥库解不开，跳过来源值比对：{e}）", file=sys.stderr)
            data = {}
        for path, val in (data.get("fields") or {}).items():
            out[f"keystore:{path}"] = val
        for key, _id_field, val_field in be.lists:
            for k, val in (data.get(key) or {}).items():
                out[f"keystore:{key}[{k}]"] = val
    return out


def scan_values(root: Path, known: dict[str, str]) -> list[Finding]:
    """判据②：已知凭据值出现在文件内容里。"""
    out: list[Finding] = []
    needles = {label: v.encode("utf-8") for label, v in known.items() if len(v) >= MIN_SECRET_LEN}
    if not needles:
        return out

    for path in _iter_files(root):
        if path.name in CONFIG_FILE_NAMES:
            continue  # 结构判据负责
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        for label, needle in needles.items():
            if needle in raw:
                try:
                    where = str(path.relative_to(root))
                except ValueError:
                    where = str(path)
                out.append(Finding("value-leak", where, f"{_line_of(raw, needle)} · {label}",
                                   "凭据被写进了这个文件，检查是哪个环节漏了"))
    return out


def _iter_files(root: Path):
    if root.is_file():
        yield root
        return
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            yield Path(dirpath) / name


# ── 默认扫描点 ─────────────────────────────────────────────────────

def default_roots(cfg: dict, keystore_path: Path) -> list[Path]:
    """默认扫「LiMo 主动写出的位置」：数据目录 + 应用数据目录（密钥库所在）。

    ⚠️ 扫的是**用户库实例 + 运行期产物**，不是仓库源码 —— 那是另一条判据
    （「开发时把 key 写进代码」），用 `--root` 指向仓库单跑，别混计。
    """
    roots: list[Path] = []
    data_dir = (cfg or {}).get("data_dir") or str(Path.home() / ".limo")
    roots.append(Path(data_dir))
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    app_dir = Path(local) / "LiMo"
    if app_dir not in roots:
        roots.append(app_dir)
    # 密钥库被 LIMO_SECRETS 指到别处时把它所在的目录也带上
    if keystore_path.exists() and keystore_path.parent not in roots:
        roots.append(keystore_path.parent)
    return roots


# ── 自证 ───────────────────────────────────────────────────────────

def selftest() -> int:
    """自证扫描器**有区分力**：造一个含哨兵的文件 + 一个不含的，断言前者命中、后者不命中。

    ⚠️ 少了这一步，「零命中」与「扫描器根本没跑起来」无法区分（铁律 #38）。
    """
    import shutil
    tmp = Path(tempfile.mkdtemp(prefix="limo_check_secrets_selftest_"))
    try:
        sentinel = "sk-SELFTEST-0xdeadbeef-1234"
        hit = tmp / "leaky.log"
        hit.write_text(f"2026-01-01 载入配置 api_key={sentinel}\n", encoding="utf-8")
        miss = tmp / "clean.log"
        miss.write_text("2026-01-01 载入配置 api_key=<已脱敏>\n", encoding="utf-8")

        found = scan_values(tmp, {"llm.api_key": sentinel})
        names = [f.where for f in found]
        ok_hit = any("leaky.log" in n for n in names)
        ok_miss = not any("clean.log" in n for n in names)
        ok_line = any("第 1 行" in f.detail for f in found if "leaky.log" in f.where)

        for label, cond in (("含哨兵的文件被命中", ok_hit),
                            ("不含哨兵的文件未被误报", ok_miss),
                            ("报出了正确行号", ok_line)):
            print(f"{'PASS' if cond else 'FAIL'}: {label}")
        if ok_hit and ok_miss and ok_line:
            print("\n扫描器自证通过：能抓到真泄漏，且不误报")
            return 0
        print("\n扫描器自证**失败** —— 上面的检查结果不可信")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 主流程 ─────────────────────────────────────────────────────────

def render(findings: list[Finding], roots: list[Path]) -> str:
    lines = ["凭据卫生自检", f"扫描点：{'、'.join(str(r) for r in roots)}", ""]
    if not findings:
        lines.append("✅ 未发现明文凭据（结构判据 + 内容判据皆通过）")
        return "\n".join(lines)
    struct = [f for f in findings if f.kind == "plaintext-field"]
    leaks = [f for f in findings if f.kind == "value-leak"]
    lines.append(f"❌ 发现 {len(findings)} 处问题"
                 f"（明文落盘 {len(struct)} · 内容泄漏 {len(leaks)}）")
    for group, title in ((struct, "① 明文仍在磁盘上"), (leaks, "② 凭据被写进了文件内容")):
        if not group:
            continue
        lines.append(f"\n{title}")
        for f in group:
            lines.append(f"  · {f.where}  →  {f.detail}")
            if f.hint:
                lines.append(f"      {f.hint}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="LiMo 凭据卫生自检")
    ap.add_argument("--check", action="store_true", help="执行检查（默认行为，显式写出更清楚）")
    ap.add_argument("--root", action="append", default=[],
                    help="扫描根目录，可重复。省略则扫数据目录 + 应用数据目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非报告")
    ap.add_argument("--selftest", action="store_true", help="自证扫描器有区分力后退出")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    be = load_backend()
    cfg_file = be.config_file()
    disk_cfg = _read_config(cfg_file) or {}
    # 再读一遍启动备份（它与 config.yaml 是两份独立文件，各自可能带明文）
    bak_file = cfg_file.with_suffix(".yaml.bak")

    findings: list[Finding] = []
    for path, label in ((cfg_file, "config.yaml"), (bak_file, "config.yaml.bak")):
        if path.exists():
            findings += scan_config_structure(_read_config(path) or {}, label, be)

    known = known_secrets(disk_cfg, be)
    roots = [Path(r) for r in args.root] if args.root else default_roots(disk_cfg, be.keystore)
    roots = [r for r in roots if r.exists()]
    for root in roots:
        findings += scan_values(root, known)

    if args.json:
        print(json.dumps({
            "roots": [str(r) for r in roots],
            "findings": [{"kind": f.kind, "where": f.where, "detail": f.detail} for f in findings],
            "ok": not findings,
        }, ensure_ascii=False, indent=2))
    else:
        print(render(findings, roots))
        if disk_cfg.get(be.ref_key):
            print(f"\n（config.yaml 已带 {be.ref_key}: 标记，凭据在 DPAPI 密钥库）")

    return 1 if findings else 0


if __name__ == "__main__":
    # 退出码三态，**必须能区分「干净」与「没跑成」**：
    #   0 = 干净 ｜ 1 = 发现明文凭据 ｜ 2 = 自检自身出错（未完成，结论不可用）
    # 少了 2 这一档，脚本崩掉与「查出问题」都是 1，CI 上两者会被当成同一件事。
    import traceback
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        print("\n自检**未能完成**（既不是「干净」也不是「发现明文」）—— 退出码 2", file=sys.stderr)
        sys.exit(2)
