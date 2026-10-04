#!/usr/bin/env python3
"""把 `config.yaml` 里的明文凭据迁移进 DPAPI 密钥库。

用法：
    python scripts/migrate-secrets.py --check      # 只报告会改什么，**不写任何文件**
    python scripts/migrate-secrets.py              # 执行迁移
    python scripts/migrate-secrets.py --rollback   # 还原（凭据从密钥库填回 YAML）

退出码：0 = 完成 / 无需迁移 ｜ 1 = 需要迁移（`--check`）/ 迁移失败 ｜ 2 = 脚本自身出错
（三态与 `check-secrets.py` 一致：少了 2 这一档，「脚本崩了」与「查出问题」在 CI 上
是同一个 1，无法区分。）

**它解决什么**：二期之前所有凭据明文躺在 `~/.limo/config.yaml` 里（各家 API Key /
B 站 YouTube cookie / Tavily Key）。复制配置给别人、同步进网盘、把配置文件贴进反馈
附件，都会连凭据一起带走。迁移后 YAML 里只留一个 `secrets_ref` 标记，真实值在
`%LOCALAPPDATA%\\LiMo\\secrets.bin`（DPAPI 加密，绑定当前 Windows 账户）。

**幂等**：重复执行无副作用 —— 已迁移的配置读进来时值会先**从密钥库填回**，再交给
`save_config` 重新抽取，落盘字节不变。⚠️ 这一步不是修辞：少了它，第二次运行时内存
配置里敏感字段全空，`save_config` 会判定「用户清空了凭据」而**删掉密钥库**。

⚠️ **为什么不写明文备份**：计划里原写「先备份 `config.yaml.pre-migrate.bak` 供回滚」，
但那等于**在磁盘上新增一份明文副本** —— 正是本次要消灭的东西，且它是静默的（用户不
会知道多了一份）。回滚改由 `--rollback` 承担：从密钥库填回 YAML，是这次迁移**真正的
逆操作**，比恢复备份更精准。需要额外保险的话，请在执行前**手动**把 config.yaml 复制
到库目录之外（`--check` 也会提示这一点）。

⚠️ **什么时候别跑**：LiMo 正在运行时（脚本会检测 7413 端口并提示）。迁移动的是磁盘，
而后端内存里持有的是启动时载入的那份；运行中的实例不会察觉，但它之后任意一次保存都
会把内存配置重新落盘 —— 那次会走正常加密路径，不会退回明文，只是期间的语义容易让
人对不上账。**在应用关闭时跑最干净。**

**定位：卫生，不是安全边界**（同 `secrets.py`）—— DPAPI 挡的是配置文件被复制走的
被动泄漏，挡不住同账户下的主动恶意进程。
"""

import argparse
import json
import os
import shutil
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ── 从 backend 取唯一真相源 ────────────────────────────────────────

def _bootstrap():
    sys.path.insert(0, str(ROOT / "backend"))
    from app.core import config_io
    from app.core import secrets as secrets_mod
    return config_io, secrets_mod


# ── 观察 ───────────────────────────────────────────────────────────

def summarize(cfg: dict, config_io) -> list[str]:
    """列出**仍带明文值**的敏感字段。只给字段名，**值绝不出现**。

    与 `check-secrets.py` 的结构判据同源（同一张 `SECRET_SCALARS` / `SECRET_LISTS`
    表）—— 两处都在做「盘上还有没有明文」，表必须只有一份。
    """
    out: list[str] = []
    for path in config_io.SECRET_SCALARS:
        if config_io._dig(cfg, path):
            out.append(path)
    for key, id_field, val_field in config_io.SECRET_LISTS:
        for item in cfg.get(key) or []:
            if isinstance(item, dict) and item.get(val_field):
                out.append(f"{key}[{item.get(id_field)}].{val_field}")
    return out


def keystore_summary(data: dict) -> str:
    """密钥库里有什么（只给条数，不给值）。"""
    scalars = len(data.get("fields") or {})
    provs = len(data.get("providers") or {})
    engines = len(data.get("engine_providers") or {})
    return f"标量 {scalars} · 厂商 {provs} · 引擎 {engines}"


def backend_running() -> bool:
    """后端在 7413 上监听吗（提醒用，不阻断）。"""
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", 7413)) == 0


def read_yaml(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# ── 动作 ───────────────────────────────────────────────────────────

def do_migrate(config_io, secrets_mod, path: Path) -> int:
    """迁移：把明文抽进密钥库 + 清空 YAML 字段 + 刷新 `.bak`。"""
    cfg_path = str(path)
    # ⚠️ 把目标文件钉死在环境变量里再调 `save_config`：它按 `config_file_path(cfg)`
    #    决定写哪儿，而那个函数在 env 缺席时会用 `cfg["data_dir"]` 推路径 —— 磁盘上的
    #    `data_dir` 可能是相对值（`"data"`），于是「读 A 文件、写 B 文件」，两边都报成功。
    os.environ["LIMO_CONFIG"] = cfg_path

    disk = read_yaml(path)
    before = summarize(disk, config_io)

    # 幂等的前提（见模块 docstring）：先填回，再抽走。
    if disk.get(config_io.SECRETS_REF_KEY):
        try:
            stored = secrets_mod.load_secrets()
        except secrets_mod.SecretsUnavailable as e:
            # ⚠️ 必须中止，不能「当作空库继续」——继续下去 save_config 会走进
            #    「用户清空了凭据」分支，**把密钥库删掉**。解不开时最该做的是别动它。
            print(f"✗ 密钥库解不开，已中止（未改动任何文件）：{e}", file=sys.stderr)
            print("  换过 Windows 账户 / entropy 变过 / 文件损坏都可能。", file=sys.stderr)
            print("  原文件未被触碰；如需重置，请手动把密钥库移走后重跑。", file=sys.stderr)
            return 1
        config_io.inject_secrets(disk, stored)

    # 下沉 + 剥派生 + 抽凭据 + 原子写，全在这一句里（与生产保存路径同一份实现）。
    config_io.save_config(disk)

    after = summarize(read_yaml(path), config_io)

    # `.bak` 是**迁移前**的副本，会带着明文再活一个启动周期（`load_config` 每次启动
    # 才刷新它）。这里主动刷新一次，让「迁移完成」与「盘上无明文」同时成立。
    bak = path.with_suffix(".yaml.bak")
    refreshed = bak.exists()
    if refreshed:
        shutil.copy2(path, bak)

    data = secrets_mod.load_secrets()
    print(f"✓ 迁移完成：{path}")
    cleared = [f for f in before if f not in after]
    if cleared:
        print(f"  清空字段（{len(cleared)}）：" + "、".join(cleared))
    print(f"  密钥库：{secrets_mod.secrets_path()}（{keystore_summary(data)}）")
    # ⚠️ 引用**值**的真源在 `secrets`（`config_io` 只持有键名）—— 从那边取，别在这抄一份字面量。
    print(f"  secrets_ref：{config_io.SECRETS_REF_KEY} = {secrets_mod.SECRETS_REF}")
    if refreshed:
        print(f"  已刷新启动备份：{bak.name}")
    if after:
        print(f"⚠️  仍有明文未清（{len(after)}）：" + "、".join(after), file=sys.stderr)
        return 1
    print("\n回滚方式：python scripts/migrate-secrets.py --rollback")
    return 0


def do_rollback(config_io, secrets_mod, path: Path) -> int:
    """还原：把密钥库里的值填回 YAML，撤掉 `secrets_ref`，删除密钥库。"""
    disk = read_yaml(path)
    if not disk.get(config_io.SECRETS_REF_KEY):
        print("无需回滚：config.yaml 未处于「凭据已外移」形态")
        return 0
    try:
        stored = secrets_mod.load_secrets()
    except secrets_mod.SecretsUnavailable as e:
        print(f"✗ 密钥库解不开，无法回滚：{e}", file=sys.stderr)
        return 1
    if not config_io.has_secrets(stored):
        print("密钥库是空的，没有可还原的凭据；仅移除 secrets_ref 标记。")

    n = config_io.inject_secrets(disk, stored)
    disk.pop(config_io.SECRETS_REF_KEY, None)
    # ⚠️ 这里**必须绕过 `save_config`** —— 那个函数的职责正是把这些值再抽走。
    config_io.write_yaml_atomic(disk, path)
    secrets_mod.save_secrets({})  # 空 dict = 删除文件

    bak = path.with_suffix(".yaml.bak")
    if bak.exists():
        shutil.copy2(path, bak)

    print(f"✓ 已还原 {n} 条凭据到 {path}，密钥库已删除")
    # 迁移时 `fold_to_providers` 把 `llm.api_key` / `cloud.dashscope` 下沉进了 provider
    # 列表（密钥库里因此**没有**这两条 —— 它们是被 skip 的重复副本）。回滚只能填回
    # 库里有的东西，所以这两个旧字段仍是空。**不是丢失**：真相源在 provider 列表里，
    # `load_config` 的 `_hydrate_provider_keys` 启动时会镜像回旧字段。
    if not (disk.get("llm") or {}).get("api_key") and any(
            isinstance(p, dict) and p.get("api_key") for p in disk.get("providers") or []):
        print("  说明：llm.api_key 仍为空是**下沉的结果** —— 真值在 providers 列表里，"
              "启动时 load_config 会自动镜像回来。")
    print("⚠️  现在 config.yaml 里又是明文凭据了（这正是回滚的语义）")
    return 0


def do_check(config_io, secrets_mod, path: Path, as_json: bool = False) -> int:
    """只报告，不写任何文件。exit 1 = 需要迁移。"""
    disk = read_yaml(path)
    pending = summarize(disk, config_io)
    migrated = bool(disk.get(config_io.SECRETS_REF_KEY))

    if as_json:
        print(json.dumps({
            "config": str(path),
            "keystore": str(secrets_mod.secrets_path()),
            "pending_fields": pending,
            "migrated": migrated,
            "needs_migration": bool(pending),
        }, ensure_ascii=False, indent=2))
        return 1 if pending else 0

    if not pending:
        if migrated:
            print(f"✅ 已迁移：YAML 里无明文，带 {config_io.SECRETS_REF_KEY} 标记")
        else:
            print("✅ 无需迁移：配置里没有凭据")
        return 0

    print(f"需要迁移：{path}")
    print(f"  将清空的字段（{len(pending)}）：" + "、".join(pending))
    print(f"  将写入的密钥库：{secrets_mod.secrets_path()}")
    print(f"  将写入的标记：{config_io.SECRETS_REF_KEY} = {secrets_mod.SECRETS_REF}")
    bak = path.with_suffix(".yaml.bak")
    if bak.exists():
        print(f"  将刷新启动备份：{bak.name}（它当前也是明文）")
    if backend_running():
        print("  ⚠️  检测到后端正在 7413 端口运行 —— 建议关闭 LiMo 后再迁移")
    print("\n建议：如需要额外保险，先手动把 config.yaml 复制到库目录之外再执行。")
    print("回滚方式：python scripts/migrate-secrets.py --rollback")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="把 config.yaml 里的明文凭据迁移进 DPAPI 密钥库")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="只报告会改什么，不写任何文件")
    g.add_argument("--rollback", action="store_true", help="从密钥库还原凭据到 YAML")
    ap.add_argument("--json", action="store_true",
                    help="以 JSON 输出（隐含 --check；回滚是一次性动作，无机读待办）")
    args = ap.parse_args()

    if args.json and args.rollback:
        print("--json 只用于 --check", file=sys.stderr)
        return 2

    config_io, secrets_mod = _bootstrap()

    if not secrets_mod.is_available():
        print(f"✗ {secrets_mod.unavailable_reason()}", file=sys.stderr)
        print("  DPAPI 不可用时**不允许迁移** —— 抽出来的值无处安放，"
              "静默退回明文等于白做。", file=sys.stderr)
        return 1

    path = config_io.config_file_path()
    if not path.exists():
        print(f"配置文件不存在：{path}（无需迁移）")
        return 0

    if args.rollback:
        return do_rollback(config_io, secrets_mod, path)
    if args.check or args.json:
        return do_check(config_io, secrets_mod, path, as_json=args.json)
    return do_migrate(config_io, secrets_mod, path)


if __name__ == "__main__":
    # 退出码三态（同 check-secrets.py）：0 完成 / 1 需迁移或失败 / 2 脚本自身出错。
    import traceback
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        print("\n迁移**未能完成**（配置未被改动或已原子写入，不存在半写状态）—— 退出码 2",
              file=sys.stderr)
        sys.exit(2)
