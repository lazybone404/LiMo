"""思考强度（reasoning_effort）决策 —— 全系统唯一真相。

**为什么单独成文件**：这个决策有四个调用点（流式双产物 / 流式单产物 / 非流式
单产物 / 非流式双产物），而此前写死的字面量正是「改一处漏一处」的现场
（generate.py 与 generator.py 各躺着一份 `"low"`）。
更关键的是「整理版恒低档」这条规则**必须在 UI 上明示**，而后端这边也必须只有
一处定义——散落的副本迟早漂移成「UI 说的是 A，实际跑的是 B」。

返回 None = **不传该参数**，交由服务端默认（DeepSeek 思考模式默认 high）。
"""

from app.core import logging as log
from app.services.doc_understand import DOC_DIRECT_TIMEOUT

# 服务端认的三档；其余值（含空串）一律视为「用户未表达偏好」
EFFORT_TIERS: tuple[str, ...] = ("low", "high", "max")

# ── 静默期分档 ──
# DeepSeek 思考模式下，服务端在思考阶段**完全不推流**（实测首 chunk 后 90s 零数据）。
# 单 chunk 等待超时必须容忍它，否则正常的长思考会被误判为挂起。
CHUNK_TIMEOUT_DEFAULT = 150.0
CHUNK_TIMEOUT_MAX = 180.0
# ⚠️ 这两个值必须严格小于生成总超时（_GEN_TIMEOUT=330 / _GEN_TIMEOUT_CHART=400）。
# 否则单 chunk 超时永远轮不到生效，总超时先到——分档就成了死代码。


def chunk_timeout_for(effort: str | None, doc_direct: bool = False) -> float:
    """单 chunk 静默期超时（秒）。

    `effort="max"`：思考更久，放宽到 180s。

    `doc_direct=True`（PDF 直传）：模型收到 file 块后要先把原件转成视觉表征
    才吐第一个 token，这段**完全静默**，且与思考档位是两个独立来源
    （实测 ≈10s/MB，14MB 上界对应 ~140s，远超 180s 的 max 档）。
    """
    base = CHUNK_TIMEOUT_MAX if effort == "max" else CHUNK_TIMEOUT_DEFAULT
    if doc_direct:
        # max() 而非直接 return：哪天 DOC_DIRECT_TIMEOUT 被调小，直传不该
        # 反而比普通档更早被判挂起——那是个「看起来更安全」的隐蔽回归。
        return max(base, DOC_DIRECT_TIMEOUT)
    return base


def effort_for(product: str, user_tier: str, supports_reasoning: bool) -> str | None:
    """按产物类型与用户档位决定实际下发的 reasoning_effort。

    | user_tier      | notes  | polished |
    |----------------|--------|----------|
    | ""（未指定）    | None   | "low"    |
    | "low"          | "low"  | "low"    |
    | "high"         | "high" | "low"    |
    | "max"          | "max"  | "max"    |

    - `""`（老客户端 / 未选择）时 notes 返回 None：等价于今天的服务端默认（high），
      零回归——前端默认档也是 'high'，两者一致。
    - **polished 恒为 "low"（除非 max）**：它是忠实整理任务而非创作任务，
      深度思考换不来质量，却要付 90s+ 的静默期。这条规则在前端 UI 上有对应文案
      （GenerateOptions 的档位说明），改动这里必须同步改文案。
    - `supports_reasoning=False`：该模型不认这个参数（qwen 等会忽略未知字段），
      一律返回 None 而不是硬塞——用户**显式**选过档位时额外告警，
      让「选了但没生效」可见（失败显式化）；未选择则静默，否则每次生成都刷警告。
    """
    if not supports_reasoning:
        if user_tier in EFFORT_TIERS:
            log.warning("模型不支持 reasoning_effort，档位设置未生效",
                        product=product, tier=user_tier)
        return None

    if product == "polished":
        # 整理版固定低档；只有用户明确要「都用最高档」时才例外
        return "max" if user_tier == "max" else "low"
    if user_tier in EFFORT_TIERS:
        return user_tier
    return None
