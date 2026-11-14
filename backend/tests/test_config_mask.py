"""`GET /api/config` 脱敏 + `fetch.proxy` 成对回填。

**为什么单独立一个文件**：这条接口在「agent 走接口读库」的新叙事下是**入口**，
而它曾经把 `cloud.dashscope` / `cloud.alibaba` / `auth.*_parsed`（含明文 cookie 与
yt-dlp 可直接使用的 netscape 内容）/ `fetch.proxy`（含 `user:pass@`）原样返回 ——
接口本身成了泄漏通道。

⚠️ 每个哨兵值都设计成**能在响应体里直接 grep 到**，且对照实验先证明「旧实现真的
泄漏」（否则新实现的 PASS 可能只是假绿灯）。

⚠️ **含 tab 的哨兵对字符串匹配是盲的**（JSON 会把真实 tab 转义成 `\\t`，子串永不
命中 —— netscape 就是这么漏掉的），故另配一条**结构层面**的对照，它不依赖匹配。

跑法：`pytest tests/test_config_mask.py` 或 `python tests/test_config_mask.py`。
"""
import copy
import json
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.dependencies import get_config as dep_get_config, get_config_lock
from app.routers import config as cfg_router

#: 每个值都能在响应体里直接 grep 到（netscape 那条例外，见模块 docstring）
SEN = {
    "dashscope": "sk-dashscope-SENTINEL-9876",
    "alibaba": "LTAI-alibaba-SENTINEL-5432",
    "tavily": "tvly-SENTINEL-tavily-1111",
    "deepseek": "sk-deepseek-SENTINEL-5555",
    "proxypw": "proxy-SENTINEL-pw-2222",
    "cookie": "SESSDATA-SENTINEL-cookie-3333",
    "netscape": ".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tSENTINEL-netscape-4444",
}

BASE_CFG = {
    "llm": {"provider": "deepseek", "api_key": "sk-llm-SENTINEL-0000", "model": {}},
    "cloud": {"dashscope": SEN["dashscope"], "alibaba": SEN["alibaba"]},
    "fetch": {"proxy": f"http://user:{SEN['proxypw']}@127.0.0.1:7890"},
    "auth": {
        "bilibili": f"SESSDATA={SEN['cookie']}; bili_jct=jct-xyz",
        "youtube": "",
        "douyin": "",
        # ⚠️ 这是**历史落盘形态**（三写时代产物）。新代码不再产生它，但旧配置文件里
        #    还躺着 —— 接口必须仍能安全处理，不能因为「我们不再写它」就放行。
        "bilibili_parsed": {
            "platform": "bilibili",
            "cookies": {"SESSDATA": SEN["cookie"]},
            "netscape": "# Netscape HTTP Cookie File\n" + SEN["netscape"],
        },
    },
    "agent_tools": {"search": {
        "tavily_api_key": SEN["tavily"],
        "deepseek_api_key": SEN["deepseek"],
        "provider": "tavily",
    }},
    "scene_models": {"generate": 1},
    "data_dir": "C:/tmp/limo",
    "target_language": "zh-CN",
    "chat_context": {"budget_ratio": "auto"},
}


class _Env:
    def __init__(self, cfg, saved, client):
        self.cfg = cfg
        self.saved = saved
        self.client = client


@pytest.fixture
def env(monkeypatch):
    """最小 app + 依赖覆盖，**零副作用**（刻意不启动 `main.app` —— 那会跑 startup、
    建 Store、写用户的 `~/.limo`）。"""
    cfg = copy.deepcopy(BASE_CFG)
    saved: list[dict] = []
    monkeypatch.setattr(cfg_router, "_save_config",
                        lambda c: saved.append(copy.deepcopy(c)))
    # ⚠️ `POST /config` 会调 `configure_proxy` —— 它写的是**真实的** `os.environ`
    #   （以及模块级豁免表）。不挡住的话，一条 POST 就把 `HTTP_PROXY` 塞进 pytest
    #   进程，之后任何读环境的用例看到的都不是它以为的值，而红/绿取决于测试顺序。
    #   `configure_proxy` 体内按模块全局名找 `apply_proxy_env`，故挡这里就够。
    from app.core import net as _net
    monkeypatch.setattr(_net, "apply_proxy_env", lambda *a, **k: None)
    monkeypatch.setattr(_net, "_BYPASS_SUFFIXES", _net.DEFAULT_BYPASS_SUFFIXES)

    app = FastAPI()
    app.include_router(cfg_router.router)
    app.dependency_overrides[dep_get_config] = lambda: cfg
    app.dependency_overrides[get_config_lock] = lambda: threading.Lock()
    return _Env(cfg, saved, TestClient(app))


def _legacy_response(cfg: dict) -> dict:
    """修复前的 GET 返回体（照抄原实现）—— 用来证明新判据**有区分力**。"""
    return {
        "cloud": cfg.get("cloud", {}),
        "fetch": {"proxy": cfg.get("fetch", {}).get("proxy", "")},
        "auth": {
            "bilibili_parsed": cfg.get("auth", {}).get("bilibili_parsed"),
            "youtube_parsed": cfg.get("auth", {}).get("youtube_parsed"),
            "douyin_parsed": cfg.get("auth", {}).get("douyin_parsed"),
        },
    }


# ══ 对照实验：先证明「旧实现真的泄漏」══════════════════════════════

def test_legacy_impl_really_leaks():
    legacy = json.dumps(_legacy_response(copy.deepcopy(BASE_CFG)), ensure_ascii=False)
    leaked = [k for k, v in SEN.items() if v in legacy]
    assert len(leaked) >= 4, f"旧实现泄漏了 {leaked} —— 判据失去区分力"


def test_legacy_impl_leaks_netscape_structurally():
    """结构层面的对照（不依赖字符串匹配，故对含 tab 的哨兵也有效）。"""
    bp = _legacy_response(copy.deepcopy(BASE_CFG))["auth"]["bilibili_parsed"]
    assert "cookies" in bp and "netscape" in bp
    assert "SENTINEL-netscape-4444" in bp["netscape"]


# ══ 新实现 ═════════════════════════════════════════════════════════

def test_no_sentinel_in_response(env):
    r = env.client.get("/config")
    assert r.status_code == 200
    body = json.dumps(r.json(), ensure_ascii=False)
    still = [k for k, v in SEN.items() if v in body]
    assert not still, f"仍在响应里：{still}"


def test_cloud_masked_but_structure_kept(env):
    data = env.client.get("/config").json()
    assert "***" in data["cloud"]["dashscope"]
    assert "***" in data["cloud"]["alibaba"]
    assert set(data["cloud"]) == {"dashscope", "alibaba"}


def test_proxy_masks_password_keeps_host(env):
    """抹密码、留 host —— 前端要显示它、用户要认得出是哪个代理。"""
    data = env.client.get("/config").json()
    assert data["fetch"]["proxy"] == "http://user:***@127.0.0.1:7890"


def test_get_proxy_bypass_defaults_when_key_missing(env):
    """老配置没有 `fetch.proxy_bypass` 这个键 → 回落到默认表（不能返回 None，前端会崩）。"""
    assert "proxy_bypass" not in env.cfg["fetch"]
    rules = env.client.get("/config").json()["fetch"]["proxy_bypass"]
    assert isinstance(rules, list)
    assert "bilibili.com" in rules


def test_get_proxy_bypass_empty_list_stays_empty(env):
    """⚠️ `[]`（用户清空 = 全部走代理）与 `None`（没配过）是两件事。

    写成 `... or DEFAULT_BYPASS_SUFFIXES` 时这里会变成默认表 —— 用户清空了直连域名，
    重新打开设置页又看见一整排域名，再点保存就静默变回「默认豁免」。
    """
    env.cfg["fetch"]["proxy_bypass"] = []
    assert env.client.get("/config").json()["fetch"]["proxy_bypass"] == []


def test_get_proxy_bypass_returns_user_rules(env):
    env.cfg["fetch"]["proxy_bypass"] = ["example.cn"]
    assert env.client.get("/config").json()["fetch"]["proxy_bypass"] == ["example.cn"]


def test_get_proxy_bypass_treats_dirty_value_as_missing(env):
    """非 list/tuple 的脏值按「缺键」处理 —— 与启动时 `configure_proxy` 的回落同源。"""
    env.cfg["fetch"]["proxy_bypass"] = "bilibili.com"
    rules = env.client.get("/config").json()["fetch"]["proxy_bypass"]
    assert rules == list(cfg_router.DEFAULT_BYPASS_SUFFIXES)


def test_parsed_becomes_summary_without_secret_keys(env):
    bp = env.client.get("/config").json()["auth"]["bilibili_parsed"]
    assert bp is not None
    assert set(bp) == {"platform", "cookieCount"}, "摘要里不许再有 cookies / netscape"
    assert bp == {"platform": "bilibili", "cookieCount": 2}


def test_empty_platforms_are_none(env):
    data = env.client.get("/config").json()
    assert data["auth"]["youtube_parsed"] is None
    assert data["auth"]["douyin_parsed"] is None


def test_non_secret_fields_preserved(env):
    data = env.client.get("/config").json()
    assert data["target_language"] == "zh-CN"
    assert data["data_dir"] == "C:/tmp/limo"


def test_other_secrets_masked(env):
    """前端**依赖** `auth.bilibili` 非空（SettingsPage 用它判断「已配置」并回填输入框）
    —— 所以它必须是**脱敏后的非空串**，不能为了安全抹成空。"""
    data = env.client.get("/config").json()
    assert data["auth"]["bilibili"] and "***" in data["auth"]["bilibili"]
    assert "***" in data["agent_tools"]["search"]["tavily_api_key"]
    assert "***" in data["llm"]["api_key"]


def test_agent_tools_keys_masked(env):
    """`agent_tools.search` 里的每一把 Key 都脱敏 —— 此前只掩了 tavily，
    DeepSeek 联网 Key 明文回传，两个字段在设置页上长得一模一样。"""
    data = env.client.get("/config").json()
    search = data["agent_tools"]["search"]
    assert "***" in search["tavily_api_key"]
    assert "***" in search["deepseek_api_key"]
    assert search["provider"] == "tavily", "非敏感字段不能被顺手抹掉"


def test_agent_tools_new_secret_path_auto_masked(env, monkeypatch):
    """判据是「从 `SECRET_SCALARS` 派生」而不是「把 deepseek 也加进 if」——
    往元组里补一条新路径后，不碰路由代码就该被掩码。"""
    from app.core import config_io
    monkeypatch.setattr(
        config_io, "SECRET_SCALARS",
        config_io.SECRET_SCALARS + ("agent_tools.fetch.future_api_key",))
    env.cfg["agent_tools"]["fetch"] = {"future_api_key": "fk-SENTINEL-future-6666"}
    data = env.client.get("/config").json()
    assert "***" in data["agent_tools"]["fetch"]["future_api_key"]


def test_post_masked_agent_keys_keep_original(env):
    """成对判据的那一半：提交掩码值不能覆盖真实 Key。"""
    body = env.client.get("/config").json()["agent_tools"]
    env.client.post("/config", json={"agent_tools": body})
    assert env.cfg["agent_tools"]["search"]["tavily_api_key"] == SEN["tavily"]
    assert env.cfg["agent_tools"]["search"]["deepseek_api_key"] == SEN["deepseek"]


def test_post_new_agent_key_replaces(env):
    """回填不能把「用户真的换了 Key」也吃掉。"""
    env.client.post("/config", json={"agent_tools": {
        "search": {"deepseek_api_key": "sk-deepseek-NEW-7777"}}})
    assert env.cfg["agent_tools"]["search"]["deepseek_api_key"] == "sk-deepseek-NEW-7777"


def test_empty_auth_config(env):
    env.cfg["auth"] = {}
    data = env.client.get("/config").json()
    assert data["auth"]["bilibili_parsed"] is None
    assert data["auth"]["youtube_parsed"] is None
    assert data["auth"]["douyin_parsed"] is None


# ══ 成对性：GET 脱敏了，POST 必须回填（否则保存 = 丢密码）══════════

def test_post_restores_masked_proxy(env):
    r = env.client.post("/config", json={"fetch": {"proxy": "http://user:***@127.0.0.1:7890"}})
    assert r.status_code == 200
    assert env.cfg["fetch"]["proxy"] == BASE_CFG["fetch"]["proxy"], \
        "回填失败 —— 前端把显示用的 *** 存了回去，真实代理密码丢失"


def test_post_keeps_real_change(env):
    """回填不能把「用户真的改了」也吃掉。"""
    env.client.post("/config", json={"fetch": {"proxy": "http://newuser:newpw@10.0.0.1:8080"}})
    assert env.cfg["fetch"]["proxy"] == "http://newuser:newpw@10.0.0.1:8080"


def test_post_does_not_touch_process_env(env, monkeypatch):
    """隔离**自证**（不是靠「我 monkeypatch 了」这句直觉）：

    保存配置会调 `configure_proxy`，而它默认写 `os.environ`。fixture 若哪天被删掉
    那行挡板，这条会红 —— 那时现象是「跑完全量测试，进程里多了一组 HTTP_PROXY」，
    红/绿取决于测试顺序。
    """
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    env.client.post("/config", json={"fetch": {"proxy": "http://u:p@10.0.0.1:8080"}})
    assert "HTTP_PROXY" not in os.environ
    assert "HTTPS_PROXY" not in os.environ


def test_post_empty_bypass_clears_rules(env):
    """清空直连域名 → 存 `[]`（全部走代理），且回读仍是空的（与 GET 那几条成对）。"""
    r = env.client.post("/config", json={"fetch": {"proxy": "", "proxy_bypass": []}})
    assert r.status_code == 200
    assert env.cfg["fetch"]["proxy_bypass"] == []
    assert env.client.get("/config").json()["fetch"]["proxy_bypass"] == []


# ══ mask_proxy 边界 ════════════════════════════════════════════════

@pytest.mark.parametrize("raw,expected", [
    ("http://127.0.0.1:7890", "http://127.0.0.1:7890"),   # 无 @ → 原样
    ("", ""),                                              # 空串 → 原样
    ("http://user:***@h:1", "http://user:***@h:1"),        # 已脱敏 → **幂等**
    ("socks5://u:p@h:1080", "socks5://u:***@h:1080"),      # 协议无关
])
def test_mask_proxy(raw, expected):
    assert cfg_router.mask_proxy(raw) == expected


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--no-header", "-p", "no:cacheprovider"]))
