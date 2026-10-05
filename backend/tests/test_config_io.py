"""配置落盘形态（`core/config_io.py`）—— 三张成对表 / 类型守卫 / 往返幂等 / 自检。

**二期「凭据不落 `config.yaml`」的全部契约都在这里**。三张表各自管一件事，而每一张
的**两侧**（剥 ↔ 填、下沉 ↔ 跳过、类型 ↔ 读侧用法）都必须是同一份真相：

| 表 | 一侧 | 另一侧 | 对不上的症状 |
|---|---|---|---|
| `SECRET_SCALARS` / `SECRET_LISTS` | `extract_secrets`（抽走） | `inject_secrets`（填回） | 保存后 Key 消失 |
| `DERIVED_AUTH_SUFFIX` | `strip_derived`（剥） | `auto_parse`（重算） | 派生副本反复写回盘 |
| `fold_to_providers`（下沉） | 返回成功集 → `skip=` | 下沉失败**不跳过** | 同一把 key 存两份 / key 无声丢 |
| `_EMPTY_CONTAINERS` | 键名 | 类型 | **启动即崩**（`llm_models` 曾被写成 `[]`）|

⚠️ **每条断言都配了「若实现写错会怎样」**；`startup_check` 那条尤其如此 —— 它必须读
**磁盘**，读入参（内存 cfg，已被 `load_config` 回填过）会恒绿，而它正是要发现「盘上
还有明文」那个问题的。

跑法：`pytest tests/test_config_io.py` 或 `python tests/test_config_io.py`。
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import yaml

_TMP = tempfile.mkdtemp(prefix="limo_config_io_test_")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

# ⚠️ 两个 env 都要设，且必须在 import 之前 —— 否则 save_config 会写**真实**的
#    %LOCALAPPDATA%\\LiMo\\secrets.bin（2026-10-04 验证时踩过一次）。
_CONFIG = Path(_TMP) / "config.yaml"
os.environ["LIMO_CONFIG"] = str(_CONFIG)
os.environ["LIMO_SECRETS"] = str(Path(_TMP) / "secrets.bin")

from app.core import config_io, secrets as secrets_mod  # noqa: E402
from app.core.config import DEFAULT_CONFIG  # noqa: E402

KEY = "sk-roundtrip-AAAA1111BBBB2222"
COOKIE = "SESSDATA=cookie-1234567890; bili_jct=abcdef"
TAVILY = "tvly-dev-99887766554433221100"


@pytest.fixture(autouse=True)
def _clean():
    """每条测试都从「干净的盘 + 干净的库」开始 —— 这些断言大多在看**盘上残留**。"""
    _CONFIG.unlink(missing_ok=True)
    _CONFIG.with_suffix(".yaml.bak").unlink(missing_ok=True)
    secrets_mod.secrets_path().unlink(missing_ok=True)
    yield
    _CONFIG.unlink(missing_ok=True)
    _CONFIG.with_suffix(".yaml.bak").unlink(missing_ok=True)
    secrets_mod.secrets_path().unlink(missing_ok=True)


def _seed() -> dict:
    cfg = {
        "data_dir": str(_TMP),
        "log_level": "INFO",
        "llm": {"provider": "dashscope", "model": "qwen-max", "api_key": KEY},
        "providers": [
            {"id": "dashscope", "api_key": KEY, "base_url": "https://example.invalid/v1"},
            {"id": "openai", "api_key": ""},
        ],
        "engine_providers": [{"id": "dashscope", "api_key": KEY}],
        "cloud": {"dashscope": KEY, "alibaba": ""},
        "auth": {"bilibili": COOKIE, "youtube": "", "douyin": ""},
        "agent_tools": {"search": {"tavily_api_key": TAVILY}},
    }
    return cfg


def _disk() -> dict:
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) or {}


# ══ 类型守卫：写错的症状是「启动即崩」 ═══════════════════════════════

def test_empty_containers_match_default_config():
    """表里的类型必须等于 `DEFAULT_CONFIG` 的**真实**类型。

    ⚠️ 这不是形式主义：`llm_models` 曾被归进 list 组（`None → []`），而
    `_sync_llm_model_caps` / `model_resolver` 都拿它当 dict 用 `.get()` —— 配置里
    该键缺失时，`save_config` 会写出一个**下次启动必然 AttributeError** 的 config.yaml。
    改动 `_EMPTY_CONTAINERS` 而忘了另一侧，这条会立刻红。
    """
    for key, kind in config_io._EMPTY_CONTAINERS.items():
        if key in DEFAULT_CONFIG:
            assert isinstance(DEFAULT_CONFIG[key], kind), (
                f"{key}: DEFAULT_CONFIG 里是 {type(DEFAULT_CONFIG[key]).__name__}，"
                f"表里写的是 {kind.__name__}")


def test_missing_container_repaired_with_correct_type():
    cfg = {"llm": {"provider": "dashscope", "api_key": KEY}}   # 容器键全缺（见 _EMPTY_CONTAINERS）
    config_io.save_config(cfg)
    disk = _disk()
    assert isinstance(disk["llm_models"], dict)
    assert isinstance(disk["engine_providers"], list)
    assert isinstance(disk["engine_models"], list)
    assert isinstance(disk["engine_bindings"], dict)


def test_repaired_config_can_be_loaded():
    """上面那条的**结果**：修好之后 `load_config` 必须真能读（核到调用点）。"""
    from app.core.config import load_config
    _CONFIG.write_text(yaml.dump({"data_dir": str(_TMP),
                                  "llm": {"provider": "dashscope", "api_key": KEY}}),
                       encoding="utf-8")
    config_io.save_config(yaml.safe_load(_CONFIG.read_text(encoding="utf-8")))
    assert isinstance(load_config().get("llm_models"), dict)


# ══ ① 敏感字段：抽 ↔ 填 ════════════════════════════════════════════

def test_save_strips_plaintext_and_writes_ref():
    config_io.save_config(_seed())
    disk = _disk()
    text = _CONFIG.read_text(encoding="utf-8")
    assert KEY not in text and COOKIE not in text and TAVILY not in text
    assert disk[config_io.SECRETS_REF_KEY] == secrets_mod.SECRETS_REF
    # 键结构保留（用户打开 YAML 看得见「这里有个 api_key」）
    assert disk["llm"]["api_key"] == ""
    assert disk["auth"]["bilibili"] == ""
    assert disk["providers"][0]["api_key"] == ""


def test_inject_restores_by_provider_id():
    config_io.save_config(_seed())
    cfg = _disk()
    n = config_io.inject_secrets(cfg, secrets_mod.load_secrets())
    assert n > 0
    assert cfg["providers"][0]["api_key"] == KEY
    assert cfg["engine_providers"][0]["api_key"] == KEY
    assert cfg["auth"]["bilibili"] == COOKIE
    # ⚠️ `llm.api_key` / `cloud.dashscope` **刻意不在这里断言** —— 它们已被
    #    `fold_to_providers` 下沉，密钥库里没有这两条（那正是去重的意义）；旧字段由
    #    `load_config` 的 `_hydrate_provider_keys` 从 provider 列表镜像回来
    #    （见 test_roundtrip_fidelity）。
    assert "llm.api_key" not in (secrets_mod.load_secrets().get("fields") or {})


def test_inject_matches_by_id_not_position():
    """按 **id** 索引而非顺序 —— 用户重排 providers 不该串 key。"""
    cfg = {"providers": [{"id": "a", "api_key": ""}, {"id": "b", "api_key": ""}]}
    config_io.inject_secrets(cfg, {"providers": {"b": "sk-b", "a": "sk-a"}})
    assert cfg["providers"][0]["api_key"] == "sk-a"
    assert cfg["providers"][1]["api_key"] == "sk-b"


def test_clearing_all_keys_removes_keystore_and_ref():
    """用户把 Key 全清空 ⇒ 库删除 + ref 撤掉。

    否则下次 load 会去解一个不存在的库，且 YAML 里躺着一条没意义的标记。
    ⚠️ 这条的前提是**入参是回填过的内存配置**（见 `save_config` 里的注释）。
    """
    config_io.save_config(_seed())
    assert secrets_mod.secrets_path().exists()

    cfg = _disk()
    config_io.inject_secrets(cfg, secrets_mod.load_secrets())
    for path in config_io.SECRET_SCALARS:
        config_io._poke(cfg, path, "")
    for key, _id, val_field in config_io.SECRET_LISTS:
        for item in cfg.get(key) or []:
            item[val_field] = ""
    config_io.save_config(cfg)

    assert not secrets_mod.secrets_path().exists()
    assert config_io.SECRETS_REF_KEY not in _disk()


# ══ ② 派生字段 ═════════════════════════════════════════════════════

def test_strip_derived_removes_parsed_copies():
    """历史三写时代的副本（`_parsed.cookies` / `.netscape`）必须被剥掉。

    netscape 那份是 **yt-dlp 可直接使用的 cookie 文件内容** —— 同一份 cookie 存三份里
    最敏感的形态。
    """
    cfg = {"auth": {"bilibili": COOKIE,
                    "bilibili_parsed": {"cookies": {"SESSDATA": COOKIE},
                                        "netscape": "# Netscape...\n" + COOKIE}}}
    removed = config_io.strip_derived(cfg)
    assert removed == ["auth.bilibili_parsed"]
    assert "bilibili_parsed" not in cfg["auth"]
    assert cfg["auth"]["bilibili"] == COOKIE        # 原始串**不动**（它是真相源）


# ══ ③ 可下沉字段 ═══════════════════════════════════════════════════

def test_fold_folds_and_reports():
    cfg = _seed()
    folded = config_io.fold_to_providers(cfg)
    assert folded == frozenset({"llm.api_key", "cloud.dashscope"})


def test_fold_skips_when_target_missing():
    """没有对应 provider 条目 ⇒ **下沉不成功** ⇒ 不进返回集。

    这是「跳过抽取 ⇔ 已确认下沉成功」的边界：若它照样返回成功集，
    `extract_secrets(skip=...)` 会跳过一个**根本没被下沉**的值 —— 用户的 key 无声消失。
    """
    cfg = _seed()
    cfg["llm"]["provider"] = "no-such-provider"             # 目标条目不存在 → 无处可沉
    folded = config_io.fold_to_providers(cfg)
    assert "llm.api_key" not in folded, "下沉失败却报成功 → 该 key 会被 skip 掉而无声消失"
    assert "cloud.dashscope" in folded                      # 这条能下沉（engine_providers 在）
    assert cfg["llm"]["api_key"] == KEY                     # 值**原地未动**（它仍是唯一存放处）


def test_fold_skips_masked_values():
    """脱敏值（含 `***`）不得被当成真值下沉 —— 那会把 `***` 写进真相源。"""
    cfg = _seed()
    cfg["llm"]["api_key"] = "sk-****AAAA"
    folded = config_io.fold_to_providers(cfg)
    assert "llm.api_key" not in folded
    assert "***" not in str(cfg["providers"][0]["api_key"])


def test_folded_secret_is_stored_once():
    """下沉成功后，密钥库里**只有一份**（provider 条目那份）。

    这是二期「先去重」的落点：加密多份同一 key 仍暴露结构，去重才是根因修复。
    """
    config_io.save_config(_seed())
    data = secrets_mod.load_secrets()
    assert "llm.api_key" not in data["fields"]
    assert "cloud.dashscope" not in data["fields"]
    assert data["providers"] == {"dashscope": KEY}
    assert data["engine_providers"] == {"dashscope": KEY}
    assert data["fields"]["auth.bilibili"] == COOKIE
    assert data["fields"]["agent_tools.search.tavily_api_key"] == TAVILY


# ══ 往返与幂等 ═════════════════════════════════════════════════════

def test_roundtrip_fidelity():
    """load → 内存 → save 之后，用户能感知的东西一个不少。"""
    from app.core.config import load_config
    config_io.save_config(_seed())
    loaded = load_config()
    assert loaded["llm"]["api_key"] == KEY
    assert loaded["auth"]["bilibili"] == COOKIE
    assert loaded["agent_tools"]["search"]["tavily_api_key"] == TAVILY
    assert loaded["cloud"]["dashscope"] == KEY
    assert loaded["providers"][0]["api_key"] == KEY


def test_save_is_idempotent_after_load():
    """稳态后连续三轮写盘**字节等价**。

    ⚠️ 判据是「load 之后的稳态」，不是「拿裸 DEFAULT_CONFIG 存出的盘」—— 后者必然
    不同，因为 `load_config` 会补全 provider 列表等（判据跑到了，但它看的输入不是
    它以为的那个）。
    """
    from app.core.config import load_config
    config_io.save_config(_seed())
    cfg = load_config()
    for _ in range(3):
        config_io.save_config(cfg)
    first = _CONFIG.read_bytes()
    for _ in range(3):
        config_io.save_config(load_config())
    assert _CONFIG.read_bytes() == first


# ══ 启动自检 ═══════════════════════════════════════════════════════

def test_startup_check_reads_disk_not_memory():
    """⚠️ **本文件最重要的一条**：`startup_check` 必须读**磁盘**。

    它拿到的入参是 `load_config` 回填过的内存 cfg —— 那份**处处非空**。若实现改成
    「检查入参」，这条判据会恒绿，而它存在的全部意义就是发现「盘上还有明文」。

    两条断言合起来才钉得住：同一份内存 cfg，盘干净 → 空；盘有明文 → 非空。
    """
    memory_cfg = _seed()                                   # 已回填，处处有值
    assert config_io.startup_check(memory_cfg) == [], "盘上干净时不该报"

    _CONFIG.write_text(yaml.dump(_seed(), allow_unicode=True), encoding="utf-8")
    problems = config_io.startup_check(memory_cfg)
    assert problems, "盘上有明文却没报 —— 判据读的可能是内存 cfg（恒绿）"
    assert any("config.yaml" in p for p in problems)


def test_startup_check_covers_bak():
    """`.bak` 是**迁移前**的副本，会带着明文再活一个启动周期 —— 一并查。"""
    config_io.save_config(_seed())                          # config.yaml 干净
    _CONFIG.with_suffix(".yaml.bak").write_text(yaml.dump(_seed(), allow_unicode=True),
                                                encoding="utf-8")
    problems = config_io.startup_check(_seed())
    assert any("config.yaml.bak" in p for p in problems)


def test_startup_check_clean_after_migration():
    config_io.save_config(_seed())
    _CONFIG.with_suffix(".yaml.bak").unlink(missing_ok=True)
    assert config_io.startup_check(_seed()) == []


# ══ config 文件路径的唯一入口 ═══════════════════════════════════════

def test_config_file_path_prefers_env():
    """env 覆盖优先于 `cfg["data_dir"]` —— 否则「读 A 文件、写 B 文件」，
    两边看起来都对，只有用户会发现设置莫名回滚。"""
    assert config_io.config_file_path({"data_dir": "D:/elsewhere"}) == _CONFIG
    assert config_io.config_file_path() == _CONFIG


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--no-header", "-p", "no:cacheprovider"]))
