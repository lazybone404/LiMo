"""
配置加载
"""

import copy
import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "provider": "deepseek",
        "api_key": "",
        "model": {"deepseek": "deepseek-flash", "openai": "gpt-4o", "anthropic": "claude-sonnet-4-6"},
        "base_url": {"deepseek": "https://api.deepseek.com"},
    },
    # 模型预设 — 不同场景绑定不同预设
    # provider/model 为空时使用 llm 全局配置
    "llm_presets": [
        {"id": "generate", "label": "生成笔记", "provider": "", "model": "", "desc": "V4.1 Flash — 快速便宜"},
        {"id": "chat",     "label": "AI 对话", "provider": "", "model": "", "desc": "V4 Pro — 推理更强"},
    ],
    # 可用模型列表 — 前端设置页动态渲染
    # context_window：结构化上下文窗口（token），供对话上下文预算使用（四层：用户自定义 > 预设 > provider 默认表 > 保守 32k）
    # price_in/price_out：每百万 token 价格（元）——预设不填，用户在中转/自定义模型时于设置页填写后才启用费用显示
    "llm_models": {
        "deepseek": [
            # 2026-09-10 官方发布 V4.1-Flash，模型名改为 deepseek-flash：旧名 deepseek-v4-flash 与
            # deepseek-v4-flash-vision-exp 对应的模型均已下线（官方暂用路由兜着，勿再使用）。
            # V4.1-Flash 原生支持图像理解，旧 vision 实验版已无存在必要，合并进 flash 一条。
            # 参数约束（官方「思考模式」页）：思考模式**默认开启**且 effort 默认 high；该模式下
            # temperature / presence_penalty / frequency_penalty **不报错但不生效**；top_p 仅思考
            # 模式生效且范围 0.95–1.0（低于按 0.95）。effort 三档 low/high/max。
            # 价格（元/百万 token，空闲/高峰）：flash 输入 1/2、输出 4/8；pro 输入 4.5/9、输出 13.5/27。
            # 两档差异达一倍且按北京时间工作日 9-12、14-18 分峰谷，预设不填单一价格以免误导。
            # 上下文 1M、最大输出 384K；并发限制 flash 2500 / pro 500。
            # capabilities 的 "reasoning" = 认 reasoning_effort 参数（见 llm/service.py 的
            # supports_reasoning）——它不按 provider 判断，故新渠道要显式标注才会放开档位选择器。
            # ⚠️ 未标 ≠ 不支持，只表示「LiMo 未查证」：前端不展示档位选择器，后端拿到档位会
            # 记一条 warning（失败显式化），而不是静默当没选。
            {"id": "deepseek-flash",  "name": "V4.1 Flash", "desc": "1M上下文 · 原生多模态 · 快", "capabilities": ["text", "image", "reasoning"], "context_window": 1000000},
            {"id": "deepseek-v4-pro", "name": "V4 Pro",     "desc": "1M上下文 · 纯文本 · 推理强", "capabilities": ["text", "reasoning"],          "context_window": 1000000},
        ],
        # capabilities 的 "document" = 支持在消息里带 file 块（PDF 直传，见
        # services/doc_understand.py 与 llm/service.py 的 supports_document）。
        # **按模型精确标注，不做名字自动检测**——按名字猜 PDF 能力必然误标
        # （routers/model.py 的 _PROVIDER_IMAGE_RULES 只管 image，刻意不管 document）。
        # 标注依据 = 官方《PDF 理解》页的支持名单（2026-09-23 逐家查证），**只有四个**：
        #   qwen3.8-max · qwen3.8-max-0902（快照版，不预设）· qwen3.8-flash · qwen3.8-27b
        # ⚠️ 名单**不含**任何 qwen-vl / qwen3.5 视觉模型——页面标题写的是「通过 VL 模型
        # 解析」，但支持列表是收窄的；按「有视觉能力 → 应该也能传 PDF」去推会一次错标三个。
        # ⚠️ 官方另注：该功能**仅华北2（北京）地域**可用，且 Responses API 传 PDF 会返回
        # 200 但不传文件（LiMo 走 Chat Completions，不受影响；见 doc_understand.py）。
        # ⚠️ 其他渠道一律不标，各有硬理由（OpenAI 通道在但模型面不一致 / Anthropic 用
        # document 块不是 file 块 / Gemini 兼容层不支持 / GLM·Kimi 是独立端点抽文本 /
        # opencode 网关未查证且地域可能不符）——逐家结论见 doc_understand.py 的
        # 「哪些渠道能用」表，**不要凭印象补标**。
        "dashscope": [
            # ⚠️ 全段通则（2026-10-02 逐条打开官方模型页核对）：context_window 一律取官方
            # 「上下文限制」表里的【上下文长度】，**不是**「最大输出长度」——两者在同页分列，
            # 极易看错。此前 16 条里 11 条填的是 131072，那是把「最大输出」当成了窗口
            # （对话预算 = 窗口 × 0.7，填错会让可用的 700k 缩成 91k）。
            # 官方页形如 https://help.aliyun.com/zh/model-studio/<模型名小写、点号转连字符>
            # 例：qwen3.7-max → /qwen3-7-max，qwen3-vl-flash → /qwen3-vl-flash（vl 不转）。
            # 每条取值与抓取日期见行尾注释。**改这里的数值前请重新打开该页确认**。
            {"id": "qwen3.8-max",         "name": "Qwen3.8 Max",         "desc": "视觉+视频 · 旗舰 · PDF直传",    "capabilities": ["text", "image", "video", "document"], "context_window": 1000000},
            {"id": "qwen3.8-flash",       "name": "Qwen3.8 Flash",       "desc": "视觉+视频 · PDF直传 · 快",     "capabilities": ["text", "image", "video", "document"], "context_window": 1000000},
            {"id": "qwen3.8-27b",         "name": "Qwen3.8 27B",         "desc": "视觉+视频 · PDF直传 · 开源版", "capabilities": ["text", "image", "video", "document"], "context_window": 1000000},
            # 模态 Text（纯文本，官方页输入模态逐条写明）——qwen3.7-max-2026-06-08 快照版才是
            # 「Image Text Video」，主线刻意只有文本，不要按快照版给它补能力位。
            {"id": "qwen3.7-max",         "name": "Qwen3.7 Max",         "desc": "纯文本 · 旗舰推理",              "capabilities": ["text"],                 "context_window": 1000000},  # 官方 /qwen3-7-max：上下文 1000000，最大输出 131072（2026-10-02）
            {"id": "qwen3.7-plus",        "name": "Qwen3.7 Plus",        "desc": "多模态+视频 · 日常推荐",        "capabilities": ["text", "image", "video"], "context_window": 1000000},  # 官方 /qwen3-7-plus：Image Text Video，1000000（2026-10-02）
            # ⚠️ 以下三条此前标 capabilities=["text"]，官方页均写明是**原生视觉语言系列**：
            # qwen3.7-flash = Image Text Video，qwen3.6-flash = Image Text Video。
            # 漏标 image 会让「AI 分析图片」「OCR 模型选择」两处入口选不到它们。
            {"id": "qwen3.7-flash",       "name": "Qwen3.7 Flash",       "desc": "视觉+视频 · 极速响应",          "capabilities": ["text", "image", "video"], "context_window": 1000000},  # 官方 /qwen3-7-flash：Image Text Video，1000000（2026-10-02）
            {"id": "qwen3.6-flash",       "name": "Qwen3.6 Flash",       "desc": "视觉+视频 · 轻量快速",          "capabilities": ["text", "image", "video"], "context_window": 1000000},  # 官方 /qwen3-6-flash：Image Text Video，1000000（2026-10-02）
            {"id": "qwen-flash",          "name": "Qwen Flash",          "desc": "纯文本 · 最便宜",               "capabilities": ["text"],                 "context_window": 1000000},  # 官方 /qwen-flash：1000000（2026-10-02）
            {"id": "qwen-plus",           "name": "Qwen Plus",           "desc": "纯文本 · 低成本推理",            "capabilities": ["text"],                 "context_window": 1000000},  # 官方 /qwen-plus：1000000（快照版 -2025-07-28 页写的 131072 属旧口径，2026-10-02）
            {"id": "qwen-turbo",          "name": "Qwen Turbo",          "desc": "纯文本 · 极低成本",              "capabilities": ["text"],                 "context_window": 131072},   # 官方 /qwen-turbo：131072（2026-10-02 复核无误）
            # ⚠️ OCR 两条是**高估**，必须改小：默认绑定 qwen-vl-ocr-latest 的真实窗口只有 38192，
            # 填 131072 会让对话/参考压缩按 3.4 倍于真实的预算切分，超窗在请求发出后才暴露。
            {"id": "qwen-vl-ocr-latest",  "name": "Qwen VL OCR",         "desc": "视觉 · 纯OCR识别",              "capabilities": ["text", "image"],          "context_window": 38192},    # 官方 /qwenvl-ocr：最大输入 30000 + 最大输出 8192 = 上下文 38192（2026-10-02）
            {"id": "qwen3.5-ocr",         "name": "Qwen3.5 OCR",         "desc": "视觉 · 卡证票据OCR",            "capabilities": ["text", "image"],          "context_window": 65536},    # 官方 /qwen3-5-ocr：最大输入 49152 + 最大输出 16384 = 上下文 65536（2026-10-02）
            {"id": "qwen3.5-flash",       "name": "Qwen3.5 Flash",       "desc": "视觉+视频 · Flash多模态",       "capabilities": ["text", "image", "video"], "context_window": 1000000},  # 官方 /qwen3-5-flash：Text Image Video，1000000（2026-10-02）
            {"id": "qwen3-vl-flash",      "name": "Qwen3 VL Flash",      "desc": "视觉+视频 · 最快最便宜（视频 2秒-1小时）", "capabilities": ["text", "image", "video"], "context_window": 262144},   # 官方 /qwen3-vl-flash：262144（2026-10-02）
            {"id": "qwen3-coder-plus",    "name": "Qwen3 Coder Plus",    "desc": "纯文本 · 代码生成",              "capabilities": ["text"],                 "context_window": 1000000},  # 官方 /qwen3-coder-plus：1000000（2026-10-02）
            {"id": "qwen-vl-max",         "name": "Qwen-VL Max",          "desc": "视觉+视频 · 旗舰图片理解（视频 2秒-20分）", "capabilities": ["text", "image", "video"], "context_window": 131072},   # 官方 /qwen-vl-max：131072（2026-10-02 复核无误）
        ],
        "openai": [
            {"id": "gpt-4o",        "name": "GPT-4o",        "desc": "文本+视觉", "context_window": 128000},
            {"id": "gpt-4o-mini",   "name": "GPT-4o Mini",   "desc": "文本",       "context_window": 128000},
        ],
        "anthropic": [
            {"id": "claude-sonnet-4-6", "name": "Sonnet 4.6", "desc": "文本+视觉", "context_window": 200000},
            {"id": "claude-opus-4-8",   "name": "Opus 4.8",   "desc": "文本+视觉", "context_window": 200000},
            {"id": "claude-haiku-4-5",  "name": "Haiku 4.5",  "desc": "文本",       "context_window": 200000},
        ],
        "google": [
            {"id": "gemini-3.5-flash", "name": "Gemini 3.5 Flash", "desc": "文本+视觉", "context_window": 1000000},
            {"id": "gemini-3.1-pro",   "name": "Gemini 3.1 Pro",   "desc": "文本+视觉", "context_window": 1000000},
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "desc": "文本+视觉", "context_window": 1000000},
        ],
        # 智谱取值来自官方模型概览页（docs.bigmodel.cn，2026-10-02 抓取）：该页按「输入模态」
        # 分表，每行给出「上下文窗口 / 最大输出 Tokens」两列——**同样要先分清是哪一列**。
        "zhipu": [
            {"id": "glm-5.2",       "name": "GLM-5.2",     "desc": "1M上下文", "context_window": 1000000},  # 官方：1M 上下文 / 128K 出（2026-10-02）
            # ⚠️ 131072 → 200000：GLM-4.7 官方标 200K 上下文（最大输出 128K），此前填的是「输出」那列。
            {"id": "glm-4.7",       "name": "GLM-4.7",     "desc": "推理+编程", "context_window": 200000},   # 官方：200K 上下文 / 128K 出（2026-10-02）
            # ⚠️ glm-4v-plus 已从官方**在售**视觉模型表下架（2026-10-02 逐条核对：在售表中只剩
            # GLM-5V-Turbo / GLM-4.6V / GLM-4.1V-Thinking-FlashX / GLM-4.6V-Flash /
            # GLM-4.1V-Thinking-Flash / GLM-4V-Flash；glm-4v-plus 缺席，仅在**旧价格表**留有
            # 「GLM-4V-Plus-0111 | 16K」的痕迹）。保留条目以免老用户已启用的配置凭空消失，
            # 但标 retired 让前端可提示「已下线」——⚠️ 前端消费 retired 的 UI 尚未实现，
            # 现在它只是一个**待消费的信号**（见 docs 中的模型下线机制方案）。
            {"id": "glm-4v-plus",   "name": "GLM-4V-Plus", "desc": "视觉理解（官方已下架）", "context_window": 131072,
             "retired": "2026-10-02", "retired_note": "已从官方在售视觉模型表移除，建议改用 GLM-4.6V"},
        ],
        # Moonshot 取值来自官方模型文档 platform.moonshot.cn/docs/models（2026-10-02 抓取）。
        # ⚠️ 该厂商的 /v1/models 端点会返回结构化元数据（context_length / supports_image_in /
        # supports_video_in / supports_reasoning）——**是最值得自动核对的渠道之一**（需 API key）。
        "moonshot": [
            # ⚠️ 131072 → 262144：官方标注 256k，且**支持视觉输入**（supports_image_in）。
            {"id": "kimi-k2.6",       "name": "Kimi K2.6",       "desc": "编程+智能体 · 支持视觉", "capabilities": ["text", "image"], "context_window": 262144},  # 官方：256k（2026-10-02）
            # ⚠️ kimi-k2-thinking 官方已于 2026-05-25 下线。同 glm-4v-plus：保留条目 + retired 标记，
            # 不做静默删除（删除对老用户是 no-op，且会让已启用的配置凭空消失）。
            {"id": "kimi-k2-thinking", "name": "Kimi K2 Thinking", "desc": "深度推理（官方已下线）", "context_window": 131072,
             "retired": "2026-05-25", "retired_note": "官方已下线，建议改用 kimi-k3"},
            # 补入官方在售且窗口显著更大的两条（此前预设缺失）
            {"id": "kimi-k3",         "name": "Kimi K3",         "desc": "2.8万亿参数 · 原生视觉 · 1M", "capabilities": ["text", "image"], "context_window": 1000000},  # 官方：1M（2026-10-02）
            {"id": "kimi-k2.7-code",  "name": "Kimi K2.7 Code",  "desc": "编程优化 · 256k",       "context_window": 262144},  # 官方：256k（2026-10-02）
        ],
        # OpenCode Go — OpenAI 兼容网关（opencode.ai/go/v1，chat/completions），
        # 与 OpenCode Zen 共用 OPENCODE_API_KEY（Go 固定订阅 / Zen 按量）。订阅制：5h $12 / 周 $30 / 月 $60。
        # 模型清单来自官方 docs/go 的 openai-compatible 系（GPT/Grok 走 Responses API，与 LiMo chat.completions 不互通，
        # 不进预置，若有需求在设置页手动添加）。Qwen/GLM 高版本暂标 image 供图片处理/OCR 场景可选，
        # 以网关实际能力为准，不支持时在设置页取消「多模态」标记。
        "opencode": [
            {"id": "deepseek-v4-pro",   "name": "DeepSeek V4 Pro",   "desc": "推理更强",            "context_window": 131072},
            {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash", "desc": "快速便宜",            "context_window": 131072},
            {"id": "qwen3.8-max",       "name": "Qwen3.8 Max",       "desc": "旗舰 · 可图片",       "capabilities": ["text", "image"], "context_window": 131072},
            {"id": "qwen3.7-max",       "name": "Qwen3.7 Max",       "desc": "旗舰推理 · 可图片",   "capabilities": ["text", "image"], "context_window": 131072},
            {"id": "qwen3.7-plus",      "name": "Qwen3.7 Plus",      "desc": "日常推荐 · 可图片",   "capabilities": ["text", "image"], "context_window": 131072},
            {"id": "qwen3.6-plus",      "name": "Qwen3.6 Plus",      "desc": "低配 · 可图片",       "capabilities": ["text", "image"], "context_window": 131072},
            {"id": "glm-5.3",           "name": "GLM-5.3",           "desc": "旗舰 · 可图片",       "capabilities": ["text", "image"], "context_window": 131072},
            {"id": "glm-5.2",           "name": "GLM-5.2",           "desc": "推理 · 可图片",       "capabilities": ["text", "image"], "context_window": 131072},
            {"id": "glm-5.1",           "name": "GLM-5.1",           "desc": "轻量推理",            "context_window": 131072},
            {"id": "kimi-k3",           "name": "Kimi K3",           "desc": "深度推理",            "context_window": 131072},
            {"id": "kimi-k2.7-code",    "name": "Kimi K2.7 Code",    "desc": "编程优化",            "context_window": 131072},
            {"id": "kimi-k2.6",         "name": "Kimi K2.6",         "desc": "编程+智能体",         "context_window": 131072},
            {"id": "minimax-m3",        "name": "MiniMax M3",        "desc": "通用",               "context_window": 131072},
            {"id": "minimax-m2.7",      "name": "MiniMax M2.7",      "desc": "通用",               "context_window": 131072},
            {"id": "mimo-v2.5",         "name": "MiMo-V2.5",         "desc": "高性价比",            "context_window": 131072},
        ],
    },
    "fetch": {"proxy": ""},
    "auth": {"bilibili": "", "youtube": "", "douyin": ""},
    "cloud": {"alibaba": "", "dashscope": ""},
    # 引擎厂商（内置 + 用户自定义）
    "engine_providers": [
        {"id": "dashscope", "name": "阿里云 DashScope", "type": "cloud", "base_url": "https://dashscope.aliyuncs.com", "api_key": "", "capabilities": ["asr", "ocr"],
         "builtin_models": [
             {"model_name": "paraformer-v2", "category": "asr", "desc": "离线语音转文字 · 支持大文件"},
             {"model_name": "qwen-vl-max", "category": "ocr", "desc": "旗舰视觉 · 文字提取"},
             {"model_name": "qwen-vl-plus", "category": "ocr", "desc": "轻量视觉 · 文字提取"},
             {"model_name": "qwen-vl-ocr-latest", "category": "ocr", "desc": "纯OCR识别 · 文档/表格/公式"},
             {"model_name": "qwen3-vl-flash", "category": "ocr", "desc": "快速视觉 · 最低成本"},
             {"model_name": "qwen3.5-ocr", "category": "ocr", "desc": "视觉 · 卡证票据OCR"},
         ]},
        {"id": "bcut", "name": "B站必剪（免费云转写）", "type": "cloud", "base_url": "", "api_key": "", "capabilities": ["asr"],
         "builtin_models": [
             {"model_name": "bcut", "category": "asr", "desc": "免费云转写 · 中文优化"},
         ]},
        {"id": "doc-local", "name": "本地文档引擎组", "type": "local", "base_url": "", "api_key": "", "capabilities": ["doc"],
         "builtin_models": [
             {"model_name": "pypdfium2", "category": "doc", "desc": "PDF 极速纯文字+表格+图片（BSD-3）"},
             {"model_name": "markitdown", "category": "doc", "desc": "Office/EPUB 转 Markdown"},
             {"model_name": "python-docx", "category": "doc", "desc": "Word .docx 文字+表格+图片"},
             {"model_name": "python-pptx", "category": "doc", "desc": "PPT .pptx 文字+图片+备注"},
             {"model_name": "openpyxl", "category": "doc", "desc": "Excel .xlsx 表格数据提取"},
             {"model_name": "epub-container", "category": "doc", "desc": "EPUB 电子书 · 章节+图片+元数据（自研）"},
         ]},
    ],
    # 已启用的引擎模型（id → provider_id + model_name + category）
    "engine_models": [],
    # 引擎分配 — 绑定 engine_models 的 id（只取第一个可用，失败直接报错，不再降级）
    "engine_bindings": {
        "asr": [],
        "ocr": [],
        "doc": [],
        "doc:epub": [],
    },
    # 对话上下文管理（C2-C4）— 预算档位 / 历史上限 / 文档引用模式
    "chat_context": {
        "budget_ratio": "auto",   # auto（窗口×0.7）| 0.5 | 0.7 | 0.85（context_window 用于输入的比例）
        "history_limit": 0,        # 历史轮数上限（0 = 不限制，按预算自动裁剪）
        "doc_mode": "auto",        # auto | full | summary（文档引用模式）
    },
    # Agent 工具配置 — 网络搜索 + 网页抓取
    "agent_tools": {
        "search": {"provider": "duckduckgo", "searxng_url": "http://127.0.0.1:18080", "tavily_api_key": ""},
        "fetch":  {"provider": "trafilatura"},
    },
    # 插件系统（EnginePlugin v1）
    "plugin_states": {},    # name → bool（false=禁用，重启后生效；未配置默认 enabled）
    "plugin_configs": {},   # name → dict（插件自身配置，经进程外 stdin JSON config 传给引擎）
    "data_dir": str(Path.home() / ".limo"),
    "log_level": "INFO",
}


def _resolve_data_dir() -> str:
    """确定数据目录，优先 ~/.limo，不可写则降级到 %LOCALAPPDATA%/LiMo"""
    import os as _os

    primary = Path.home() / ".limo"
    # 尝试创建 primary，成功则使用
    try:
        primary.mkdir(parents=True, exist_ok=True)
        # 写入探针验证权限
        (primary / ".write_test").touch()
        (primary / ".write_test").unlink()
        return str(primary)
    except OSError:
        pass

    # 降级 1: %LOCALAPPDATA%/LiMo（Windows 上始终可写）
    localappdata = _os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        fallback = Path(localappdata) / "LiMo"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            return str(fallback)
        except OSError:
            pass

    # 降级 2: %APPDATA%/LiMo
    appdata = _os.environ.get("APPDATA", "")
    if appdata:
        fallback = Path(appdata) / "LiMo"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            return str(fallback)
        except OSError:
            pass

    # 最终降级：临时目录
    import tempfile
    return str(Path(tempfile.gettempdir()) / "LiMo")


def _migrate_legacy_dir(old: Path, new: Path) -> None:
    """从旧数据目录迁移到新目录"""
    if old.exists() and not new.exists():
        try:
            old.rename(new)
            import logging
            logging.getLogger(__name__).info("数据目录已迁移: %s → %s", old, new)
        except OSError:
            pass  # 跨盘符等无法 rename，保留旧目录


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """加载配置，优先从文件，缺省用默认值"""
    # 旧目录迁移: ~/.mybigwork → ~/.limo
    _migrate_legacy_dir(Path.home() / ".mybigwork", Path.home() / ".limo")

    # 智能选择数据目录（~/.limo 不可写时降级到 %LOCALAPPDATA%/LiMo）
    resolved_dir = _resolve_data_dir()
    DEFAULT_CONFIG["data_dir"] = resolved_dir

    # config.yaml 路径：环境变量优先，否则放在数据目录
    if config_path is None:
        config_path = (
            os.environ.get("LIMO_CONFIG") or
            os.environ.get("MYBIGWORK_CONFIG") or
            str(Path(resolved_dir) / "config.yaml")
        )

    config = copy.deepcopy(DEFAULT_CONFIG)

    path = Path(config_path)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                user_config = yaml.safe_load(f) or {}
            _deep_merge(config, user_config)
        except (OSError, yaml.YAMLError, PermissionError) as e:
            import logging
            logging.getLogger(__name__).warning(
                "无法加载 config.yaml (%s)，使用默认配置: %s", path, e
            )

        # 自动修正旧 data_dir
        if ".mybigwork" in config.get("data_dir", ""):
            config["data_dir"] = str(Path.home() / ".limo")

        # 启动时自动备份 — 防止后续写入损坏后可恢复
        backup_path = path.with_suffix(".yaml.bak")
        try:
            import shutil
            shutil.copy2(path, backup_path)
        except Exception:
            pass  # 备份失败不影响启动

    # 向后兼容：engine_bindings 旧格式 int → [int], None → []
    bindings = config.get("engine_bindings", {})
    for cat in ("asr", "ocr", "doc"):
        val = bindings.get(cat)
        if val is None:
            bindings[cat] = []
        elif isinstance(val, int):
            bindings[cat] = [val]
        elif not isinstance(val, list):
            bindings[cat] = []

    # 增量同步：DEFAULT_CONFIG 新增的 builtin_models + provider 条目自动补入已有配置
    _sync_missing_providers(config)
    _sync_builtin_models(config)
    _sync_llm_model_caps(config)

    return config


# 权威字段白名单 —— DEFAULT_CONFIG 是 llm_models 的唯一权威来源，这些字段一律对齐。
#
# ⚠️ 依据（2026-10-02 全仓取证）：**llm_models 层没有任何编辑入口**。
#   后端 6 处引用全为只读：capabilities.py:283（下发给前端）/ :60（判有无 key）/
#   model.py:321（刷新失败回退）/ :387（建 enabled 时查预设能力）/
#   config_io.py:36（None→[] 防御）/ model_resolver.py:110（解析上下文窗口）；
#   前端 3 处亦只读：api/core.ts（类型定义）/ DocLibrary.tsx（换模型推荐）/ 测试。
#   ⇒ 用户**不可能**在 llm_models 里表达任何自定义意图，对齐不会覆盖用户选择。
#
# ⚠️ 为什么必须做对齐（「只补不删」不够）：`_deep_merge` 对**列表是整段替换**，
#   用户 config.yaml 里的 llm_models 是**导入那一刻的快照**，此后 DEFAULT 的任何
#   修正（官方改名 / 窗口变更 / 下线标记）对老用户**全部无效**。实测用户配置：
#   `llm_models.dashscope.qwen3.8-max.context_window = 131072`（冻结在旧值），
#   **正压住** DEFAULT 的 1000000；同理 name/desc 永远停在旧文案。
#
# ⚠️ 本同步**只在内存生效、不落盘**（`load_config` 结束时不 save_config）——
#   刻意为之：模式 #29 的教训是「startup 期间写全量配置会永久丢失敏感字段」。
#   同步是幂等的，每次启动重算结果一致；用户日后自行改设置触发保存时会自然收敛。
_SYNC_OVERRIDE_FIELDS = ("name", "desc", "context_window")

# 下线标记：**双向对齐**（DEFAULT 有则设、DEFAULT 无则删）。
# ⚠️ 必须双向——只补不删的话，某模型官方恢复上架、DEFAULT 撤销了 retired 标记时，
#   用户侧的残留标记会一直显示「已下线」，而用户没有任何办法清掉它。
_SYNC_OPTIONAL_FLAGS = ("retired", "retired_note")

# 点名墓碑表：(provider_id, model_id) → 下线信息。
#
# ⚠️ 与 llm_models 条目里的 retired 字段**分工不同，两者都要**：
#   · llm_models 条目自带 retired —— 该模型**仍在 DEFAULT 预设里**（保留条目，免得
#     老用户配置凭空消失），但官方已下架。例：zhipu/glm-4v-plus、moonshot/kimi-k2-thinking。
#   · 本表 —— 该 id **已从 DEFAULT 预设里移除**（官方改名/下线），只活在**老用户
#     config.yaml 的快照**里。DEFAULT 里没有这个 id，常规对齐（def_by_id 匹配）
#     永远碰不到它，**只能靠本表点名**。
#
# ⚠️ 本表**只给已存在的用户条目标记，绝不新增条目**——新用户不该在模型下拉里
#    看到一条「（旧名）· 官方已改名」（那是纯噪声）。老用户则必须看到：他正配着它。
RETIRED_MODEL_IDS = {
    ("deepseek", "deepseek-v4-flash"): {
        "retired": "2026-09-10",
        "retired_note": "官方改名，请改用 deepseek-flash",
    },
    ("deepseek", "deepseek-v4-flash-vision-exp"): {
        "retired": "2026-09-10",
        "retired_note": "原生多模态已并入 deepseek-flash",
    },
}


def _sync_llm_model_caps(config: dict):
    """将 DEFAULT_CONFIG 的 llm_models 对齐到已有配置（补新增 + 对齐权威字段）。

    _deep_merge 对列表是整段替换，旧 config.yaml 的 llm_models 停留在导入时的快照，
    不包含新增模型，也不吸收 DEFAULT 后续的任何修正。此函数按 provider 匹配做三件事：

    1. 补入 DEFAULT 有而用户没有的模型条目（新增官方模型自动出现）
    2. **对齐权威字段** name / desc / context_window / retired —— 官方改名、窗口口径
       修正、下线标记都靠这一步才能到达老用户；字段白名单的依据见上方注释
       （llm_models 层零编辑入口 ⇒ DEFAULT 可安全覆盖）
    3. 能力位**只补不删** —— capabilities 是这一层**唯一**有编辑入口的字段
       （设置页可手动标记/取消 image 等），故只补 DEFAULT 明确有、用户没有的，
       不动用户已有的标记。

    不碰用户自建模型（DEFAULT 里没有的 id）。
    """
    default_models = DEFAULT_CONFIG.get("llm_models", {})
    user_models = config.get("llm_models", {})
    for prov_id, def_list in default_models.items():
        if not isinstance(def_list, list):
            continue
        user_list = user_models.get(prov_id)
        if not isinstance(user_list, list):
            continue

        # 1) 补入 DEFAULT 有而用户没有的模型条目（新增模型自动出现）
        user_by_id = {m.get("id") for m in user_list if m.get("id")}
        for dm in def_list:
            if dm.get("id") and dm["id"] not in user_by_id:
                # ⚠️ 必须 deepcopy：浅拷贝下 capabilities 是 list，与 DEFAULT_CONFIG
                #    共享引用——后续任何 in-place 修改都会污染模块级全局，
                #    波及所有新用户的默认配置
                user_list.append(copy.deepcopy(dm))
                logger.info("配置同步: 新增 LLM 模型 %s/%s", prov_id, dm["id"])

        def_by_id = {m.get("id"): m for m in def_list if m.get("id")}
        for um in user_list:
            mid = um.get("id")
            dm = def_by_id.get(mid) if mid else None
            if not dm:
                continue  # 用户自建模型（DEFAULT 无此 id）→ 不碰

            # 2) 权威字段对齐（DEFAULT 声明了才覆盖，未声明则保留用户值）
            for fld in _SYNC_OVERRIDE_FIELDS:
                if fld not in dm:
                    continue
                new_val = copy.deepcopy(dm[fld])
                if um.get(fld) != new_val:
                    um[fld] = new_val
                    logger.info(
                        "配置同步: 模型 %s/%s 字段 %s 对齐 %r → %r",
                        prov_id, mid, fld, um.get(fld), new_val,
                    )

            # 2b) 下线标记双向对齐（DEFAULT 撤销则删除用户侧残留）
            for fld in _SYNC_OPTIONAL_FLAGS:
                if fld not in dm and fld in um:
                    del um[fld]
                    logger.info("配置同步: 模型 %s/%s 撤销 %s", prov_id, mid, fld)
                elif fld in dm and um.get(fld) != dm[fld]:
                    um[fld] = dm[fld]
                    logger.info("配置同步: 模型 %s/%s 标记 %s=%s", prov_id, mid, fld, dm[fld])

            # 3) 能力补全（只补 DEFAULT 明确有的，不动用户已有标记）
            def_caps = dm.get("capabilities")
            if not def_caps:
                continue
            user_caps = um.get("capabilities") or []
            missing = [c for c in def_caps if c not in user_caps]
            if missing:
                um["capabilities"] = user_caps + missing
                logger.info("配置同步: 模型 %s/%s 能力补全 %s", prov_id, mid, missing)

    # B) 点名墓碑：DEFAULT 里**没有**的旧模型 id（官方改名/移除），只在老用户的
    #    config.yaml 快照里存在 —— 上面的 def_by_id 匹配永远碰不到它们。
    #    ⚠️ 只标记已存在的条目，**绝不新增**（`user_list` 不存在即跳过）。
    for (prov_id, mid), info in RETIRED_MODEL_IDS.items():
        user_list = user_models.get(prov_id)
        if not isinstance(user_list, list):
            continue
        for um in user_list:
            if um.get("id") != mid:
                continue
            for fld, val in info.items():
                if um.get(fld) != val:
                    um[fld] = val
                    logger.info(
                        "配置同步: 旧模型 %s/%s 标记 %s=%s", prov_id, mid, fld, val
                    )


def _sync_missing_providers(config: dict):
    """将 DEFAULT_CONFIG 中已定义但用户 config.yaml 中缺失的 engine_providers 补入。

    _deep_merge 对列表是整段替换，旧 config.yaml 不包含新增 provider（如 bcut）。
    此函数按 id 匹配，补入 DEFAULT_CONFIG 中有而用户配置中没有的条目。
    """
    default_provs = DEFAULT_CONFIG.get("engine_providers", [])
    user_provs = config.get("engine_providers", [])
    user_ids = {p["id"] for p in user_provs}
    added = False
    for dp in default_provs:
        if dp["id"] not in user_ids:
            user_provs.append(dp)
            added = True
            logger = __import__("logging").getLogger(__name__)
            logger.info("Auto-added missing engine_provider: %s", dp["id"])
    if added:
        config["engine_providers"] = user_provs


def _deep_merge(base: dict, override: dict):
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _sync_builtin_models(config: dict):
    """将 DEFAULT_CONFIG 的 builtin_models 增量同步到已有配置（只补不删）。

    只做两件保守的事，不碰用户自定义模型：
    1. 补入 DEFAULT 中有而用户没有的 builtin 模型（新增引擎自动出现）
    2. 移除已废弃的旧 provider 整组（pymupdf — AGPL 引擎组，id 已迁移为 doc-local）
       其 builtin_models 与 engine_models 条目随 provider 一起清理。
    """
    default_provs = DEFAULT_CONFIG.get("engine_providers", [])
    user_provs = config.get("engine_providers", [])
    default_by_id = {p["id"]: p for p in default_provs}

    for up in user_provs:
        dp = default_by_id.get(up["id"])
        if not dp:
            continue
        default_builtins = dp.get("builtin_models", [])
        user_builtins = up.get("builtin_models", [])
        existing = {(bm["model_name"], bm.get("category", "")) for bm in user_builtins}
        for dbm in default_builtins:
            key = (dbm["model_name"], dbm.get("category", ""))
            if key not in existing:
                user_builtins.append(dbm)
                logger.info("配置同步: 新增引擎模型 %s/%s", up["id"], dbm["model_name"])

    # ── 旧 provider 清理 ──
    # pymupdf（AGPL 引擎组）已迁移为 doc-local；faster-whisper 已从内置移除（需自行安装插件）
    _DEPRECATED_PROVIDERS = {"pymupdf", "faster-whisper"}
    config["engine_providers"] = [
        p for p in user_provs if p.get("id") not in _DEPRECATED_PROVIDERS
    ]

    # engine_models 残留清理：provider 已不存在的条目移除（含 pymupdf 全部引擎）
    engine_models = config.get("engine_models", [])
    live_prov_ids = {p.get("id") for p in config.get("engine_providers", [])}
    kept = [m for m in engine_models if m.get("provider_id") in live_prov_ids]
    dropped = len(engine_models) - len(kept)
    if dropped:
        logger.info("配置同步: 移除 %d 个失效引擎模型（provider 已废弃）", dropped)
    config["engine_models"] = kept


import hashlib as _hashlib

def build_engine_models(config: dict) -> list[dict]:
    """从 builtin_models 动态注册引擎模型，返回完整的 engine_models 列表。

    config.yaml 中 engine_models 可能为空或不完整（builtin 模型只在 API 响应中
    动态注册，不持久化），但 adapter/process 的引擎解析器需要完整列表来将绑定 ID
    映射到 provider/model。此函数封装 capabilities/list_engine_models 同款逻辑。
    """
    models = [dict(m) for m in config.get("engine_models", [])]
    existing = {(m["provider_id"], m["model_name"], m["category"]) for m in models}
    ENGINE_CATS = {"asr", "ocr", "doc"}
    for prov in config.get("engine_providers", []):
        for bm in prov.get("builtin_models", []):
            if bm["category"] not in ENGINE_CATS:
                continue
            key = (prov["id"], bm["model_name"], bm["category"])
            if key not in existing:
                mid = int(_hashlib.md5(
                    f"engine:{prov['id']}:{bm['model_name']}".encode()
                ).hexdigest()[:8], 16)
                models.append({"id": mid, "provider_id": prov["id"],
                               "model_name": bm["model_name"], "category": bm["category"]})
                existing.add(key)
    return models
