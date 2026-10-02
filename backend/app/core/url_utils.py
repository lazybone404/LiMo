"""厂商 API 地址归一化 — 全系统唯一入口。

本模块存在的理由：同一个「把 provider 的 base_url 拼成 /models、/chat/completions
可用地址」的判据，曾在 **3 处** 各自实现（`routers/model.py` 的 `test_connection`
与 `refresh_models`、`routers/config.py` 的 `/llm/models`）。这是项目铁律
「**成对逻辑必须同源**」的典型违反形态——改一处忘一处不会报错，只会让不同入口
对同一个 provider 得出不同结论（「测试连接」说通、「刷新模型」拉不到）。
"""

import re

# 「路径里已经带版本段」的判据 —— /v1、/v1beta、/v1alpha、/v4、/v2 …
# 刻意不加 re.I：URL 路径大小写敏感，各家的版本段一律小写；加 I 只会让 `/VIP/v1` 之类误命中。
_VERSION_SEG_RE = re.compile(r"/v\d+[a-z]*(?:/|$)")


def api_root(base_url: str | None) -> str:
    """把 provider 的 base_url 归一为「可直接拼 `/models`、`/chat/completions` 的根」。

    **判据是「路径是否已含版本段」，不是「是否以 /v1 结尾」**（2026-10-02 订正）。
    旧判据 `if not url.endswith("/v1"): url += "/v1"` 对多数 provider 成立，
    但它**不是普适规则**——各家的端点路径并不统一。已核出的两处反例：

      · `https://generativelanguage.googleapis.com/v1beta/openai`（Google 兼容层）
        旧：不以 `/v1` 结尾 → 拼成 `.../v1beta/openai/v1/models` → **404**
        新：`/v1beta/` 命中 → 原样 → `.../v1beta/openai/models` → **正确**
      · `https://open.bigmodel.cn/api/paas/v4`（智谱）
        旧：拼成 `.../api/paas/v4/v1/models`，官方是 `.../v4/models`
        新：`/v4` 命中 → 原样 → **顺带修复**
        ⚠️ 智谱**两条路径都返回 401**（路由宽松、不报 404）→ **状态码区分不了对错**，
           这类问题只能靠官方文档裁决，别指望用探测兜住。

    其余 provider 的行为**逐值不变**（已逐个验算）：

      | base_url | 旧判据 | 新判据 | 结果 |
      |---|---|---|---|
      | `https://api.deepseek.com` | 拼 `/v1` | 拼 `/v1` | 同 |
      | `https://api.anthropic.com` | 拼 `/v1` | 拼 `/v1` | 同 |
      | `https://api.openai.com/v1` | 不拼 | 不拼 | 同 |
      | `.../compatible-mode/v1`（dashscope） | 不拼 | 不拼 | 同 |
      | `https://api.moonshot.cn/v1` | 不拼 | 不拼 | 同 |
      | `https://opencode.ai/zen/go/v1` | 不拼 | 不拼 | 同 |
      | 用户自配 `http://localhost:8000` | 拼 `/v1` | 拼 `/v1` | 同 |

    唯一会改变行为的是「路径中已含版本段但**不**以 `/v1` 结尾」的情形——那正是
    google / zhipu 的情况，两处都是修正而非回归。

    ⚠️ **本函数只管路径，不管鉴权**。全链统一用 `Authorization: Bearer`——Google 兼容层
    接受 Bearer（这也正是本项目选兼容层而非原生 API 的原因），故不需要任何按 provider
    的特判分支。旧注释称「Google 鉴权走 `?key=`」，那是**原生 API** 的性质，已作废。
    """
    url = (base_url or "").rstrip("/")
    if _VERSION_SEG_RE.search(url):
        return url
    return url + "/v1"
