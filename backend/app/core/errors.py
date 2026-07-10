"""
分层异常体系
参照 system-design-spec.md 八.1 + BiliNote demo 错误分类
"""

import functools
import logging
import time

_log = logging.getLogger(__name__)


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
                 recoverable: bool = True, status_code: int = 400):
        self.code = code
        self.message = message
        self.detail = detail
        self.recoverable = recoverable
        self.status_code = status_code
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
            "recoverable": self.recoverable,
        }


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

    @classmethod
    def index_corrupt(cls, detail: str = ""):
        return cls("STORE_INDEX_CORRUPT", "索引文件损坏，正在自动重建",
                   detail, recoverable=False)
