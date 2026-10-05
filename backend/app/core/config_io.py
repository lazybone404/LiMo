"""
配置持久化 — 统一的 config.yaml 读写。

**落盘形态的三张表就在这里**（敏感字段 / 派生字段 / 可下沉字段），它们是成对逻辑的
唯一真相源：`save_config` 按它们剥、`load_config` 按它们填（见 `core/config.py`）。
任何一处新增字段必须同时想到另一侧，否则症状是「保存后 Key 没了」或「配置里躺着明文」
—— **两种都不会抛错**。

凭据不落 `config.yaml`：抽出来送 `core/secrets.py` 的 DPAPI 密钥库
（`%LOCALAPPDATA%\\LiMo\\secrets.bin`），YAML 里只留一个 `secrets_ref` 标记。
"""

import copy
import logging
import os
from pathlib import Path
from typing import Any

from app.core import secrets as secrets_mod

logger = logging.getLogger(__name__)

#: config.yaml 里替代真实凭据的引用键。值取自 `secrets.SECRETS_REF`。
SECRETS_REF_KEY = "secrets_ref"


# ══════════════════════════════════════════════════════════════════════════
# ① 敏感字段 —— 值必须离开 config.yaml
# ══════════════════════════════════════════════════════════════════════════

#: 点分路径；值非空才抽取，落盘时**清空但保留键**（用户打开 YAML 看得见有这些字段）。
SECRET_SCALARS: tuple[str, ...] = (
    "llm.api_key",
    "cloud.dashscope",
    "cloud.alibaba",
    "auth.bilibili",
    "auth.youtube",
    "auth.douyin",
    "agent_tools.search.tavily_api_key",
)

#: 列表里的敏感字段 —— `(容器键, 条目 id 字段, 值字段)`。按 id 索引，顺序无关。
SECRET_LISTS: tuple[tuple[str, str, str], ...] = (
    ("providers", "id", "api_key"),
    ("engine_providers", "id", "api_key"),
)


# ══════════════════════════════════════════════════════════════════════════
# ② 派生字段 —— 能重算，落盘无价值且会与真相源漂移
# ══════════════════════════════════════════════════════════════════════════

#: `auth.<platform>_parsed` 由 `auto_parse()` 从 `auth.<platform>` 确定性派生。
DERIVED_AUTH_SUFFIX = "_parsed"


#: 顶层容器键 → 它的空值形态。`save_config` 拿它补 None。
#:
#: ⚠️ **类型必须与读侧一致**，写错的表现是「启动即崩」而非保存报错：`llm_models` 曾被
#: 归进 list 组（`None → []`），而 `_sync_llm_model_caps` / `model_resolver` 都拿它当
#: dict 用 `.get()` → 配置里该键缺失时写出 `[]`，**下一次启动 `AttributeError`**。
#: 绑在一张表里就不会再出现「键名在这组、类型在那组」。
_EMPTY_CONTAINERS: dict[str, type] = {
    "llm_models": dict,        # ← dict，不是 list
    "engine_providers": list,
    "engine_models": list,
    "engine_bindings": dict,
}


def config_file_path(cfg: dict | None = None) -> Path:
    """`config.yaml` 的落点 —— **唯一入口**。

    ⚠️ 这个拼法此前在 `save_config` / `load_config` / 自检脚本里**各有一份**
    （环境变量覆盖、data_dir 兜底各写各的）。任何一处漏掉 env 覆盖，症状就是
    「读 A 文件、写 B 文件」—— 两边看起来都对，只有用户会发现设置莫名回滚。
    """
    env = os.environ.get("LIMO_CONFIG") or os.environ.get("MYBIGWORK_CONFIG")
    if env:
        return Path(env)
    data_dir = (cfg or {}).get("data_dir") or str(Path.home() / ".limo")
    return Path(data_dir) / "config.yaml"


def _dig(cfg: dict, path: str) -> Any:
    cur: Any = cfg
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _poke(cfg: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = cfg
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def strip_derived(cfg: dict) -> list[str]:
    """剥离派生字段（就地）。返回被剥离的路径，供日志与自检。

    ⚠️ 历史版本把 cookie **写三份**（`auth.<p>` 原始串 + `_parsed.cookies` +
    `_parsed.netscape`，而 netscape 就是 yt-dlp 可直接使用的 cookie 文件内容）。
    那些副本会经 `_deep_merge` 回到内存，不主动清就一直被写回去。
    读侧不受影响：`GET /api/config` 已改为**实时派生摘要**（`_summarize_parsed`）。
    """
    removed: list[str] = []
    auth = cfg.get("auth")
    if isinstance(auth, dict):
        for key in [k for k in auth if isinstance(k, str) and k.endswith(DERIVED_AUTH_SUFFIX)]:
            auth.pop(key, None)
            removed.append(f"auth.{key}")
    return removed


# ══════════════════════════════════════════════════════════════════════════
# ③ 可下沉字段 —— 同一份秘密的第二个存放处，落盘前折进真相源
# ══════════════════════════════════════════════════════════════════════════

def fold_to_providers(cfg: dict) -> frozenset[str]:
    """把 `llm.api_key` / `cloud.dashscope` 下沉进 provider 列表（真相源）。

    这两个是**旧版字段**，但仍有读取点（`resolve_model` / `capabilities` / `engines`
    的 key 探测）；而 `routers/model.py` 保存厂商 Key 时**两个地方都写**
    （`llm.api_key` ← 厂商 Key、`cloud.dashscope` ← 引擎 Key）。
    ⇒ 同一把 key 存两处，用户改了一处、另一处还是旧的 —— 迟早漂移。

    做法：落盘前把值**下沉**到 provider 条目，返回**成功下沉的字段路径**；
    `extract_secrets(skip=...)` 据此跳过它们，密钥库里就只有一份密文。

    ⚠️ **下沉不成功就不跳过**（目标条目不存在、或源值本身是脱敏值）—— 那种情况下
    `llm.api_key` 是这枚 key 的**唯一**存放处，跳过抽取 = 用户的 key 无声消失。
    「跳过」与「下沉成功」必须严格成对。
    """
    folded: set[str] = set()

    llm = cfg.get("llm")
    if isinstance(llm, dict) and llm.get("api_key") and not is_masked(llm["api_key"]):
        pid = llm.get("provider") or ""
        for prov in cfg.get("providers") or []:
            if isinstance(prov, dict) and pid and prov.get("id") == pid:
                prov["api_key"] = llm["api_key"]
                folded.add("llm.api_key")
                break

    cloud = cfg.get("cloud")
    if isinstance(cloud, dict) and cloud.get("dashscope") and not is_masked(cloud["dashscope"]):
        for ep in cfg.get("engine_providers") or []:
            if isinstance(ep, dict) and ep.get("id") == "dashscope":
                ep["api_key"] = cloud["dashscope"]
                folded.add("cloud.dashscope")
                break

    return frozenset(folded)


def extract_secrets(cfg: dict, *, skip: frozenset[str] = frozenset()) -> dict:
    """抽出全部敏感字段（供写入 DPAPI 密钥库）。

    `skip` 里的点分路径**不进密钥库** —— 那些值已由 `fold_to_providers` 下沉进
    真相源，再存一份就是重复密文（同一把 key 在库里躺两份）。
    """
    out: dict[str, Any] = {"v": 1, "fields": {}, "providers": {}, "engine_providers": {}}
    for path in SECRET_SCALARS:
        if path in skip:
            continue
        val = _dig(cfg, path)
        if val:
            out["fields"][path] = val
    for key, id_field, val_field in SECRET_LISTS:
        bucket = out[key]
        for item in cfg.get(key) or []:
            if isinstance(item, dict) and item.get(id_field) and item.get(val_field):
                bucket[str(item[id_field])] = item[val_field]
    return out


def has_secrets(data: dict) -> bool:
    """密钥库里是否真有东西（空库不该写文件、也不该留 `secrets_ref`）。"""
    return bool(data.get("fields") or data.get("providers") or data.get("engine_providers"))


def inject_secrets(cfg: dict, data: dict) -> int:
    """把密钥库里的值填回配置（就地），返回填回条数。

    ⚠️ 与 `extract_secrets` **成对**：少了这一半，`save_config` 清空过的字段
    就再也没人填回来 —— 表现是「重启后所有 Key 消失」。
    """
    n = 0
    for path, val in (data.get("fields") or {}).items():
        _poke(cfg, path, val)
        n += 1
    for key, id_field, val_field in SECRET_LISTS:
        bucket = data.get(key) or {}
        for item in cfg.get(key) or []:
            if isinstance(item, dict) and item.get(id_field) and str(item[id_field]) in bucket:
                item[val_field] = bucket[str(item[id_field])]
                n += 1
    return n


def strip_secret_values(cfg: dict) -> None:
    """把已抽走的敏感字段在**落盘副本**上清空（就地，保留键结构）。

    保留键（空串）而不是 `pop`：用户打开 config.yaml 看得见「这里有 `llm.api_key`」，
    不至于以为字段凭空消失；读侧本来就用 `.get(path, "")`，空串与缺失等价。
    """
    for path in SECRET_SCALARS:
        if _dig(cfg, path):
            _poke(cfg, path, "")
    for key, _id_field, val_field in SECRET_LISTS:
        for item in cfg.get(key) or []:
            if isinstance(item, dict) and item.get(val_field):
                item[val_field] = ""


def write_yaml_atomic(cfg: dict, path: Path | str) -> None:
    """把 `cfg` 原样写成 YAML（原子：先写 `.tmp` 再 `os.replace`）。

    ⚠️ **不抽凭据、不做落盘归一** —— 它只是「写一份 YAML」这一个动作的实现，写什么
    由调用方决定：`save_config` 写的是处理过的副本；`migrate-secrets --rollback`
    写的是**故意含明文**的还原形态。dump 参数与替换语义只此一份，两处各写各的
    必然在某个参数上分家（`allow_unicode` 掉了就是满屏 `\\uXXXX`）。
    """
    import yaml

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = str(path) + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            logger.warning("Failed to clean up tmp config file", exc_info=True)
        raise


def save_config(cfg: dict) -> None:
    """写回 YAML 文件（原子写入：先写临时文件再替换，防止中断损坏）

    防御性校验：拒绝写入明显损坏的配置（关键字段为 None/空）。

    ⚠️ **写下去的是一份经过处理的副本**，不是 `cfg` 本身：入参是运行期的内存配置，
    `resolve_model` / `capabilities` / `engines` 的 key 探测都指着它，就地清空 = 当场失效。
    """
    config_path = str(config_file_path(cfg))

    # ── 防御性校验：关键字段不得被意外清空 ──
    llm = cfg.get("llm")
    if llm is None:
        logger.error("save_config 拒绝写入：cfg.llm 为 None，可能存在内存污染。跳过保存。")
        return
    if not isinstance(llm, dict):
        logger.error("save_config 拒绝写入：cfg.llm 类型异常 (%s)，跳过保存。", type(llm).__name__)
        return

    # 顶层容器键不得为 None —— 空值形态与**类型绑定在同一张表里**
    for key, kind in _EMPTY_CONTAINERS.items():
        if cfg.get(key) is None:
            logger.warning("save_config：cfg.%s 为 None，已自动修复为 %s", key, kind())
            cfg[key] = kind()

    # ── 落盘形态归一：折叠与剥派生作用于 **cfg 本身**，凭据抽取只在副本上 ──
    # 前两步为什么改内存：内存里若留着「同一份秘密的另一个副本」，`GET /providers`
    # 就会显示旧 key（设置页读的正是那一份）；派生副本同理，留着只会被反复写回盘。
    # 第三步必须在副本上：读取点指着内存那份真值。
    folded = fold_to_providers(cfg)
    for path in strip_derived(cfg):
        logger.debug("save_config: 剥离派生字段 %s", path)

    disk = copy.deepcopy(cfg)
    secrets_data = extract_secrets(disk, skip=folded)
    if has_secrets(secrets_data):
        # ⚠️ 失败**显式抛**（DPAPI 不可用等），不静默退回明文落盘 ——
        # 静默退回等于二期白做，且用户从 config.yaml 上看不出任何异常。
        secrets_mod.save_secrets(secrets_data)
        strip_secret_values(disk)
        disk[SECRETS_REF_KEY] = secrets_mod.SECRETS_REF
    elif disk.get(SECRETS_REF_KEY):
        # 用户把 Key 全清空了 —— 密钥库随之删除，引用也该一起撤掉
        # （否则下次 load 会去解一个不存在的库，且 YAML 里躺着一条没意义的标记）
        #
        # ⚠️ **这个分支的前提是「入参是回填过的内存配置」**：它无法区分「用户清空了
        #    凭据」与「调用方直接把磁盘形态的 YAML 喂了进来」（后者字段本来就是空的，
        #    因为值在密钥库里）。拿磁盘 YAML 直调 save_config ⇒ **密钥库被无声删除**，
        #    且 secrets_ref 一并被撤（= 凭据彻底失联）。迁移脚本为此必须先把密钥库
        #    的值 inject 回来再 save（见 scripts/migrate-secrets.py 的幂等说明）。
        #    新增 save_config 的调用点前，先问一句：这个 cfg 是 load 过的吗？
        secrets_mod.save_secrets({})
        disk.pop(SECRETS_REF_KEY, None)

    write_yaml_atomic(disk, config_path)


def startup_check(cfg: dict) -> list[str]:
    """启动期快速自检 —— 只做**不遍历磁盘**的那部分，返回人类可读的问题（空 = 干净）。

    ⚠️ 与 `scripts/check-secrets.py --check` 是**分工，不是重复**：那边还会遍历数据目录
    比对内容（能发现「凭据被写进了日志」这类泄漏），代价是扫一遍整个库。启动路径上跑
    全量会让冷启动等一次目录遍历，而它每天给出的结论都一样。

    查两件事：
      ① DPAPI 能不能用 —— 不能用则凭据根本无法加密落盘；
      ② `config.yaml` / `.bak` 的**磁盘原貌**里还有没有非空的敏感字段（= 尚未迁移）。

    ⚠️ 第②条必须读**磁盘**，不能读传进来的 `cfg` —— 它已被 `load_config` 回填过，
    处处非空，拿它判等于恒绿（「判据跑到了，但看的不是它以为的输入」，铁律 #39）。
    `.bak` 一并查：迁移那一刻它是**迁移前**的副本，会带着明文再活一个启动周期。
    """
    import yaml

    problems: list[str] = []

    ok, why = secrets_mod.selftest()
    if not ok:
        problems.append(f"DPAPI 不可用（{why}）—— 凭据无法加密保存")

    path = config_file_path(cfg)
    for target, label in ((path, "config.yaml"),
                          (path.with_suffix(".yaml.bak"), "config.yaml.bak")):
        try:
            disk = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(disk, dict):
            continue
        plain = [p for p in SECRET_SCALARS if _dig(disk, p)]
        for key, id_field, val_field in SECRET_LISTS:
            plain += [f"{key}[{item.get(id_field)}].{val_field}"
                      for item in disk.get(key) or []
                      if isinstance(item, dict) and item.get(val_field)]
        if plain:
            problems.append(f"{label} 里仍有明文凭据：" + "、".join(plain))
    return problems


def mask_sensitive(value: str) -> str:
    """脱敏：API Key 中间位替换为 ***"""
    if not value:
        return ""
    if len(value) > 8:
        return value[:4] + "***" + value[-4:]
    return "***"


def is_masked(value: str) -> bool:
    """检测是否为脱敏占位值（前端未修改 key，发回了 ***）"""
    return bool(value) and "***" in str(value)


def clean_api_key(value: str) -> str:
    """清洗 API Key：去首尾空白、去 HTML 标签包裹、去引号包裹。

    防止用户从网页复制 Key 时带入了 HTML 标签或多余的引号/空格。
    """
    import re
    v = value.strip().strip("'\"")  # 去首尾空白+引号
    # 检测被 HTML 标签包裹的情况：<div>sk-xxx</div> → sk-xxx
    m = re.search(r'>([^<]+)<', v)
    if m:
        inner = m.group(1).strip().strip("'\"")
        if len(inner) > 4:
            return inner
    return v


def merge_sensitive(new_val: str, old_val: str) -> str:
    """若前端传了脱敏值（含***），保留旧值；否则用新值（自动清洗）"""
    if new_val and is_masked(new_val) and old_val:
        return old_val
    return clean_api_key(new_val)
