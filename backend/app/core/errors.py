"""
分层异常体系
参照 system-design-spec.md 八.1 + BiliNote demo 错误分类
"""

import errno
import functools
import logging
import time

_log = logging.getLogger(__name__)

# AppError status_code 的默认值哨兵——区分「未显式指定（默认 400）」与「显式传 400」。
# main.py 异常处理器：未显式指定且 recoverable → 200；显式指定则必须尊重（含 400）。
_UNSET = object()


# ── 重试策略标记（对齐 BiliNote demo/pipeline.py）──

class RetryableError(Exception):
    """可重试的临时错误（网络超时、服务端5xx等），装饰器会自动指数退避重试"""


class FatalError(Exception):
    """不可重试的致命错误（配置缺失、文件不存在等），直接抛出"""


def retry(max_attempts: int = 3, base_delay: float = 2.0):
    """装饰器：对 RetryableError 做指数退避重试，FatalError 直接抛出。

    对齐 BiliNote demo/pipeline.py:38-55
    """
    def deco(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_err = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except FatalError:
                    raise
                except RetryableError as e:
                    last_err = e
                    if attempt < max_attempts:
                        delay = base_delay * (2 ** (attempt - 1))
                        _log.warning("%s 第%d次失败，%.1fs后重试: %s",
                                     func.__name__, attempt, delay, e)
                        time.sleep(delay)
            raise last_err
        return wrapper
    return deco


class AppError(Exception):
    """所有异常的基类"""
    def __init__(self, code: str, message: str, detail: str = "",
                 recoverable: bool = True, status_code: int | object = _UNSET):
        self.code = code
        self.message = message
        self.detail = detail
        self.recoverable = recoverable
        # 区分「显式指定 status_code」与「默认 400」——main.py 异常处理器据此决定
        # recoverable 时是否压成 200。用 sentinel 而非「≠400」判断，否则显式传
        # status_code=400 + recoverable=True（如 export_custom）会被误压成 200
        # （评审 H1：前端 `if (!r.ok)` 把 200 当成功 → 下载损坏 zip）。
        if status_code is _UNSET:
            self.status_code = 400
            self._explicit_status = False
        else:
            self.status_code = int(status_code)
            self._explicit_status = True
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
            "recoverable": self.recoverable,
        }


def http_status(exc: AppError) -> int:
    """`AppError` → HTTP 状态码的**唯一判据**（`main.py` 的异常处理器与测试共用）。

    显式指定过 `status_code`（`_explicit_status`，**含显式 400**）时尊重它；未显式
    时 `recoverable → 200`（「尚未生成」是预期业务状态，压成 200 可免浏览器
    DevTools 显示网络错误）。显式 400 必须返回 400 —— 用「≠默认 400」判断会让
    `status_code=400 + recoverable=True`（如 `export_custom`）被误压成 200，
    前端 `if (!r.ok)` 当成功 → 下载到损坏 zip（评审 H1）。

    ⚠️ 抽出来是因为测试要造「与生产一致」的 app：复刻一份表达式就是模式 #54 的
    入口，而分叉的症状（测试与线上状态码不同）不抛错、只是静默判错。
    """
    return exc.status_code if exc._explicit_status else (
        200 if exc.recoverable else exc.status_code)


# -- 获取层 --

class FetchError(AppError):
    """获取层异常"""
    @classmethod
    def network(cls, url: str, detail: str = ""):
        return cls("FETCH_NETWORK", f"无法访问 {url}，检查链接或网络", detail)

    @classmethod
    def cookie_expired(cls, platform: str, detail: str = ""):
        return cls("FETCH_COOKIE_EXPIRED", f"{platform} 登录已过期，请重新粘贴 Cookie", detail)

    @classmethod
    def unsupported(cls, detail: str = ""):
        return cls("FETCH_UNSUPPORTED", "该格式暂不支持", detail, recoverable=False)


# -- 转录层 --

class TranscribeError(AppError):
    """转录层异常"""
    @classmethod
    def asr_oom(cls, detail: str = ""):
        return cls("TRANS_ASR_OOM", "本地 ASR 内存不足，已降级为纯字幕", detail)

    @classmethod
    def vl_quota(cls, detail: str = ""):
        return cls("TRANS_VL_QUOTA", "视觉识别 API 额度用尽，已跳过画面描述", detail)

    @classmethod
    def ocr_low_confidence(cls, detail: str = ""):
        return cls("TRANS_OCR_LOW_CONFIDENCE", "文字识别置信度低，结果仅供参考", detail, recoverable=False)

    @classmethod
    def empty(cls, detail: str = ""):
        return cls("TRANS_EMPTY", "正文提取为空，可查看原始文件", detail, recoverable=False)


# -- 合成层（多篇文档 → 对比综述）--

class SynthesisError(AppError):
    """多篇文档合成异常。

    全部是**预期业务状态**（用户选得太少 / 源被删 / 模型窗口不够），故一律
    `recoverable=True` 且**不显式传 status_code**——按模式 #36，它们应走
    200 + 结构化错误事件，而不是 500。
    """

    @classmethod
    def window_too_small(cls, context_window: int, required: int, detail: str = ""):
        return cls(
            "SYNTH_WINDOW_TOO_SMALL",
            f"当前模型上下文窗口（{context_window}）不足以做多文档综述，"
            f"需要 {required} 以上。请换一个窗口更大的模型。",
            detail,
        )

    @classmethod
    def too_few_sources(cls, detail: str = ""):
        return cls("SYNTH_TOO_FEW_SOURCES", "请至少选择 2 篇文档再合并", detail)

    @classmethod
    def source_missing(cls, missing: list[str], detail: str = ""):
        return cls(
            "SYNTH_SOURCE_MISSING",
            f"有 {len(missing)} 篇源文档已不存在，请刷新文档库后重试",
            detail or ", ".join(missing),
        )

    @classmethod
    def source_empty(cls, titles: list[str], detail: str = ""):
        return cls(
            "SYNTH_SOURCE_EMPTY",
            f"有 {len(titles)} 篇源文档正文为空，无法参与合成",
            detail or ", ".join(titles),
        )

    @classmethod
    def too_many_calls(cls, estimated: int, limit: int, detail: str = ""):
        """预估调用数超上限。⚠️ 在**任何计费调用之前**抛出——让它跑到整体超时
        才发现，意味着钱已经花掉了（设计 §17.5 B5）。"""
        return cls(
            "SYNTH_TOO_MANY_CALLS",
            f"这次合成预计需要 {estimated} 次模型调用，超过上限（{limit}）。"
            f"请减少篇数，或换一个上下文窗口更大的模型（减少分块）。",
            detail,
        )

    @classmethod
    def not_enough_notes(cls, available: int, excluded: list[str], detail: str = ""):
        """`source_mode="notes"` 下，有笔记的篇不足 2 篇（设计 §19.4）。

        ⚠️ 走 AppError 而非静默截断：原料模式是用户显式选的，但**没看预览就点了
        合成**的用户仍必须知道为什么没做成。
        """
        return cls(
            "SYNTH_NOT_ENOUGH_NOTES",
            f"所选文档中只有 {available} 篇已有笔记，不足以做对比（至少 2 篇）。"
            f"请先为这些文档生成笔记，或改用「原文」作为原料。",
            detail or ", ".join(excluded),
        )


# -- 生成层 --

class GenerateError(AppError):
    """生成层异常"""
    @classmethod
    def api_timeout(cls, detail: str = ""):
        return cls("GEN_API_TIMEOUT", "AI 服务响应超时，已重试 3 次，建议稍后重试", detail)

    @classmethod
    def api_error(cls, detail: str = ""):
        return cls("GEN_API_ERROR", "AI 服务返回错误", detail)


# -- 存储层 --

class StoreError(AppError):
    """存储层异常"""
    @classmethod
    def disk_full(cls, detail: str = ""):
        return cls("STORE_DISK_FULL", "磁盘空间不足，请清理后重试", detail)

    # ⚠️ 曾有 `index_corrupt`（"索引文件损坏，正在自动重建"），全仓零调用 —— 已删。
    # 它的文案承诺的正是 `Store._load_index` 现在真正做的事（备份 + 全盘重建 + warning），
    # 而作为一个 `recoverable=False` 的 AppError，抛出去只会**阻止启动**，与自愈互斥。
    # 重建成没成、损失了什么，都从 `_load_index` 的 warning 里看。

    @classmethod
    def write_failed(cls, op: str, exc: OSError):
        """把 OSError 按**真实原因**分类 —— 而不是一律说「磁盘空间不足」。

        ⚠️ 粗细在这里是有代价的：权限被拒 / 文件被占用 / 路径过长三种失败与磁盘空间
        毫无关系，用户照「请清理磁盘」去行动会发现清了也没用，而真相就在
        `exc.errno` / `exc.winerror` 里。Windows 上「文件被占用」是 WinError 32
        （共享冲突），同时 `errno` 被一并给成 `EACCES`(13) —— 只看 errno 会把它误判成
        「没有权限」，故 **winerror 优先**。
        """
        winerr = getattr(exc, "winerror", None)
        code = exc.errno
        detail = f"{op}: {type(exc).__name__}: {exc}"
        if code == errno.ENOSPC:
            return cls("STORE_DISK_FULL", "磁盘空间不足，请清理后重试", detail)
        if winerr == 32 or code in (errno.EBUSY, errno.EAGAIN):
            return cls("STORE_FILE_BUSY", "文件正被其他程序占用，请关闭后重试", detail)
        if winerr == 206 or code == errno.ENAMETOOLONG:
            return cls("STORE_PATH_TOO_LONG",
                       "文件路径过长，请缩短标题或更换数据目录位置", detail)
        if code in (errno.EACCES, errno.EPERM):
            return cls("STORE_ACCESS_DENIED",
                       "没有写入权限，请检查数据目录权限或关闭占用它的程序", detail)
        return cls("STORE_WRITE_FAILED",
                   "写入数据目录失败，请检查该目录是否可用", detail)
