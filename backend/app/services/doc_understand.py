"""
文档直传（PDF → 模型 `file` 块）— Qwen 优先。

把 PDF **原件**作为消息的一部分发给模型，由模型自己「看」原件，替代默认的
「本地 Doc 引擎抽 Markdown → 文本进 prompt」链路。与 services/video_understand.py
同族的直传能力，但形态不同：视频直传是**先理解成描述文本**再注入 prompt
（SDK 原生通道，服务端抽帧）；文档直传是**原件随生成请求一起发**
（file 块），故本模块只负责「文件 → 消息块」，不发起任何 LLM 调用。

## 为什么必须走 Chat Completions（不要顺手换成 Responses API）

LiMo 侧实测（2026-09-23）：dashscope 兼容层上**两条通道都能送达文件**
（与 OpenAI 官方「Responses API 会丢文件」的记载不同）。但官方文档明确写了
Responses API 传 PDF「返回 HTTP 200 但文件不会传给模型」，而这类失败
**没有任何客户端可见信号**——产物会是一篇看不出异常的泛泛而谈。Chat
Completions 的 `file` 块是本项目唯一逐次自证过的通道，换过去等于把唯一
有据可依的路径换成有条件成立的路径。

自证方式（`backend/_pdf_probe.py`，探针脚本已按约定用完即删，方法留档于此）：
造一个**图片型** PDF（内容以像素承载，无文本层），页内写唯一随机串
`LIMOPROBE-xxxxxx`，令模型复述——**能复述 = 文件真的到了**，只回泛泛内容 = 未传输。
⚠️ 判据要宽松：模型 OCR 会把 `0` 读成 `O`、`1` 读成 `l`，逐字符严格比较会得到
假阴性（首版就栽在这，险些把「已送达」误判成「不支持」），故随机串字符集排除
易混字符且比对前做归一化。

## 哪些渠道能用（2026-09-23 逐家查官方文档）

能力位 `document` **只标查证过的**——标了就是给用户一个入口，标错等于给假入口。

| 渠道 | 官方结论 | 标注 |
|---|---|---|
| dashscope | 《PDF 理解》页明列 **qwen3.8-max / qwen3.8-max-0902 / qwen3.8-flash / qwen3.8-27b** | 前三个（+flash/27b 新增预设）标 ⚠️ 见下 |
| openai | 官方指南：Chat Completions **只支持 PDF**（`file_data`/`file_id`，不支持 `file_url`/`detail`）；但社区反复反馈 gpt-5 系**看不到文件**、Azure gpt-4o 直接报 `does not support file content types` | **不标**（通道在、模型面不一致，LiMo 未实测） |
| anthropic | 原生用 `document` 块，**不是** OpenAI 的 `file` 块；而 LiMo 所有 provider 共用 AsyncOpenAI，发出去的是后者 | **不标**（格式不通，且无独立适配层） |
| google | OpenAI 兼容层**不支持**文档理解（Google 官方 forum 确认）——兼容层与原生 API 是两回事 | **不标** |
| zhipu | 文件能力走独立端点（`layout_parsing` / `files` + `purpose`），**不是** message 内 file 块；且预设内无声明「文件」模态的模型 | **不标** |
| moonshot | 同 zhipu：`files.create(purpose="file-extract")` **抽文本**再塞进 messages；官方明确「不支持用 file_id 引用文件内容作为上下文」 | **不标**（语义是抽文本，不是原件直传） |
| opencode 网关 | 未查证能否把 file 块转发到上游；且官方明写 PDF 理解**仅限华北2（北京）地域**，网关转发到非北京地域必失败 | **不标** |

⚠️ **dashscope 的坑**：官方 PDF 名单**只有 qwen3.8 系四个**，`qwen3-vl-flash` /
`qwen-vl-max` / `qwen3.5-flash` 这些「视觉模型」**都不在名单里**（页面标题虽叫
「PDF文件通过 VL 模型解析」，但支持列表是收窄的）。按「有视觉能力 → 大概能传 PDF」
去推，会一次标错三个模型。

⚠️ 单文件 ≤150MB / ≤256 页是**厂商侧**限制（走 URL 传才算数）；LiMo 走 base64，
真实闸门见下一节。

## 为什么闸门是 14MB 而不是厂商宣称的 150MB

dashscope 对 **data-uri 形态**的 file 块有硬上限：实测 19MB PDF 直接被拒，
报 `Exceeded limit on max bytes per data-uri item : 20971520`（20 MiB）。
⚠️ 该上限约束的是 **base64 之后的字符串**，不是原始文件：PDF 膨胀 4/3，
按 `len("data:application/pdf;base64,") + 4*ceil(N/3) ≤ 20971520` 反解，
原始字节上限 ≈ 15,728,619（≈15.0 MiB）。

取 **14 MiB** 而非贴着理论边界：15 MiB（15728640 字节）算出的 data-uri
会比上限**多 25 字节**，照样被拒——贴边的常量等于一个随机失效的开关。

想突破这个体积只能改走 Files API（先上传取 file_id），但**首包耗时才是真瓶颈**：
实测 12KB → 3.4~4.7s、9.5MB → 131~143s（≈10s/MB，次线性，由页数主导），
官方文档给出的最坏值则是「首包最长 300 秒」——150MB 量级没有任何用户等得起。
故本轮不引入 Files API。

## ⚠️ 双真相源：直传模式下 rawText 不是模型所见的真相源

| 场景 | 模型看到 | 转录 tab / 导出 / FTS 搜索看到 |
|---|---|---|
| 扫描版 PDF、本地无 OCR | 读懂了原件，笔记翔实 | rawText 近乎空 → 转录空白、搜不到、导出无内容 |
| 图片型表格 / 图表 | 能看到 | rawText 里没有 |

**这不是可修的 bug，是本模式的固有代价**，实现者与用户都必须知道。
故：① Doc 引擎仍照跑（rawText 是导出/搜索/预览的真相源，它是本地库、不花
token，跳过它 = 转录 tab 空白 + 导出丢内容）；② 直传成功但 rawText 过短时，
**生成完成时给一条显式 warning**（失败显式化，见 routers/generate.py）。

## 为什么「只发原文件」而不是「原件 + 抽取文本」

叠加会让同一份内容 token 双花（PDF 视觉计费 + Markdown 文本各占一遍），
大 PDF 尤其明显。用户在生成面板上二选一，不是二合一。
"""

from __future__ import annotations

import base64
import os
import re

from app.core import logging as log

# 「本地抽取近乎为空」的判据（字符数），用于直传后的完成期 warning。
# 取 100 而非 0：扫描件 + 无 OCR 时 rawText 并不是空串，而是**占位文本**
# （原图 markdown + `_所有 OCR 引擎均失败_`，约 50 字符）——只判空串会漏掉
# 这种「看起来有内容、实际没内容」的典型形态，而那正是本提示要覆盖的场景。
DOC_DIRECT_MIN_RAW_CHARS = 100

# 直传体积闸门（**原始字节**）——由 data-uri 硬上限倒推，见模块文档
DOC_DIRECT_MAX_BYTES = 14 * 1024 * 1024

# 直传模式下生成调用的**首个 chunk 静默超时**（秒）。
#
# ⚠️ 值是探针实测出来的，不是估的——预设值一旦小于真实首包，直传每次都会在首包
# 到达前被自己的超时切断，静默降级回文本模式：**功能上线即等于没上**，而日志里
# 只看到「超时」，看起来像厂商不配合。
#
# 实测首包（`qwen3.8-max`，图片型 PDF，每档 1~3 次采样）：
#    12KB / 1 页  → 3.4s · 4.4s · 4.7s
#    9.5MB / 100 页 → 131.5s · 142.8s
#   13.7MB / 140 页 → 149.3s
# 曲线**次线性**（体积 ×1.44 只涨 6s，由页数而非字节数主导）。
#
# ⚠️ **但取值不能只按实测加余量**——初版正是这么算的（149.3 × 1.6 ≈ 240），
# 而那个值是错的：官方《PDF 理解》文档明写「首包超时时间最长为 **300 秒**」。
# 240 < 300，于是落在 240~300s 区间的**正常请求会被我们自己的超时掐断**，
# 照样静默降级，日志里只看到「超时」，看起来像厂商不配合。
# 实测覆盖的是那几档样本，**它证不了上界**；上界只写在官方文档里（模式 #39：
# 判据跑到了，但它看的输入不是它以为的那个）。
# 330 = 官方上限 300 × 1.1 —— 不贴着 300 设，否则服务端正好卡点返回时
# 变成「厂商超时」与「我们的超时」赛跑。
DOC_DIRECT_TIMEOUT = 330.0

# 直传模式下生成调用的**总超时**（秒）。
# 总超时包住「首包静默 + 全部输出」两段：首包最长 330s（见上），之后还要正常生成
# 数千字（~330s）。沿用基础档 330 会让「首包 330s 的正常直传」剩 0s 生成预算，
# 长文档必被整体掐断。660 = 330 + 330。
#
# ⚠️ 与 DOC_DIRECT_TIMEOUT 是**乘二关系不是并列关系**：调大上面那个而不动它，
# 或者反过来，都会让某一侧先到——前者把生成预算吃光，后者让首包永远轮不到
# （单 chunk 超时成了死代码）。改一个必须同时看另一个。
DOC_DIRECT_GEN_TIMEOUT = 660.0

# data-uri 前缀：MIME 必须是 application/pdf，模型据此判定文件类型
_PDF_MIME = "data:application/pdf;base64,"

# 落盘原件名形如 `{uuid4().hex}_{safe_name}`（ingest 的 _persist_ingest_result），
# 32 位 hex 前缀对模型无意义——剥掉后 filename 更干净（模型会把它当文档名参考）
_UUID_PREFIX = re.compile(r"^[0-9a-f]{32}_")

_FILENAME_MAX = 80


def clean_filename(file_path: str) -> str:
    """basename → 剥 uuid 前缀 → 限长；空则回退 `document.pdf`。

    只做展示性清洗（不含安全语义）：值由 json 序列化承载，不存在结构化注入。
    """
    name = os.path.basename(file_path or "").strip()
    name = _UUID_PREFIX.sub("", name)
    if len(name) > _FILENAME_MAX:
        base, ext = os.path.splitext(name)
        name = base[: _FILENAME_MAX - len(ext)] + ext
    return name or "document.pdf"


def build_pdf_document(file_path: str) -> dict | None:
    """PDF 原件 → 直传文档块 `{"filename", "data_uri"}`。

    返回 None = **不可直传，调用方保持文本模式**（`clean_text` 原样不动）——
    注意这不是「换一条链路」，而是「零成本回到现状」。所有失败分支都
    log.warning 带原因，让「开了开关但没用上」在日志里可见。

    只负责编码，不发起任何请求：真正的传输发生在生成调用里（LLMService.stream
    的 file 块），因此这里的耗时是百毫秒级（读 14MB + base64），不需要超时包装。
    """
    # 扩展名闸门（纵深防御）：前端只在 isPDF 时显示开关，但 API 可直调——
    # MIME 被硬编码为 application/pdf，非 PDF 进来会以错误类型发出。
    if not (file_path or "").lower().endswith(".pdf"):
        log.warning("文档直传: 仅支持 PDF，改用文本模式",
                    path=os.path.basename(file_path or "")[:80])
        return None

    try:
        size = os.path.getsize(file_path)
    except OSError as e:
        log.warning("文档直传: 原件不可读，改用文本模式",
                    path=file_path, error=str(e)[:80])
        return None

    if size <= 0:
        log.warning("文档直传: 原件为空，改用文本模式", path=file_path)
        return None

    if size > DOC_DIRECT_MAX_BYTES:
        log.warning("文档直传: 超过 data-uri 体积闸门，改用文本模式",
                    size_mb=round(size / (1024 * 1024), 1),
                    limit_mb=DOC_DIRECT_MAX_BYTES // (1024 * 1024))
        return None

    try:
        with open(file_path, "rb") as f:
            data = f.read()
    except OSError as e:
        log.warning("文档直传: 原件读取失败，改用文本模式",
                    path=file_path, error=str(e)[:80])
        return None

    doc = {
        "filename": clean_filename(file_path),
        "data_uri": _PDF_MIME + base64.b64encode(data).decode("ascii"),
    }
    log.info("文档直传: 原件已编码",
             filename=doc["filename"], size_mb=round(size / (1024 * 1024), 1))
    return doc
