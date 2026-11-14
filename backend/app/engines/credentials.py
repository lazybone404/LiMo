"""引擎凭据注入 —— 「调用时从配置取 Key / base_url 补到引擎副本上」的唯一入口。

**为什么需要它**：引擎在 `init_engines(config)` 里注册成**单例**，Key 是那一刻读的。
用户在设置页填的 Key 要等下次启动才生效 —— 而界面上显示的是「已配置」。
所以每次调用前都要从当前配置重新取一遍。

⚠️ **返回副本，绝不改单例**。`adapters/video.py` 曾经的写法是
`if not engine.api_key: engine.api_key = ...`，直接写注册表里的那个对象：第一次注入
之后，用户换了 Key 也不会再生效（`not engine.api_key` 已是 False），要重启才认，
期间每次转写都 401 而设置页显示的是新 Key。副本还有个附带好处：两个并发调用各拿各的
凭据，不会互相覆盖。

⚠️ 三个消费点必须同时用这一份：`adapters/document.py`（扫描件 OCR）、
`adapters/video.py`（ASR）、`routers/process.py`（手动 OCR）。各写一份的表现是
「某个入口填了 Key 能用、另一个入口 401」，而两个入口在设置页上指向同一个厂商。
"""

import copy
import logging

from app.core.deepseek_api import deepseek_api_key
from app.core.protocols import Engine

logger = logging.getLogger(__name__)


def provider_credentials(config: dict, provider_id: str,
                         current_key: str = "", current_url: str = "") -> tuple[str, str]:
    """解析某厂商的 `(api_key, base_url)`。

    顺序：引擎实例上已有的值 → `engine_providers[id]` → 厂商专属回退
    （DeepSeek 走 `core/deepseek_api.py` 的四级链；DashScope 额外认 `cloud.*`）。
    只认 `id`，不按 `base_url` 猜身份。
    """
    cfg = config or {}
    api_key = (current_key or "").strip()
    base_url = (current_url or "").strip()

    for ep in cfg.get("engine_providers") or []:
        if isinstance(ep, dict) and ep.get("id") == provider_id:
            api_key = api_key or str(ep.get("api_key") or "").strip()
            base_url = base_url or str(ep.get("base_url") or "").strip()
            break

    if provider_id == "deepseek":
        api_key = api_key or deepseek_api_key(cfg)
    elif provider_id == "dashscope":
        cloud = cfg.get("cloud") or {}
        api_key = api_key or str(cloud.get("dashscope") or cloud.get("alibaba") or "").strip()

    return api_key, base_url


def apply_provider_credentials(engine: Engine, config: dict, provider_id: str) -> Engine:
    """返回补好凭据的**副本**；引擎没有对应属性时原样返回副本。"""
    api_key, base_url = provider_credentials(
        config, provider_id,
        str(getattr(engine, "api_key", "") or ""),
        str(getattr(engine, "base_url", "") or ""),
    )
    out = copy.copy(engine)
    if api_key and hasattr(out, "api_key"):
        out.api_key = api_key
    if base_url and hasattr(out, "base_url"):
        out.base_url = base_url
    return out
