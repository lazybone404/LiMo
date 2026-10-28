"""
引擎插件加载器 — 从 {data_dir}/plugins/*.py 自动发现并注册第三方引擎。

EnginePlugin v1 契约（2026-08-02 正式化，以用户定稿 v2 为准）：

- 默认进程外：`command` 指向独立 venv，subprocess 调用（依赖隔离 / 崩溃隔离 / 长任务）
- trusted 例外：插件声明 `trusted=true` → 允许进程内 import（纯技术声明：可进程内 import，
  崩溃/依赖风险插件作者自担；**不涉及任何协议判断**）
- 门控全类别统一：command 有值→external；trusted→inproc；无 command 且未 trusted→拒绝加载
- 进程外契约：stdin JSON（file/cancel_file/config/...config）+ stdout JSON（text/segments/pages...）
- v0 兼容：stdin 仍带 `--input <path>` argv；stdout 非 JSON 回退纯文本（旧插件零改动可用）
- 主程序不处理任何插件协议维度（不判断/不警告/不声明），license 字段仅前端展示
- PLUGIN_REGISTRY 登记所有已发现插件（含门控拒绝/禁用的），供管理 API 枚举

**两类 plugin 基类**（同一条扫描 / 门控 / 启用 / 卸载链路，契约不同）：
  - `EnginePlugin`：doc / ocr / asr —— 「一个文件 → 一段文本」，入口 `process()`
  - `EmbeddingPlugin`：embedding —— 「一组文本 → 一组向量」，入口 `embed()`
    ⚠️ 进程外模式下它**不是**一次性 stdin/stdout，而是常驻子进程 + 按行 JSON 往返
    （模型加载 3–5 秒 > 查询侧 `QUERY_TIMEOUT = 3.0`，每次 Popen 等于每次搜索都超时）
"""

import ast
import asyncio
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from app.core.registry import EngineRegistry
from app.core.protocols import Engine
from app.core.deps import probe_venv
from app.core.errors import FatalError

logger = logging.getLogger(__name__)

PLUGIN_API_VERSION = 1


class EnginePlugin:
    """插件基类 — 第三方引擎继承并声明元数据。

    例 (~/.limo/plugins/my_plugin.py，进程外模式，推荐)：
        from app.engines.plugin_loader import EnginePlugin
        class MyEngine(EnginePlugin):
            name = "my-engine"
            category = "doc"
            command = ["my_engine/.venv/Scripts/python.exe", "my_engine/engine.py"]

    例 (trusted 进程内模式，仅轻量无重依赖插件)：
        class MyLightEngine(EnginePlugin):
            name = "my-light"
            trusted = True   # 显式信任声明：允许进程内 import，风险自担
            def process(self, file_path, config):
                return "markdown"
    """

    # ── 运行模式（二选一）──
    command: Optional[list[str]] = None   # 有值=进程外（默认主体）；None 需 trusted=true
    trusted: bool = False                 # 显式信任声明：允许进程内 import（纯技术含义）

    # ── 元数据 ──
    category: str = "doc"                 # 开放字符串（v1 生效：doc/ocr/asr）
    name: str = ""
    license: str = "Unknown"              # SPDX（仅前端展示，无警告语义）
    description: str = ""
    file_types: list[str] = []            # 如 [".pdf"] → capabilities fileTypeMap 自动归入
    display_name: str = ""                # 用户向展示名，缺省回退 name
    user_desc: str = ""                   # 人话能力描述
    config_schema: list[dict] = []        # v1 简版配置声明（前端渲染简单表单）
    namespace: str = "engine"             # 注册表命名空间（v1 恒 engine，为 adapter/tool 预留）
    permissions: list[str] = []           # 预留空位（权限模型后置）
    api_version: int = PLUGIN_API_VERSION

    timeout: int = 300                    # 子进程超时秒数（仅进程外模式）

    #: 用户在设置页保存的配置（`config_schema` 表单值）。**由 adapter 回填** ——
    #: 插件自己不要给它初始赋值；没配过时是空 dict，读到就用默认值。
    #: 声明层的 `precheck()` 可以直接读它（自检发生在设置页，那时配置已回填）。
    plugin_config: dict = {}

    # ── Engine 协议适配 ──

    def accept(self, file_path: str) -> bool:
        if not self.file_types:
            return True
        return any(file_path.lower().endswith(ext) for ext in self.file_types)

    def process(self, file_path: str, config: dict):
        """进程内模式实现（trusted 例外）。返回 str（text）或 dict（segments 等）。"""
        raise NotImplementedError

    def precheck(self) -> Optional[str]:
        """就绪自检（可选）。返回**给用户看的**不可用原因，`None` = 可以跑。

        默认实现返回 `None`（没有额外前置条件）—— 插件作者不重写它，语义就是
        「装上了就能用」。需要外部依赖 / 模型文件 / 密钥的插件重写它。

        ⚠️ **必须「报告」而不是「第一次调用时抛异常」**：抛异常的那条路上，用户
        已经选好了引擎、点了生成，代价是一次失败任务；而自检发生在设置页/启动时，
        用户还没付出任何东西。这也是 `core/deps.probe()` 那个形状的来源 ——
        两者都返回 `None | str`，消费方不需要转译。

        ⚠️ **这一条进契约、而 `precheck`/`warmup`/`release` 三个同类钩子不进
        `EmbeddingEngine` 协议，不是双标**：那个协议是 `runtime_checkable`，
        加成员会让**云端引擎不再满足它**（云端确实没有「依赖装没装」这回事）；
        而 `EnginePlugin` 是普通基类，加一个有默认实现的成员不会排斥任何子类。

        ⚠️ 会被 `GET /api/plugins`（设置页插件页）调用，故**必须轻**：不要在这里
        加载模型 / 起子进程。`core.deps.probe()` 的「真 import 一次」是可接受的上界，
        它的第二次及以后由 `sys.modules` 命中。
        """
        return None


class EmbeddingPlugin(EnginePlugin):
    """向量化插件基类 —— **一组文本 → 一组向量**。

    与 doc / ocr / asr 的区别不是「分类不同」，而是**形状不同**：那三类都是
    「一个文件 → 一段文本」，走 `process()`（进程内）或一次性 stdin/stdout（进程外）；
    这一类吃的是文本、吐的是向量，连「文件」这个概念都没有。

    ## 进程外模式：常驻子进程 + 按行 JSON（**不是每次 Popen**）

    每次 `Popen` 都要重新加载模型（3–5 秒），而查询侧
    `VectorSearcher.QUERY_TIMEOUT = 3.0` —— 那样每次搜索都会超时并退回纯字面，
    用户看到的提示是「语义检索超时」（他会以为是网络问题，而插件根本不联网）。
    故外部模式下的协议是：

    ```
    启动   → 子进程 stdout 第一行：{"ready": true, "model_id": "...", "dimension": 512}
    请求   → stdin:  {"id": 1, "texts": ["...", "..."]}
    响应   → stdout: {"id": 1, "vectors": [[...], [...]], "dimension": 512}
    出错   → stdout: {"id": 1, "error": "原因"}
    ```

    ⚠️ `id` 必须原样回传：它是「这条响应属于哪次请求」的唯一判据。管道一旦错位
    （超时后被丢下的半条响应），后续每一轮都会读到上一条的结果，而形状完全合法
    —— 那会是「检索结果莫名其妙」，不像错误。adapter 发现 `id` 不符会丢弃子进程。

    ## 实现注意

    - 子进程**必须无缓冲**：`command` 里带 `-u`（或 `PYTHONUNBUFFERED=1`），
      否则按行往返会一直等在缓冲区里。
    - 每条向量**必须已 L2 归一化**：余弦退化成点积，未归一化的向量不会报错，
      只会让排序静默变差。
    """

    #: 覆盖父类的 `doc` —— 注册表按它分派到 `_PluginEmbeddingAdapter`。
    category = "embedding"
    capabilities = ["embedding"]

    #: 落库的模型标识（`vector_store` 四元组身份之一）。
    #: ⚠️ **换权重或量化档（int8 ↔ fp32）必须改它**：两套权重共用一个 id 时
    #: `ensure_identity` 会判「没变」，而向量已经换了 —— 检索质量轻微下降、零报错。
    model_id = ""

    #: 输出维度。与 `model_id` 一起构成索引口径；**不声明会在自检里被拦下**
    #: （维度 0 写进库之后，余弦会在不同维度之间静默算错）。
    dimension = 0

    #: 是否本地推理。前端据此显示「免费 / 零外发」的成本文案（不靠名字前缀猜）。
    is_local = True

    #: 进程外模式下 `precheck` 要检查的子 venv 依赖（**import 名**，不是 PyPI 包名）。
    #: ⚠️ 非空时才做 venv 检查 —— `command` 指向自带二进制的插件（如 tesseract 那种
    #: 没有 venv 的形态）不会因此被误报成「未创建独立环境」。
    requires_modules: tuple[str, ...] = ()

    #: 进程外模式下 `precheck` 要检查的必需文件（相对插件子目录，或绝对 / `~` 路径）。
    #: 模型文件这类「装了依赖也还不一定能跑」的东西写在这里。
    requires_files: tuple[str, ...] = ()

    def embed(self, texts: list[str]) -> list[list[float]]:
        """同步实现 —— adapter 负责丢线程池（ONNX 推理是阻塞 CPU 计算）。

        契约（与 `engines/embedding_base.py` 同一份）：

        1. 顺序与入参**一一对应**；
        2. 每条**已 L2 归一化**；
        3. 空输入返回 `[]`，且**不发起任何调用**（不该为了一个空批把模型拉进内存）。
        """
        raise NotImplementedError


class RerankPlugin(EnginePlugin):
    """重排插件基类 —— **一个查询 + 一组候选文本 → 一组「下标 + 分数」**。

    ## 第三种形状

    doc / ocr / asr 是「一个文件 → 一段文本」（`process()`），embedding 是
    「一组文本 → 一组向量」（`embed()`），这一类是「比较」—— 它同时吃查询与
    候选，输出的是**候选之间的相对次序**。三者共用同一条扫描 / 门控 / 启用 /
    卸载链路，只有契约入口不同。

    ## 进程外模式：同样是常驻子进程 + NDJSON（不是每次 Popen）

    每轮 Popen 都要重载权重（bge-reranker 量级 500MB+，冷读几十秒），而重排
    发生在**一次在线检索里**，`RERANK_TIMEOUT = 5.0` 秒 —— 每次起进程等于
    每次搜索都超时。协议：

    ```
    启动   → stdout 第一行：{"ready": true, "model_id": "bge-reranker-v2-m3"}
    请求   → stdin:  {"id": 1, "query": "...", "documents": ["...", "..."], "top_n": 20}
    响应   → stdout: {"id": 1, "results": [[0, 0.83], [3, 0.71]]}
    出错   → stdout: {"id": 1, "error": "原因"}
    ```

    ⚠️ `results` 里的**第一个数是入参下标**，不是结果里的新位置 —— 搞反的结果
    是整张排名表错位，而每一行看起来都合法。`id` 必须原样回传（同 `EmbeddingPlugin`）。

    ## 实现注意

    - 子进程**必须无缓冲**（`command` 里带 `-u`），否则按行往返会卡在缓冲区。
    - **允许只返回前 k 条**（`top_n` 语义），调用方会把缺失下标按原序补到尾部
      —— 但不要用「补一个假分数」的方式凑满，那会让尾部候选莫名排到前面。
    """

    #: 覆盖父类的 `doc` —— 注册表按它分派到 `_PluginRerankAdapter`。
    category = "rerank"
    capabilities = ["rerank"]

    #: 权重标识。换权重 / 换量化档（int8 ↔ fp32）**必须改它**：两套权重共用一个
    #: id 时实验记录与日志里分不出「这次用的是哪一份」，分数漂移无从归因。
    model_id = ""

    #: 单次请求能吃的候选条数上限，`0` = 不确定 / 不设限（由服务端兜底）。
    #: ⚠️ 这是**能力声明不是调优旋钮**：编小会让调用方白丢候选（重排只能在
    #: 召回内挑），编大则会拿到 400。
    max_documents = 0

    #: 是否本地推理。前端据此显示「免费 / 零外发」的成本文案（不靠名字前缀猜）。
    is_local = True

    #: 进程外模式下 `precheck` 要检查的子 venv 依赖（**import 名**，不是 PyPI 包名）。
    requires_modules: tuple[str, ...] = ()

    #: 进程外模式下 `precheck` 要检查的必需文件（相对插件子目录，或绝对 / `~` 路径）。
    requires_files: tuple[str, ...] = ()

    def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        """同步实现 —— adapter 负责丢线程池（打分是阻塞 CPU / 网络计算）。

        契约（与 `engines/rerank_base.py` 同一份）：

        1. 返回 `[(原始下标, 分数)]`，下标在 `[0, len(documents))` 且互不重复；
        2. 空 `documents` 返回 `[]`，且**不发起任何调用**；
        3. 允许返回子集（前 k 条），尾部由调用方补齐。
        """
        raise NotImplementedError


# ── 加载门控 ──

def _resolve_exec_mode(plugin: EnginePlugin) -> Optional[str]:
    """加载门控（全类别统一）：command→external；trusted→inproc；无 command 且未 trusted→拒绝"""
    if plugin.command:
        return "external"
    if plugin.trusted:
        return "inproc"
    logger.warning(
        "插件 %s 无 command 且未声明 trusted=true，拒绝加载（默认进程外）", plugin.name
    )
    return None


# ── 契约解析与结果分派（纯函数，可单测）──

def _parse_stdout(raw: str) -> dict:
    """进程外 stdout 解析：JSON 优先；非 JSON 回退 v0 纯文本（doc/ocr→text、asr→单段）"""
    s = raw.strip()
    if s.startswith("{"):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    return {"text": s, "_v0": True}


def _dispatch_result(category: str, payload: dict, engine_name: str):
    """按 category 分派为结构化结果（类别开放，未知类别默认 doc 契约）。

    修复 v0 返回纯 str 导致消费方 .text/.segments 访问崩的核心 bug。
    """
    if category == "asr":
        from app.engines.transcript import TranscriptResult, TranscriptSegment
        segs = [
            TranscriptSegment(
                float(s.get("start", 0)), float(s.get("end", 0)), s.get("text", "")
            )
            for s in payload.get("segments", [])
        ]
        if not segs and payload.get("text"):   # v0 纯文本 asr → 单段兜底
            segs = [TranscriptSegment(0, 0, payload["text"])]
        full = payload.get("full_text") or " ".join(x.text for x in segs)
        return TranscriptResult(
            language=payload.get("language", "zh"),
            duration=payload.get("duration", segs[-1].end if segs else 0),
            segments=segs,
            full_text=full,
        )
    if category == "ocr":
        from app.engines.ocr import OCRResult
        return OCRResult(
            text=payload.get("text", ""),
            engine=engine_name,
            confidence=float(payload.get("confidence", 0.0)),
        )
    from app.engines.doc_types import DocResult
    return DocResult(
        text=payload.get("text", ""),
        pages=int(payload.get("pages", 1)),
        engine=engine_name,
        is_scanned=bool(payload.get("is_scanned", False)),
        scanned_pages=int(payload.get("scanned_pages", 0)),
    )


# ── Plugin adapter: wraps EnginePlugin into Engine protocol ──

def _resolve_command(plugin: EnginePlugin, plugin_dir: Path) -> list[str]:
    """`command` 里的相对路径按插件目录解析成绝对路径。

    **两处共用**（`_PluginEngineAdapter._run_external` 的一次性调用 /
    `_PluginEmbeddingAdapter` 的常驻子进程）—— 各写一份的失效形态是
    「文件引擎能跑、向量插件找不到解释器」，而两边都不报错。

    Windows `CreateProcess` 对带子目录的相对路径参数解析不可靠，故必须绝对化。
    """
    out: list[str] = []
    for part in plugin.command or []:
        # `-u` / `--model-dir` 这类开关**原样保留**：它们不是路径，拼成
        # `<插件目录>/-u` 之后 Python 会去开一个叫「-u」的文件（实测报
        # `can't open file '...\-u'`，看起来像「解释器找不到脚本」）。
        if part.startswith("-") or Path(part).is_absolute():
            out.append(part)
        else:
            out.append(str(plugin_dir / part))
    return out


def _plugin_env() -> dict[str, str]:
    """子进程环境：在继承的基础上**强制 UTF-8 模式**。

    ⚠️ **不设它的后果是静默的乱码，不是报错**。Windows 上 Python 未开 UTF-8 模式时
    `sys.stdin.encoding` 是 `gbk`、`errors` 是 `surrogateescape` —— 主进程写进去的
    UTF-8 字节被 GBK 解码成一串代理字符而不抛异常，`json.loads` 照样成功，于是
    「编码出来的是乱码文本的向量」：检索质量下降，日志一片绿。

    `PYTHONUTF8=1` 让子进程的 stdio 一律 UTF-8，与父进程 Popen 的 `encoding="utf-8"`
    成对；插件自己再 reconfigure 一次是纵深防御（不依赖启动方式）。

    两处共用（`_run_external` 的一次性调用 / `_spawn_locked` 的常驻进程）—— 只改一处
    的失效形态是「文件引擎好好的、向量插件全是乱码」。
    """
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    return env


class _PluginEngineAdapter:
    """将 EnginePlugin 适配为 EngineRegistry 兼容的 Engine 协议。

    execute 按 category 分派返回 DocResult/OCRResult/TranscriptResult，
    消费方（document/video/image）已按结构化字段消费，零改动。
    """

    def __init__(self, plugin: EnginePlugin, plugin_dir: Path, source_file: Path | None = None):
        self._plugin = plugin
        self._plugin_dir = plugin_dir
        # 声明文件（plugins/xxx.py）——子目录命名规范「声明文件同名的子目录」，
        # 依赖它解析子目录（name 可能与声明文件 stem 不同，如 pymupdf4llm_heavy.py 里 name=pymupdf4llm）
        self._source_file = source_file
        self.name = plugin.name
        self.license = plugin.license
        self.description = plugin.description
        # 从插件复制必要的 Engine 属性
        self.supported_modes = getattr(plugin, 'supported_modes', [])
        self.capabilities = getattr(plugin, 'capabilities', [])
        self.file_types = plugin.file_types  # capabilities fileTypeMap 动态归入依赖此属性
        # v1 元数据（capabilities 暴露）
        self.is_plugin = True
        self.display_name = plugin.display_name or plugin.name
        self.user_desc = plugin.user_desc or ""
        self.config_schema = plugin.config_schema or []
        self.namespace = plugin.namespace or "engine"
        self.api_version = plugin.api_version or PLUGIN_API_VERSION
        self.trusted = plugin.trusted
        # 插件自身配置（Phase 4 由 cfg["plugin_configs"] 填充；空 dict 时插件用默认值）
        self._plugin_config: dict = {}
        self._exec_mode = _resolve_exec_mode(plugin)

    # ── 插件配置（写一次，两处都拿到）──

    @property
    def plugin_config(self) -> dict:
        return self._plugin_config

    @plugin_config.setter
    def plugin_config(self, value: dict) -> None:
        """设置配置时**同步给插件实例**。

        两个消费方：执行路径读 `adapter.plugin_config`（塞进 stdin / 环境变量），
        **`precheck()` 在声明层跑**（设置页自检）—— 只有 adapter 有配置的话，自检
        只能拿写死的默认值判断，用户把模型 / 二进制放到配置指定的位置后会被报
        「缺文件」，而实际完全能用（`precheck` 的失效方向反过来也就是「说就绪、
        一跑就报错」）。
        """
        self._plugin_config = dict(value or {})
        try:
            self._plugin.plugin_config = self._plugin_config
        except Exception:  # noqa: BLE001 —— `__slots__` / 只读属性的插件：退回默认值
            pass

    def accept(self, file_path: str) -> bool:
        return self._plugin.accept(file_path)

    def run(self, file_path: str, **kwargs):
        """Engine 协议入口"""
        return self.execute(file_path, **kwargs)

    def execute(self, file_path: str, **kwargs):
        cancel_event = kwargs.pop("cancel_event", None)
        if self._exec_mode == "external":
            raw = self._run_external(file_path, kwargs, cancel_event)
            return _dispatch_result(self._plugin.category, _parse_stdout(raw), self.name)

        # ── inproc（trusted 例外）──
        # 插件代码跑在主进程里，异常会顺着调用链冒到 adapter 层，而那里只会
        # 留一句 `str(e)[:100]`（document.py 的兜底），traceback 全丢。边界放在
        # 这里才能同时做到：日志留住原始 traceback、用户看到带插件名的原因。
        #
        # ⚠️ **返回值类型也必须守**：插件返回 `None` 时，旧代码会包成
        # `{"text": None}` → DocResult(text=None) → 上层报「返回空结果」，
        # 读起来像「这份文件没内容」而不是「插件坏了」。这个错误必须当场说清。
        try:
            out = self._plugin.process(file_path, kwargs)
        except Exception as e:  # noqa: BLE001 —— 第三方代码，什么异常都可能有
            logger.exception("插件 %s 执行失败（进程内）", self.name)
            raise FatalError(
                f"插件 {self.name} 执行失败：{type(e).__name__}: {str(e)[:200]}"
            ) from e
        if isinstance(out, dict):
            payload = out
        elif isinstance(out, str):
            payload = {"text": out}
        else:
            raise FatalError(
                f"插件 {self.name} 返回了不支持的类型 {type(out).__name__}"
                f"（契约要求 str 或 dict）"
            )
        return _dispatch_result(self._plugin.category, payload, self.name)

    def precheck(self) -> Optional[str]:
        """转发插件的自检。异常兜成「不可用」—— 自检自己炸了，结论就是不可用。

        ⚠️ **兜底必须在这里、而不是指望每个消费方**：`GET /api/plugins` 与
        `vector_runtime` 都会调它，而插件是任意第三方 Python —— 其中一处的
        try/except 漏了，症状就是「打开设置页 500」，看起来像主程序坏了。
        """
        try:
            return self._plugin.precheck()
        except Exception as e:  # noqa: BLE001
            return f"插件自检失败：{type(e).__name__}: {e}"

    def _run_external(self, file_path: str, config: dict, cancel_event=None) -> str:
        """进程外执行：subprocess Popen 调用独立 venv，stdin 传 JSON config。

        stdin JSON: {file, cancel_file, config(插件自身配置), ...kwargs}
        argv 保留 `--input <path>`（v0 插件兼容）。
        cancel: 监视线程写标记文件 → 宽限 3s → kill（timeout 永远兜底）。
        """
        safe = {
            k: v for k, v in config.items()
            if isinstance(v, (str, int, float, bool, list, dict, type(None)))
        }
        safe["file"] = file_path
        # 插件自身配置进契约（plugin_configs → set_plugin_config 更新；空 dict 插件用默认值）
        safe["config"] = self.plugin_config
        cancel_file = ""
        if cancel_event is not None:
            # cancel_file 用绝对路径（子进程 cwd 与主程序不同）
            cancel_file = os.path.join(
                tempfile.gettempdir(),
                f"limo_cancel_{os.getpid()}_{threading.get_ident()}.flag",
            )
            safe["cancel_file"] = cancel_file

        # command 相对路径 → 基于插件目录解析为绝对路径（唯一实现在 `_resolve_command`）
        cmd = _resolve_command(self._plugin, self._plugin_dir)
        proc = subprocess.Popen(
            cmd + ["--input", file_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=str(self._plugin_dir),
            env=_plugin_env(),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        # 取消监视线程：cancel_event 置位 → 写标记 → 宽限 3s → 强杀
        stop = threading.Event()

        def _monitor():
            while not stop.is_set():
                if cancel_event and cancel_event.is_set():
                    try:
                        Path(cancel_file).write_text("cancel", encoding="utf-8")
                    except OSError:
                        pass
                    time.sleep(3)
                    if proc.poll() is None:
                        proc.kill()
                    return
                time.sleep(0.2)

        mon = threading.Thread(target=_monitor, daemon=True)
        mon.start()
        try:
            out, err = proc.communicate(json.dumps(safe), timeout=self._plugin.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            raise RuntimeError(
                f"Plugin {self.name} timed out after {self._plugin.timeout}s"
            ) from None
        finally:
            stop.set()
            if cancel_file:
                try:
                    os.unlink(cancel_file)
                except OSError:
                    pass
        if proc.returncode != 0:
            raise RuntimeError(
                err.strip() or f"Plugin exited with code {proc.returncode}"
            )
        return out


# ── 向量化插件适配（常驻子进程 + NDJSON over stdio）──

#: 子进程吐出 ready 行之前的等待上限。模型加载实测 3–5 秒，慢盘 / 首次冷读更久。
EMBED_READY_TIMEOUT = 30.0

#: 单次编码往返的等待上限。**不是「正常该多久」而是「多久算卡死」**：超时后会丢弃
#: 子进程（管道里残留的半条响应会让后续每一轮都错位）—— 宁可重起一次，也不留一个
#: 「形状合法但内容错位」的进程。
EMBED_REQUEST_TIMEOUT = 180.0

#: `idle` 档的默认空闲门槛（秒）。**与内置本地引擎 `DEFAULT_IDLE_RELEASE` 同值**：
#: 两者承载的是同一份使用节奏（低频、天级），门槛不同会让「换插件前后行为变了」
#: 且没人说得清为什么。相等由 `test_plugin_embedding.py` 的守卫钉住。
EMBED_IDLE_RELEASE = 600.0

#: `release()` 抢 `_proc_lock` 的等待上限（秒）。抢不到就返回 `False`（=「正在编码中，
#: 这次不释放」）。
#:
#: ⚠️ **不能无限等**：`release` 是同步方法，唯一调用链是 `vector_worker._do_release`
#: ← `_stop_worker`，而它跑在**事件循环线程**上。锁可能被一次编码往返持有到
#: `EMBED_REQUEST_TIMEOUT`（180 秒）—— 无超时的话，用户点一下关掉语义检索，整个后端
#: 就停摆三分钟（健康探测超时 → 顶栏「正在重试」），而真正的原因是「在等编码」。
#: 抢不到只是「这一轮不释放」，下一轮 `maybe_release` 还会再来。
RELEASE_LOCK_TIMEOUT = 5.0


class _PluginResidentAdapter(_PluginEngineAdapter):
    """常驻子进程 + NDJSON over stdio 的公共骨架（向量化 / 重排两族共用）。

    继承 `_PluginEngineAdapter` 只为复用**元数据装配**（`display_name` /
    `config_schema` / `plugin_config` / `_exec_mode` 那些，`enumerate_plugins` 与
    设置页都按这些字段读数）。`execute()` 被覆盖成显式错误 —— 这一族不吃文件。

    ⚠️ **进程生命周期与管道协议只有这一份**，不是「胶水可以各写一遍」：`id` 回传
    校验、非 JSON 行即丢进程、stderr 持续 drain（管道写满会让子进程阻塞在日志输出
    上，表现为「卡住」）、UTF-8 强制。抄两份的失效形态是「一族正常、另一族偶发
    错位 / 全是乱码」，而两边都不报错。子类只回答三件事：**请求长什么样**
    （`_roundtrip_locked` 里那次 `_send_locked`）、**响应里的业务字段怎么解释**
    （各自的 `_check_*`）、**ready 握手要校验什么**（`_verify_ready_locked`）。

    ⚠️ **一把锁串行化「进程生命周期 + 一次完整的写/读往返」**。用一把而不是两把：
    分成「生命周期锁 + IO 锁」时，`release` 杀掉进程的那一刻必须同时持有两把，而
    它们的获取顺序在往返与 `release` 之间相反 —— 那就是死锁。
    """

    #: 日志与错误文案里的类别名（「向量插件」/「重排插件」）。只影响措辞。
    KIND = "插件"

    #: 失败文案里的动作名（「编码」/「重排」）。拼成「{KIND} {name} {VERB}失败」。
    VERB = "调用"

    #: 子进程吐出 ready 行之前的等待上限（秒）。子类按模型量级覆盖 ——
    #: 22.8MB 的 bge-small 冷读 3–5 秒，544MB 的 reranker 是另一个量级。
    READY_TIMEOUT = 30.0

    def __init__(self, plugin: EnginePlugin, plugin_dir: Path,
                 source_file: Path | None = None):
        super().__init__(plugin, plugin_dir, source_file)
        self.model_id = getattr(plugin, "model_id", "") or plugin.name
        self.is_local = bool(getattr(plugin, "is_local", True))
        self._proc: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()
        self._next_id = 0
        self._last_used = 0.0
        self._stderr_tail: list[str] = []

    # ── Engine 协议：这一族引擎不接受文件 ──

    def execute(self, file_path: str, **kwargs):
        raise FatalError(
            f"插件 {self.name} 是{self.KIND}（category={self._plugin.category}），"
            f"不接受文件输入"
        )

    # ── 生命周期 ──

    async def warmup(self) -> None:
        """把模型预热到「第一次调用不用等加载」。

        ⚠️ 不预热的后果不是「慢」而是「第一次查询必定退回退化路径」（加载 3–5 秒 >
        `QUERY_TIMEOUT = 3.0` / `RERANK_TIMEOUT = 5.0`），而提示写的是「超时」。
        """
        if self._exec_mode == "external":
            await asyncio.to_thread(self._warm_external)
            return
        warm = getattr(self._plugin, "warmup", None)
        if warm is not None:
            await asyncio.to_thread(warm)

    def release(self, *, after_idle: float = EMBED_IDLE_RELEASE) -> bool:
        """空闲够久就释放模型，归还内存。返回「这次真的释放了吗」。

        ⚠️ **同步方法**（`vector_worker._do_release` 直接调，不 await）：真正的动作
        要么是「杀一个进程」（毫秒级），要么是「丢掉一个对象引用」（微秒级）。

        ⚠️ 外部模式下「释放」比内置引擎更彻底 —— 杀掉的进程把 Session、tokenizer、
        numpy 缓冲一起还回去。这也让三档策略（`never` / `idle` / `immediate`）的
        语义与内置引擎自然对齐，不需要第二套解释。

        ⚠️ **进程内模式必须转发**：不转发时插件写的 `release` 钩子永不被调用，而
        外部模式那份工作正常 —— 插件作者换个形态发布就发现「选常驻内存时它永远不还」。
        """
        if self._exec_mode != "external":
            rel = getattr(self._plugin, "release", None)
            if rel is None:
                return False          # 没实现钩子 = 无可释放（同 `warmup` 的 getattr）
            return bool(rel(after_idle=after_idle))
        if after_idle > 0 and self._proc is not None:
            if (time.monotonic() - self._last_used) < after_idle:
                return False
        # 抢锁**必须有上限**，理由见 `RELEASE_LOCK_TIMEOUT`。
        if not self._proc_lock.acquire(timeout=RELEASE_LOCK_TIMEOUT):
            return False
        try:
            if self._proc is None:
                return False
            if after_idle > 0 and (time.monotonic() - self._last_used) < after_idle:
                return False          # 等锁期间又被用过
            return self._kill_locked()
        finally:
            self._proc_lock.release()

    def precheck(self) -> Optional[str]:
        """返回**给用户看的**不可用原因（`None` = 就绪）。

        三层：子类自己的前置条件（`_precheck_local`）→ 「这个插件包装齐了吗」
        （外部模式才有的 venv / 依赖 / 文件）→ 插件自己的 `precheck()`。

        ⚠️ 会被 `GET /api/plugins` 与 `GET /api/search/status` 调用，**必须轻**：
        不 import、不起子进程。`probe_venv` 是纯文件系统检查（见 `core.deps`）。
        """
        reason = self._precheck_local()
        if reason:
            return reason
        if self._exec_mode == "external":
            reason = self._precheck_external()
            if reason:
                return reason
        try:
            return self._plugin.precheck()
        except Exception as e:  # noqa: BLE001 —— 自检自己炸了，结论就是不可用
            return f"插件自检失败：{type(e).__name__}: {e}"

    def _precheck_local(self) -> Optional[str]:
        """子类自己的前置条件（默认没有）。返回给用户看的原因。"""
        return None

    def _precheck_external(self) -> Optional[str]:
        modules = tuple(getattr(self._plugin, "requires_modules", ()) or ())
        pkg_dir = _plugin_subdir(self)
        if modules:
            reason = probe_venv(
                pkg_dir / ".venv", modules=modules,
                hint="在 设置 → 插件 里点「一键安装依赖」",
            )
            if reason:
                return reason
        missing = []
        for name in getattr(self._plugin, "requires_files", ()) or ():
            p = Path(name).expanduser()
            if not p.is_absolute():
                p = pkg_dir / p
            if not p.exists():
                missing.append(str(p))
        if missing:
            return "缺少必需文件：" + "、".join(missing)
        return None

    # ── 进程管理（调用方必须已持有 `_proc_lock`）──

    def _warm_external(self) -> None:
        with self._proc_lock:
            self._ensure_proc_locked()

    def _ensure_proc_locked(self) -> subprocess.Popen:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            return proc
        if proc is not None:
            # 进程自己死了（依赖缺失 / 模型文件坏 / 被外部杀掉）—— 先收残骸再起新的，
            # 否则下一轮又会去读一个已关闭的管道。
            self._kill_locked()
        self._spawn_locked()
        assert self._proc is not None      # _spawn_locked 失败会抛，不会返回 None
        return self._proc

    def _spawn_locked(self) -> None:
        cmd = _resolve_command(self._plugin, self._plugin_dir)
        # 插件配置走**环境变量**而不是首行请求：首行是 ready 握手，两边都按固定形状
        # 解析；把配置混进去会让「协议」多一条随插件变化的约定。文件引擎走的是
        # stdin 里的 `config` 字段（一次性调用，请求本身就是那条消息）。
        env = _plugin_env()
        if self.plugin_config:
            env["LIMO_PLUGIN_CONFIG"] = json.dumps(
                self.plugin_config, ensure_ascii=False
            )
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=str(self._plugin_dir),
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self._proc = proc
        # drain 线程**拿这个局部 list**而不是每次读 `self._stderr_tail`：重启时
        # （`_ensure_proc_locked` 收残骸 → `_spawn_locked`）旧进程的 drain 线程可能
        # 还活着几毫秒，用属性的话它的最后几行会串进新进程的尾部，错误消息里就出现
        # 「上一个进程的 stderr 配这一个进程的失败」。
        stderr_tail: list[str] = []
        self._stderr_tail = stderr_tail
        # stderr 必须**持续读走**：管道写满（Windows 约 64KB）会让子进程阻塞在日志
        # 输出上，表现为「卡住」，而真正的错误恰恰在那段没人读的内容里。
        threading.Thread(target=self._drain_stderr, args=(proc, stderr_tail), daemon=True).start()

        line = self._readline_locked(proc, self.READY_TIMEOUT)
        ready: Optional[dict] = None
        if line:
            try:
                parsed = json.loads(line)
                ready = parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                ready = None
        if ready is None or not ready.get("ready"):
            tail = "".join(self._stderr_tail[-5:]).strip()
            self._kill_locked()
            raise RuntimeError(
                f"{self.KIND} {self.name} 启动失败"
                + (f"：{ready.get('error')}" if isinstance(ready, dict) and ready.get("error") else "")
                + (f"（stderr: {tail[:300]}）" if tail else "（进程无输出）")
            )
        # 声明值与实际值不符必须当场报：写进库 / 记进实验之后口径不符是**静默**的
        # （表现为质量下降，而不是报错）。校验内容由子类决定。
        reason = self._verify_ready_locked(ready)
        if reason:
            self._kill_locked()
            raise RuntimeError(reason)
        self._last_used = time.monotonic()

    def _verify_ready_locked(self, ready: dict) -> Optional[str]:
        """握手校验。返回错误原因（调用方负责杀进程 + 抛）；`None` = 通过。"""
        return None

    def _kill_locked(self) -> bool:
        """杀掉当前子进程并清空句柄。返回「原本有进程可杀吗」。"""
        proc = self._proc
        self._proc = None
        if proc is None:
            return False
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001 —— 已经死了 / 拒绝访问，都只有「收尸失败」这一个后果
            pass
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # noqa: BLE001
                pass
        return True

    # ── 一次往返（调用方必须已持有 `_proc_lock`）──

    def _roundtrip_locked(self, proc: subprocess.Popen, payload: dict,
                          timeout: float) -> dict:
        """写一条请求、读一条响应，返回**已校验过协议**的 dict。

        这一段的每条分支都是「不校验也不报错，只是结果悄悄不对」：
        非 JSON 行留在管道里 → 后续每一轮都读到它；`id` 错位 → 读到上一条的结果
        而形状完全合法；`error` 分支不区分「单次失败」与「协议坏了」→ 好进程被
        无辜丢弃 / 坏进程被反复复用。
        """
        self._next_id += 1
        rid = self._next_id
        payload = dict(payload, id=rid)
        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            self._kill_locked()        # 下轮重起，别在坏管道上反复失败
            raise RuntimeError(f"{self.KIND} {self.name} 子进程不可用：{e}") from e

        line = self._readline_locked(proc, timeout)
        if not line:
            self._kill_locked()
            raise RuntimeError(
                f"{self.KIND} {self.name} {self.VERB}超时（{timeout:.0f}s 无响应）"
            )

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            # 非 JSON 行 = 协议破了。丢弃进程：留在管道里的后续内容同样不可信。
            self._kill_locked()
            raise RuntimeError(
                f"{self.KIND} {self.name} 返回了非 JSON 行：{line[:120]!r}"
            ) from None
        if not isinstance(msg, dict):
            self._kill_locked()
            raise RuntimeError(f"{self.KIND} {self.name} 返回的不是 JSON 对象")
        if msg.get("id") != rid:
            # 残留响应错位 —— 每一轮都会读到上一条的结果，而形状完全合法。
            self._kill_locked()
            raise RuntimeError(
                f"{self.KIND} {self.name} 响应错位"
                f"（请求 id={rid}、响应 id={msg.get('id')}）"
            )
        if msg.get("error"):
            # 单次失败（不是协议坏了）：进程留着，下一批继续用
            raise RuntimeError(f"{self.KIND} {self.name} {self.VERB}失败：{msg['error']}")
        self._last_used = time.monotonic()
        return msg

    def _readline_locked(self, proc: subprocess.Popen, timeout: float) -> str:
        """带超时地读一行。**超时返回空串**（调用方负责丢弃进程）。

        ⚠️ `readline()` 没有超时参数，而 Windows 上 `select` 不能用于管道 ——
        只能另起线程读、主线程等 Event。超时后那个线程仍卡在 readline 上，
        但它持的是**已死进程**的管道：调用方 kill 之后它会读到 EOF 自行退出。
        """
        box: dict[str, str] = {}
        done = threading.Event()

        def _read() -> None:
            try:
                box["line"] = proc.stdout.readline() if proc.stdout is not None else ""
            except Exception as e:  # noqa: BLE001
                box["line"] = ""
                box["err"] = f"{type(e).__name__}: {e}"
            finally:
                done.set()

        threading.Thread(target=_read, daemon=True).start()
        if not done.wait(timeout):
            return ""
        return box.get("line", "") or ""

    def _drain_stderr(self, proc: subprocess.Popen, stderr_tail: list[str]) -> None:
        """把子进程 stderr 读走并留最后 20 行（失败时拼进错误消息）。

        `stderr_tail` 由 `_spawn_locked` 传进来（**不是** `self._stderr_tail`）：旧进程
        的 drain 线程可能活过重启那一刻，用属性就会串到新进程的尾部。
        """
        try:
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_tail.append(line)
                if len(stderr_tail) > 20:
                    del stderr_tail[0]
        except Exception:  # noqa: BLE001 —— 进程被杀时这里抛什么都无关紧要
            pass


class _PluginEmbeddingAdapter(_PluginResidentAdapter):
    """把 `EmbeddingPlugin` 适配成 `EmbeddingEngine` 协议（`embed` + 三个可选钩子）。

    「常驻子进程 + NDJSON」那套全部在 `_PluginResidentAdapter`（向量化 / 重排两族
    共用）。这里只回答 embedding 独有的三件事：握手要校验 `dimension`、响应里是
    `vectors`、前置条件多一条「必须声明维度」。

    ⚠️ **进程生命周期由基类独占管理**：`warmup()` 拉起、`release()` 杀掉、
    `embed()` 按需重启。不把子进程交给 `subprocess.run` 的一次性模型，因为
    「每次冷启动 3–5 秒」正是查询侧超时的来源（见 `EmbeddingPlugin` 文档）。
    """

    # 措辞与协议两处都按族给定 —— 错误文案是用户唯一能看到的线索，
    # 「重排插件 编码失败」这种混搭会让人以为装错了族。
    KIND = "向量插件"
    VERB = "编码"
    READY_TIMEOUT = EMBED_READY_TIMEOUT

    def __init__(self, plugin: EnginePlugin, plugin_dir: Path,
                 source_file: Path | None = None):
        super().__init__(plugin, plugin_dir, source_file)
        self.dimension = int(getattr(plugin, "dimension", 0) or 0)
        self.capabilities = ["embedding"]

    def _precheck_local(self) -> Optional[str]:
        # 维度 0 写进库之后，余弦会在不同维度之间静默算错 —— 拦在建索引之前。
        if self.dimension <= 0:
            return f"插件 {self.name} 未声明 dimension（向量维度）"
        return None

    def _verify_ready_locked(self, ready: dict) -> Optional[str]:
        """握手校验：声明值与实际值不符必须当场报。

        写进库之后口径不符是**静默**的（表现为检索质量下降，而不是报错）。
        """
        got_dim, got_model = ready.get("dimension"), ready.get("model_id")
        if got_dim is not None:
            try:
                reported_dim = int(got_dim)
            except (TypeError, ValueError):
                # 非法值（`"512"` 之外的东西）不能让它冒成裸 ValueError —— 那样
                # 报的是「invalid literal for int()」，而真正的原因是插件写错了协议。
                return f"向量插件 {self.name} 报告的维度不是整数：{got_dim!r}"
            if reported_dim != self.dimension:
                return (
                    f"向量插件 {self.name} 报告的维度是 {got_dim}，"
                    f"声明的是 {self.dimension}"
                )
        if got_model is not None and self.model_id and got_model != self.model_id:
            return (
                f"向量插件 {self.name} 报告的模型是 {got_model}，"
                f"声明的是 {self.model_id}"
            )
        return None

    # ── 契约入口 ──

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """编码文本 → L2 归一化向量（顺序与入参一一对应）。

        契约 3（空输入返回空、**不发起任何调用**）在这里短路：连子进程都不拉起。
        """
        if not texts:
            return []
        batch = [str(t) for t in texts]
        if self._exec_mode == "external":
            vecs = await asyncio.to_thread(self._embed_external, batch)
        else:
            vecs = await asyncio.to_thread(self._embed_inproc, batch)
        return self._check_vectors(vecs, len(batch))

    # ── 编码 ──

    def _embed_external(self, texts: list[str]) -> list[list[float]]:
        with self._proc_lock:
            proc = self._ensure_proc_locked()
            # 协议层的校验（非 JSON / id 错位 / error 分支 / 超时丢进程）在基类，
            # 这里只解释 `vectors` 这个业务字段。
            msg = self._roundtrip_locked(proc, {"texts": texts}, EMBED_REQUEST_TIMEOUT)
            vectors = msg.get("vectors")
            if not isinstance(vectors, (list, tuple)):
                self._kill_locked()
                raise RuntimeError(f"向量插件 {self.name} 响应缺少 vectors 数组")
            return [list(v) if isinstance(v, (list, tuple)) else v for v in vectors]

    def _embed_inproc(self, texts: list[str]) -> list[list[float]]:
        """进程内（trusted）编码。异常包成 `FatalError`（留 traceback、带插件名）。"""
        try:
            vectors = self._plugin.embed(texts)
        except Exception as e:  # noqa: BLE001 —— 第三方代码，什么异常都可能有
            logger.exception("插件 %s 编码失败（进程内）", self.name)
            raise FatalError(
                f"插件 {self.name} 编码失败：{type(e).__name__}: {str(e)[:200]}"
            ) from e
        self._last_used = time.monotonic()
        if not isinstance(vectors, (list, tuple)):
            raise FatalError(
                f"插件 {self.name} 返回了不支持的类型 {type(vectors).__name__}"
                f"（契约要求 list[list[float]]）"
            )
        return [list(v) if isinstance(v, (list, tuple)) else v for v in vectors]

    def _check_vectors(self, vectors, n: int) -> list[list[float]]:
        """契约 1 与维度校验。**不补不猜**：少一条会让后续向量与文本整体错位，
        而错位在检索时表现为「结果莫名其妙」，不像错误。"""
        if len(vectors) != n:
            raise RuntimeError(
                f"向量插件 {self.name} 条数不符：送 {n} 条、回 {len(vectors)} 条"
            )
        out: list[list[float]] = []
        for i, vec in enumerate(vectors):
            try:
                row = [float(x) for x in vec]
            except (TypeError, ValueError) as e:
                raise RuntimeError(
                    f"向量插件 {self.name} 第 {i} 条不是数字序列：{e}"
                ) from e
            if self.dimension and len(row) != self.dimension:
                raise RuntimeError(
                    f"向量插件 {self.name} 第 {i} 条维度 {len(row)}，"
                    f"与声明的 {self.dimension} 不符"
                )
            out.append(row)
        return out


#: 重排插件子进程吐出 ready 行之前的等待上限（秒）。
#:
#: ⚠️ **不是 30 秒**（embedding 那个值）：这一类要加载的是 bge-reranker 一档的
#: 权重（数百 MB，冷读几十秒），22.8MB 的 bge-small 不在同一个量级。沿用 30 会
#: 让「第一次搜索就超时」，而提示写的是「启动失败」——用户不知道该等还是该改。
RERANK_READY_TIMEOUT = 180.0

#: 单次重排往返的等待上限（秒）。取得比编排层的 `RERANK_TIMEOUT = 5.0` 宽：
#: 编排层那个是**用户可感知的**时间预算（超了就退回融合顺序），这里是**协议层**
#: 的卡死判定。刻意不 `<` 5.0 —— 否则编排层永远先超时，子进程烂在管道里的半条
#: 响应没人收，下一轮起就错位。
RERANK_REQUEST_TIMEOUT = 15.0


class _PluginRerankAdapter(_PluginResidentAdapter):
    """把 `RerankPlugin` 适配成 `RerankEngine` 协议（`rerank` + 三个可选钩子）。

    「常驻子进程 + NDJSON」那套全部在 `_PluginResidentAdapter`。这里只回答重排
    独有的三件事：请求里同时带 query 与 candidates、响应里是 `results` 的
    `[[下标, 分数], ...]` 表、前置条件一条都没有（权重在插件自己手里）。

    ⚠️ **允许只返回前 k 条**（`top_n` 语义）。尾部由 `RerankSearcher` 按原序补齐
    —— 那一层负责「一个都不能丢」，这里负责「拿到的那几条下标是对的」。
    """

    KIND = "重排插件"
    VERB = "重排"
    READY_TIMEOUT = RERANK_READY_TIMEOUT

    def __init__(self, plugin: EnginePlugin, plugin_dir: Path,
                 source_file: Path | None = None):
        super().__init__(plugin, plugin_dir, source_file)
        self.max_documents = int(getattr(plugin, "max_documents", 0) or 0)

    # ── 契约入口 ──

    async def rerank(self, query: str,
                     documents: list[str]) -> list[tuple[int, float]]:
        """契约 2：空输入返回 `[]` 且**不发起任何调用**（连子进程都不拉起）。

        省的不是钱，是「一条必失败的请求」与「为它白加载一次模型」。
        """
        if not documents:
            return []
        batch = [str(d) for d in documents]
        q = str(query)
        if self._exec_mode == "external":
            rows = await asyncio.to_thread(self._rerank_external, q, batch)
        else:
            rows = await asyncio.to_thread(self._rerank_inproc, q, batch)
        return self._check_results(rows, len(batch))

    # ── 打分 ──

    def _rerank_external(self, query: str,
                         documents: list[str]) -> list[tuple[int, float]]:
        with self._proc_lock:
            proc = self._ensure_proc_locked()
            # 协议层的校验（非 JSON / id 错位 / error 分支 / 超时丢进程）在基类。
            msg = self._roundtrip_locked(
                proc,
                {"query": query, "documents": documents},
                RERANK_REQUEST_TIMEOUT,
            )
            results = msg.get("results")
            if not isinstance(results, (list, tuple)):
                self._kill_locked()
                raise RuntimeError(f"重排插件 {self.name} 响应缺少 results 数组")
            return list(results)

    def _rerank_inproc(self, query: str,
                       documents: list[str]) -> list[tuple[int, float]]:
        """进程内（trusted）打分。异常包成 `FatalError`（留 traceback、带插件名）。"""
        try:
            rows = self._plugin.rerank(query, documents)
        except Exception as e:  # noqa: BLE001 —— 第三方代码，什么异常都可能有
            logger.exception("插件 %s 重排失败（进程内）", self.name)
            raise FatalError(
                f"插件 {self.name} 重排失败：{type(e).__name__}: {str(e)[:200]}"
            ) from e
        self._last_used = time.monotonic()
        if not isinstance(rows, (list, tuple)):
            raise FatalError(
                f"插件 {self.name} 返回了不支持的类型 {type(rows).__name__}"
                f"（契约要求 list[tuple[int, float]]）"
            )
        return list(rows)

    def _check_results(self, rows, n: int) -> list[tuple[int, float]]:
        """契约 1：下标在 `[0, n)` 内且**互不重复**，分数是数字。

        ⚠️ **越界与重复都直接抛**（与 `rerank_dashscope` 同一条判据）：下标算错
        时**别人的分数会贴到这篇头上**，而结果列表看起来完全正常。抛出去之后
        整次重排降级为「沿用融合顺序」—— 那是可接受的；不抛则是一次**看不出来
        的错序**。条数少于 `n` 是合法的（契约 3），不在这里判。
        """
        out: list[tuple[int, float]] = []
        seen: set[int] = set()
        for pos, item in enumerate(rows):
            try:
                idx, score = int(item[0]), float(item[1])
            except (TypeError, ValueError, IndexError) as e:
                raise RuntimeError(
                    f"重排插件 {self.name} 第 {pos} 条不是 [下标, 分数]："
                    f"{type(e).__name__}: {e}"
                ) from e
            if not (0 <= idx < n):
                raise RuntimeError(
                    f"重排插件 {self.name} 返回的下标 {idx} 越界"
                    f"（候选共 {n} 条）"
                )
            if idx in seen:
                raise RuntimeError(
                    f"重排插件 {self.name} 返回了重复的下标 {idx}"
                )
            seen.add(idx)
            out.append((idx, score))
        return out


# ── 插件注册表（Phase 4 管理 API 的数据基础）──
# name → {adapter, meta, path, source, enabled, loaded}
#   enabled — config 期望状态（运行时 enable/disable 同步；前端开关位置）
#   loaded  — 是否已注册进 EngineRegistry（仅启动时决定；enabled != loaded → 待重启生效）
PLUGIN_REGISTRY: dict[str, dict] = {}


# ── 加载入口 ──

#: 三族插件的基类本体。**加一族只改这里**（下面的 `_PLUGIN_BASES` 由它派生）：
#: `init_plugins` 要排除基类本身不被当成插件实例化，`_peek_plugin_meta` 要认得
#: 声明类继承的是哪一族。两处各抄一份名单的失效形态不同但同样静默 ——
#: 排除名单漏了 = 多一个 `name=""` 的幽灵条目；识别集合漏了 = 「那一族插件被
#: 禁用后仍然加载」（peek 解析不出 name → 退回先执行再判）。
_PLUGIN_BASE_CLASSES: tuple[type, ...] = (EnginePlugin, EmbeddingPlugin, RerankPlugin)

#: 基类**名字**（AST 层只有名字）。由 `_PLUGIN_BASE_CLASSES` 派生，不手写。
_PLUGIN_BASES: frozenset[str] = frozenset(c.__name__ for c in _PLUGIN_BASE_CLASSES)

#: 基类名 → 该族默认 `category`。AST 读不到继承来的类属性，只能按基类回填；
#: 漏填的那一族在**禁用条目**里 category 是空串（前端落到「其他」分组），
#: 而重启启用后又跳到正确分组 —— 同一个插件两个位置。
_BASE_DEFAULT_CATEGORY: dict[str, str] = {
    "EmbeddingPlugin": "embedding",
    "RerankPlugin": "rerank",
}

#: `category` → 常驻子进程适配器。**不在这张表里的 category 一律走
#: `_PluginEngineAdapter`（doc 契约：`execute(file_path)`）**。
#:
#: ⚠️ 新增一族时这里和 `_PLUGIN_BASE_CLASSES` 必须**同批**改。只加基类不加这一条，
#: 用户把模板拷进 `{data_dir}/plugins/` 的那一刻它被以 doc 契约**静默注册成功**
#: （形状合法、零报错），而每次调用都在要求一个它永远不会收到的文件路径。
_RESIDENT_ADAPTERS: dict[str, type] = {
    "embedding": _PluginEmbeddingAdapter,
    "rerank": _PluginRerankAdapter,
}


def _base_name(node: ast.expr) -> str:
    """基类表达式的名字：`EnginePlugin` / `sdk.EnginePlugin` 都返回 `EnginePlugin`。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _peek_plugin_meta(py_file: Path) -> Optional[dict]:
    """**不执行插件代码**，从源码静态读出声明类上的字符串字面量属性（至少含 `name`）。

    只服务于一条路径：`plugin_states[name] is False` 时**跳过 `exec_module`**（P1-02）。
    插件名恰恰要执行完才知道，而「禁用」的全部意义就是别执行它的代码 —— 只能从源码读。

    ⚠️ 读不出来就返回 None，调用方退回「先执行、再判」的旧行为并记 debug：静态解析
    撑不住所有写法（动态拼 name、多级继承、`__slots__` 之类），**降级方向必须安全**
    （宁可多执行一个文件，也不能因为解析不了就漏装一个插件）。这条降级是**可见的**
    （有日志），不是静默的。
    """
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        # `EmbeddingPlugin` / `RerankPlugin` 都是 `EnginePlugin` 的**子类**，声明文件
        # 通常直接继承它们（`category` / `capabilities` 由基类给出）。漏掉某一支的
        # 失效形态是「那一族插件被禁用后仍然加载」—— 因为 peek 解析不出 name，调用方
        # 退回「先执行再判」，而禁用路径的全部意义正是不执行。
        bases = {_base_name(b) for b in node.bases}
        if not (bases & _PLUGIN_BASES):
            continue
        meta: dict = {}
        for stmt in node.body:
            if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)):
                meta[stmt.targets[0].id] = stmt.value.value
        if "name" in meta:
            # 继承来的类属性在这里读不到（只有 AST 上的字面量），按**基类**回填。
            # `EnginePlugin` 的默认是 `doc`（与基类本体一致），故不在表里。
            if "category" not in meta:
                # 按表的**键序**匹配而不是遍历 `bases`（set 顺序不确定）：多重继承时
                # 结果不该取决于哈希。
                for base_name_, default_cat in _BASE_DEFAULT_CATEGORY.items():
                    if base_name_ in bases:
                        meta["category"] = default_cat
                        break
            return meta
    return None


def init_plugins(config: dict):
    """扫描 {data_dir}/plugins/ 目录，加载所有插件。

    在 init_engines() 之后调用，确保内置引擎已注册。
    插件覆盖规则：同 category + name 插件覆盖内置引擎（用户优先）。

    门控：command→进程外（默认）；trusted→进程内；无 command 且未 trusted→拒绝（warning）。
    启用：plugin_states[name]==False → **连代码都不加载**（静态度出名字后整个跳过），
          仍保留 PLUGIN_REGISTRY 条目供前端展示与「重新启用」；
          未配置默认 enabled（向后兼容，现有插件不因新配置而禁用）。

    ⚠️ 「禁用」的语义是**不执行它的顶层代码**（P1-02）：插件是任意 Python，
    `exec_module` 那一刻它就能联网/写盘，与主程序同权限（`docs/plugins.md` 的告示）。
    此前的实现是先 `exec_module` 再读 enabled —— 于是「停用」只免注册、代码照跑，
    与前端「重启后停用」的文案不符。
    """
    plugins_dir = Path(config.get("data_dir", "")) / "plugins"
    if not plugins_dir.exists():
        return  # 目录不存在优雅跳过（打包版用户可能从未装插件）

    states = config.get("plugin_states", {}) or {}
    plugin_configs = config.get("plugin_configs", {}) or {}

    loaded = 0
    # 扫描范围：plugins/ 顶层 *.py + 一层子目录 *.py
    # 子目录支持 git clone 仓库布局（plugins/<repo>/<plugin>.py），
    # 排除 __pycache__ 等点开头目录 + 常见非插件基础设施文件
    _SKIP = {"setup.py", "conftest.py", "__init__.py"}
    py_files = [
        p for p in sorted(plugins_dir.glob("*.py")) + sorted(plugins_dir.glob("*/[!.]*.py"))
        if p.name not in _SKIP and not (p.name.startswith("test_") or p.name.endswith("_test.py"))
    ]
    for py_file in py_files:
        try:
            # ── 禁用即不加载（P1-02）──
            # 插件名要执行完才知道，而「禁用」的意义就是别执行它的代码 → 先从源码
            # 静态读名字，命中禁用名单就整个跳过（`exec_module` 一次都不发生）。
            # 读不出名字（None）则退回旧行为：执行后再判 enabled —— 见 `_peek_plugin_meta`。
            _peeked = _peek_plugin_meta(py_file)
            if _peeked is not None and states.get(_peeked["name"], True) is False:
                PLUGIN_REGISTRY[_peeked["name"]] = {
                    "adapter": None,        # 没有实例：这条路径的全部意义就是不实例化它
                    "meta": None,
                    "path": str(py_file),
                    "source": "user",
                    "category": _peeked.get("category", ""),
                    "enabled": False,
                    "loaded": False,
                    "reason": "disabled-by-config",
                }
                logger.info("Plugin disabled by config，代码未加载: %s (%s)",
                            _peeked["name"], py_file.name)
                continue
            if _peeked is None:
                logger.debug("插件元信息无法静态解析，改为执行后再判 enabled: %s", py_file.name)

            # 模块名带 parent（顶层="plugins"，子目录=仓库名）——同名 stem 不冲突
            spec = importlib.util.spec_from_file_location(
                f"_limo_plugin_{py_file.parent.name}_{py_file.stem}", py_file
            )
            if spec is None or spec.loader is None:
                logger.warning("Cannot load plugin %s: invalid spec", py_file.name)
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # 查找 EnginePlugin 子类
            # ⚠️ 基类本身也要排除：声明文件写 `from ... import EmbeddingPlugin` 之后，
            # 那个名字就住在模块命名空间里，而它同样是 `EnginePlugin` 的子类 ——
            # 不排除就会把**基类**当插件实例化（得到一个 name="" 的幽灵条目）。
            found = False
            for obj in vars(mod).values():
                if (
                    isinstance(obj, type)
                    and issubclass(obj, EnginePlugin)
                    and obj not in _PLUGIN_BASE_CLASSES
                ):
                    found = True
                    try:
                        instance = obj()
                    except Exception as e:
                        logger.warning(
                            "Plugin %s instantiation failed: %s", py_file.name, e
                        )
                        continue

                    # 适配器按**实例**的 category 分派而不是按基类：有的插件会继承
                    # `EnginePlugin` 却写 `category="embedding"`。
                    #
                    # ⚠️ 未列出的 category 一律落到 `_PluginEngineAdapter`（doc 契约）。
                    # 新增一族时**必须同批**加进这张表 —— 只交付基类而漏了这里，用户
                    # 把模板拷进 `{data_dir}/plugins/` 的那一刻，它被以 doc 契约
                    # **静默注册成功**（形状合法、零报错），而每次调用都会要求一个
                    # 文件路径。
                    adapter_cls = _RESIDENT_ADAPTERS.get(
                        getattr(instance, "category", ""), _PluginEngineAdapter
                    )
                    adapter = adapter_cls(instance, py_file.parent, py_file)
                    # 回填落盘配置（P1-02）：此前只有 `PUT /plugins/{name}/config` 会写
                    # `adapter.plugin_config`（内存），启动路径从不读 `plugin_configs`
                    # → 重启后插件拿到的恒是空 dict，用户保存的配置静默丢失（插件退回
                    # 默认值，而 UI 上那条 config 明明还在）。键缺失 = 用户没配过 = 空 dict。
                    adapter.plugin_config = dict(plugin_configs.get(instance.name) or {})

                    # 门控：无 command 且未 trusted → 拒绝（登记但不注册）
                    if adapter._exec_mode is None:
                        PLUGIN_REGISTRY[instance.name] = {
                            "adapter": adapter,
                            "meta": instance,
                            "path": str(py_file),
                            "source": "user",
                            "enabled": False,
                            "loaded": False,
                            "reason": "no-command-no-trusted",
                        }
                        logger.warning(
                            "Plugin %s rejected: 无 command 且未声明 trusted=true",
                            instance.name,
                        )
                        continue

                    enabled = states.get(instance.name, True)
                    PLUGIN_REGISTRY[instance.name] = {
                        "adapter": adapter,
                        "meta": instance,
                        "path": str(py_file),
                        "source": "user",
                        "enabled": enabled,
                        # loaded = 是否已注册进 EngineRegistry（仅在启动时决定；运行时 enable/disable 不改变它）
                        "loaded": enabled,
                    }
                    if enabled:
                        EngineRegistry.register(instance.category, instance.name, adapter)
                        logger.info(
                            "Plugin loaded: %s/%s (%s) [%s]",
                            instance.category,
                            instance.name,
                            adapter._exec_mode,
                            py_file.name,
                        )
                        loaded += 1
                    else:
                        logger.info(
                            "Plugin disabled by config: %s/%s", instance.category, instance.name
                        )

            if not found:
                logger.debug("No EnginePlugin subclass found in %s", py_file.name)

        except Exception as e:
            logger.warning("Plugin %s failed to load: %s", py_file.name, e)

    if loaded > 0:
        logger.info("Plugin loader: %d plugin(s) loaded from %s", loaded, plugins_dir)


# ── 管理 API 辅助（Phase 4 使用）──

# ── 一键安装依赖（进程外插件）──

# 国内镜像源（pip 默认直连 PyPI 慢/易失败；项目全局偏好）
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


def _plugin_subdir(adapter) -> Path:
    """插件执行子目录（含 engine.py / requirements.txt / .venv）。

    **优先从 `command` 推导**：那里的相对路径就是 `_run_external` 执行时真正
    解析的那个（`self._plugin_dir / part`），两处必须同源。推导不出才回退到
    声明文件 stem。

    ⚠️ 此前只看 stem，于是 `pymupdf4llm_plugin.py` + `pymupdf4llm/` 这种最常见
    的写法在「一键安装依赖」上**静默失效**：`requirements.txt` 按
    `pymupdf4llm_plugin/` 去找、找不到 → 接口返回 `no-requirements` → 前端
    **根本不显示安装按钮**。而插件本身照样能加载（command 是对的），所以从
    界面上看是「这个插件不需要依赖」。模板也因此一直没被发现。
    """
    plugin = getattr(adapter, "_plugin", None)
    for part in getattr(plugin, "command", None) or []:
        p = Path(part)
        if p.is_absolute() or len(p.parts) < 2:
            continue
        if any(seg in (".venv", "venv") for seg in p.parts):
            continue          # `xxx/.venv/Scripts/python.exe` —— 解释器，不是脚本
        return adapter._plugin_dir / p.parent

    # 回退：声明文件 stem（name 可与文件名不同，如 pymupdf4llm_heavy.py 里
    # name=pymupdf4llm，子目录却是 pymupdf4llm_heavy/）
    stem = adapter._source_file.stem if adapter._source_file else adapter.name
    return adapter._plugin_dir / stem


def _venv_python(pkg_dir: Path) -> Path:
    """venv 内 python 可执行文件路径（平台相关）"""
    if os.name == "nt":
        return pkg_dir / ".venv" / "Scripts" / "python.exe"
    return pkg_dir / ".venv" / "bin" / "python3"


def _venv_pip(pkg_dir: Path) -> Path:
    if os.name == "nt":
        return pkg_dir / ".venv" / "Scripts" / "pip.exe"
    return pkg_dir / ".venv" / "bin" / "pip"


def _venv_valid(pkg_dir: Path) -> bool:
    """venv 是否可用 —— 判据唯一实现在 `core.deps.probe_venv`（含 base python 校验）。

    ⚠️ **不要在这里再读一遍 `pyvenv.cfg`**：本函数与插件声明文件里的
    `precheck()`（`probe_venv`）回答的是同一个问题。两份实现的失效形态是
    「设置页说依赖已装、自检说没装」——同一个 venv，两个答案，而两边都不报错。

    历史（判据搬走前记在这里）：2026-08-06 修复失效 venv（uv 管理的 python 被
    升级/清理 → "No Python at ..."）视为未安装；2026-08-14 修复 WinError 448
    （不受信任装入点）→ 视为不可用，避免冒到 `/api/plugins` 变成 500。
    """
    venv = pkg_dir / ".venv"
    reason = probe_venv(venv)
    # 「压根没建」是常态（用户还没点一键安装），不打日志；「建过却不可用」才值得记
    if reason and venv.is_dir():
        logger.warning("Plugin %s venv 不可用（视为未安装）: %s", pkg_dir.name, reason)
    return reason is None


def _deps_installed(adapter) -> bool:
    """依赖是否已安装（venv python 存在且 base python 有效；失效 venv 视为未装）"""
    return _venv_valid(_plugin_subdir(adapter))


def _has_requirements(adapter) -> bool:
    """插件子目录是否存在 requirements.txt（进程外插件一键安装的前提）"""
    return (_plugin_subdir(adapter) / "requirements.txt").exists()


def _deps_installable(adapter) -> bool:
    """「一键安装依赖」是否适用于这个插件 —— 唯一判据。

    ⚠️ **前提是进程外**。这个能力做的事是「建 venv + pip install」，而进程内
    （trusted）插件根本不会从那个 venv 加载 —— 给它装依赖只会造出一个用不上的
    venv：界面显示「已安装」、插件 import 仍然失败，两边都不报错，用户只会
    觉得「怎么装都没用」。进程内插件的依赖必须在主环境里，这是它「零额外 venv」
    的另一面，不是遗漏。

    唯一消费方是 `enumerate_plugins` 的 `hasRequirements` 字段（决定前端是否渲染
    安装按钮）。`install_plugin_deps` 的入口校验**刻意只判 execMode** —— 在那条
    路径上「没有 requirements.txt」另有自己的状态（`no-requirements`），并进
    「进程内」会让两种完全不同的原因说同一句话。
    """
    return adapter._exec_mode == "external" and _has_requirements(adapter)


def _resolve_venv_base_python() -> Optional[str]:
    """建 venv 用的 base python 路径。

    源码运行 → 当前后端 python（sys.executable，版本与后端一致）。
    PyInstaller 打包 → sys.executable 是主程序 exe（不支持 -m venv），改试系统 python：
      Windows 优先 py launcher（官方安装自带），次选 python——均用 `-V` 验证可执行，
      防 Microsoft Store 别名（假 python.exe 启动会打开商店，建出损坏 venv）。
    都不可用 → None（调用方降级引导，不静默失败）。
    """
    if not getattr(sys, "frozen", False):
        return sys.executable
    candidates = ["py", "python"] if os.name == "nt" else ["python3", "python"]
    for cand in candidates:
        path = shutil.which(cand)
        if not path:
            continue
        try:
            subprocess.run(
                [path, "-V"], check=True, capture_output=True, text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return path
        except (subprocess.CalledProcessError, OSError):
            continue
    return None


def install_plugin_deps(name: str) -> dict:
    """一键安装插件依赖：建 venv + pip install（国内镜像源）。

    耗时操作（建 venv + 下载安装），调用方用 asyncio.to_thread 放线程池。
    失败显式化：返回 {status: "error", error: 原因}，不静默降级。
    """
    from app.core.errors import AppError

    entry = PLUGIN_REGISTRY.get(name)
    if not entry:
        raise AppError("PLUGIN_NOT_FOUND", f"插件不存在: {name}")
    adapter = entry["adapter"]
    if adapter is None:
        return {"status": "error", "python": "",
                "error": "插件未加载（可能已禁用），请先启用并重启后端"}
    # 进程内插件不从 venv 加载 —— 装到这里只会造出一个用不上的 venv。
    # ⚠️ 前端虽已按 `execMode === 'external'` 门控按钮，但**接口本身不能只靠前端**
    # （入口严）：否则 curl 一下就能在 `~/.limo/plugins/<name>/.venv` 里堆出
    # 一份插件永远不会加载的依赖，而界面上什么都不显示。
    if adapter._exec_mode != "external":
        return {
            "status": "inproc",
            "python": "",
            "error": "该插件在进程内运行，依赖需装在后端自身环境，不能一键安装",
            "hint": ("进程内插件（trusted=true）没有独立 venv；打包版无法追加依赖，"
                     "如需自带依赖请改用进程外模式（command + requirements.txt）"),
        }
    pkg_dir = _plugin_subdir(adapter)
    req = pkg_dir / "requirements.txt"
    if not req.exists():
        return {"status": "no-requirements", "python": ""}

    venv_py = _venv_python(pkg_dir)
    pip = _venv_pip(pkg_dir)

    try:
        # 1. 建 venv（base python 解析：源码=当前后端 / 打包=系统 python）
        if not _venv_valid(pkg_dir):
            # 失效 venv（base python 不可用，如 uv 管理的 python 被清理）→ 删除重建，
            # 用当前可用的 base python，避免「No Python at ...」无法自愈
            if venv_py.exists():
                shutil.rmtree(pkg_dir / ".venv", ignore_errors=True)
                logger.warning("Plugin %s: 失效 venv（base python 不可用）已删除，重建中", name)
            base_py = _resolve_venv_base_python()
            if not base_py:
                return {
                    "status": "no-python", "python": "",
                    "error": "未检测到可用的 Python，无法自动创建虚拟环境",
                    "hint": ("请先安装 Python（python.org/downloads），或手动执行：\n"
                             "cd ~/.limo/plugins/{name} && python -m venv .venv && "
                             ".venv/Scripts/pip install -r requirements.txt".format(name=name)),
                }
            logger.info("Plugin %s: creating venv (base=%s) at %s", name, base_py, pkg_dir / ".venv")
            subprocess.run(
                [base_py, "-m", "venv", str(pkg_dir / ".venv")],
                check=True, capture_output=True, text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        # 2. pip install（镜像源）
        logger.info("Plugin %s: pip install -r %s (mirror=%s)", name, req.name, PIP_MIRROR)
        proc = subprocess.run(
            [str(pip), "install", "-i", PIP_MIRROR, "-r", str(req)],
            capture_output=True, text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-500:]
            logger.warning("Plugin %s deps install failed: %s", name, tail[:200])
            return {"status": "error", "python": str(venv_py),
                    "error": tail or "pip install 失败，请查看日志"}
        logger.info("Plugin %s deps installed via %s", name, str(venv_py))
        return {"status": "ok", "python": str(venv_py)}
    except Exception as e:
        logger.warning("Plugin %s deps install exception: %s", name, e)
        return {"status": "error", "python": str(venv_py), "error": str(e)[:300]}


def set_plugin_config(name: str, config: dict) -> None:
    """更新插件配置到内存（下次执行 stdin 即含新 config；落盘由 router 负责）"""
    entry = PLUGIN_REGISTRY.get(name)
    if not entry:
        return
    adapter = entry.get("adapter")
    if adapter is None:
        # 被禁用的插件没有 adapter（代码未加载，P1-02）。配置**已由 router 落盘**，
        # 重启加载时会被 `init_plugins` 回填 —— 这里无需（也不能）更新内存。
        return
    adapter.plugin_config = dict(config)


def set_plugin_enabled(name: str, enabled: bool) -> None:
    """同步插件启用状态到内存（enable/disable 路由调用）。

    枚举结果立即反映期望状态（前端开关即时翻转）；引擎注册需重启生效。
    config 落盘由 router 负责。
    """
    entry = PLUGIN_REGISTRY.get(name)
    if entry:
        entry["enabled"] = bool(enabled)


def enumerate_plugins() -> list[dict]:
    """枚举所有已发现插件（含门控拒绝/禁用的），按 name 排序。

    返回管理 API 友好的 dict 列表（前端按 category 分组渲染）。

    ⚠️ 被 `plugin_states` 禁用的插件**没有 adapter**（P1-02：整个文件没执行，见
    `init_plugins`）——能力 / schema / 依赖状态一律给空值：那些信息只能在执行过模块
    之后才知道，而这条路径的全部意义**就是不执行它**。保住的是那条闭环：
    它仍出现在列表里、能点「启用」、重启后恢复正常信息。
    """
    result = []
    for name, entry in PLUGIN_REGISTRY.items():
        adapter = entry.get("adapter")
        if adapter is None:
            result.append({
                "name": name,
                "namespace": "engine",
                "category": entry.get("category", ""),
                "displayName": name,
                "userDesc": "",
                "license": "",
                "fileTypes": [],
                "configSchema": [],
                "path": entry.get("path", ""),
                # enabled / pendingRestart 仍走同一套语义：点「启用」后应为
                # enabled=True + pendingRestart=True（重启才真正加载）
                "enabled": entry.get("enabled", False),
                "loaded": entry.get("loaded", False),
                "pendingRestart": entry.get("enabled", False) != entry.get("loaded", False),
                "config": {},
                "reason": entry.get("reason", ""),
                "execMode": None,
                "hasRequirements": False,
                "depsInstalled": False,
                # `None` = **无法判断**（代码没加载，自检也就无从调用）——
                # 与 `False`（自检说不可用）刻意分开：前端只渲染 false，
                # 「问不到」不该长成一条点不动的「不可用」。
                "ready": None,
                "precheckReason": "",
            })
            continue
        # 自检（`EnginePlugin.precheck`）。放在这里而不是路由里：它是插件卡片
        # 「为什么用不了」的唯一数据源，任何枚举方（含将来的 CLI）都该拿到同一份。
        precheck_reason = adapter.precheck() or ""
        result.append({
            "name": name,
            "namespace": adapter.namespace,
            "category": adapter._plugin.category,
            "displayName": adapter.display_name,
            "userDesc": adapter.user_desc,
            "license": adapter.license,
            "fileTypes": adapter.file_types,
            "configSchema": adapter.config_schema,
            "path": entry.get("path", ""),
            "enabled": entry.get("enabled", False),
            # loaded = 运行时是否已注册；enabled != loaded → 需重启才生效（前端显示待重启徽章）
            "loaded": entry.get("loaded", False),
            "pendingRestart": entry.get("enabled", False) != entry.get("loaded", False),
            "config": adapter.plugin_config,
            "reason": entry.get("reason", ""),
            "execMode": adapter._exec_mode,
            "ready": precheck_reason == "",
            "precheckReason": precheck_reason,
            # 依赖一键安装状态：仅**进程外 + 有 requirements.txt** 才适用
            # （判据见 `_deps_installable`）
            "hasRequirements": _deps_installable(adapter),
            "depsInstalled": _deps_installed(adapter),
        })
    result.sort(key=lambda p: (p["category"], p["name"]))
    return result


def uninstall_plugin(name: str, cfg: dict) -> list[str]:
    """卸载插件：删除 {data_dir}/plugins/ 下同名 .py + 同名目录（含 venv/模型）。

    路径穿越防护：resolve() 后校验在插件目录内，杜绝 ../ 与绝对路径逃逸。
    v1 单目录下插件全在 ~/.limo/plugins/，统一路径校验即可，无 source 区分。
    Windows 删除运行中 venv 可能占用失败 → 调用方捕获异常给前端反馈。
    """
    from app.core.errors import AppError

    entry = PLUGIN_REGISTRY.get(name)
    if not entry:
        raise AppError("PLUGIN_NOT_FOUND", f"插件不存在: {name}")

    user_dir = Path(cfg.get("data_dir", "")) / "plugins"
    base = user_dir.resolve()

    removed = []
    for p in [user_dir / f"{name}.py", user_dir / name]:
        if not p.exists():
            continue
        target = p.resolve()
        if base != target and base not in target.parents:
            raise AppError("PLUGIN_PATH_OUTSIDE", f"插件路径越界，拒绝删除: {target}")
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)
        removed.append(str(p))

    PLUGIN_REGISTRY.pop(name, None)
    return removed
