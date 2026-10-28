"""arXiv 适配器 —— 论文摘要页/PDF 链接 → 走文档引擎出全文。

为什么单独一个适配器：arXiv 的两种链接形态都**不被任何现有适配器接住**
—— `/abs/2401.12345`（摘要页）没有扩展名，`/pdf/2401.12345v1` 也没有
（真正的文件名是 `2401.12345v1.pdf`，而用户从浏览器复制到的往往是前者）。
按网页导入会拿到一个「摘要 + 侧边栏」的页面，按 PDF 导入则需要用户自己找到直链。

⚠️ **`accept()` 必须是同步且纯正则的**：适配器路由在每个 URL 上都会调它，
在这里发网络请求等于给每个导入加一次网络往返。

⚠️ **必须注册在 `DocumentAdapter` 之前**：`https://arxiv.org/pdf/2401.12345v1.pdf`
这种带 `.pdf` 的形态会被 DocumentAdapter 的 `accept()` 抢走 —— 那一版能跑，但
`/abs/` 与无扩展名的 `/pdf/` 就漏了。注册顺序在 `adapters/__init__.py` 里。

⚠️ **效果是「与现有 URL 导入 PDF 行为一致」**，不是「PDF 专属能力全部可用」：
现有 URL 导入 PDF 同样**不持久化源文件**（`document.py::_download_url` 下到
tempfile 既不保存也不清理），所以依赖原始文件的那部分能力（PDF 直传、重新解析）
在这里同样受限。
"""

import logging
import re
import shutil
import tempfile
from pathlib import Path

from app.core.errors import FatalError
from app.core.types import PipelineInput, AdapterOutput, InputType, ProgressEvent
from app.core.protocols import PipelineAdapter

logger = logging.getLogger(__name__)

#: 两种入口：摘要页 `/abs/<id>` 与 PDF `/pdf/<id>`。
#: `<id>` 形如 `2401.12345` / `2401.12345v2` / 老式 `math.AG/0601001`（前者为主，
#: 老式也顺手接住 —— 正则成本为零）。⚠️ 允许尾随 `.pdf`、尾随 `/`、以及查询串
#: （从搜索结果里复制的链接常带 `?context=cs`）。
_ARXIV_RE = re.compile(
    r"^https?://(?:www\.|export\.)?arxiv\.org/(?:abs|pdf)/"
    r"([A-Za-z\-]+(?:\.[A-Za-z\-]+)?/\d{7}|\d{4}\.\d{4,5})(v\d+)?"
    r"(?:\.pdf)?/?(?:\?[^#]*)?$",
    re.IGNORECASE,
)


def parse_arxiv_id(url: str) -> str:
    """从 arXiv URL 抽出论文 id（含版本号）。

    ⚠️ **只认 arxiv.org 本身的两种路径**，不认 `xxx.arxiv.org` 的镜像 / `huggingface`
    的 arXiv 镜像 —— 那些站点的 PDF 直链形态各不相同，瞎猜只会拿到 404 页面。

    下面这些不该被接住（返回空）：
      - `https://arxiv.org/list/cs.AI/recent`（列表页，不是论文）
      - `https://arxiv.org/help/api`（文档页）
      - `https://example.com/abs/2401.12345`（别人的站点）
    """
    m = _ARXIV_RE.match((url or "").strip())
    if not m:
        return ""
    return f"{m.group(1)}{m.group(2) or ''}"


class ArxivAdapter(PipelineAdapter):
    name = "arxiv"

    def accept(self, input: PipelineInput) -> bool:
        """⚠️ 同步 + 纯正则，**不发任何网络请求**。"""
        if input.type != InputType.URL:
            return False
        return bool(parse_arxiv_id(input.url or ""))

    async def adapt(self, input: PipelineInput,
                    on_progress=None,
                    engine_config: dict | None = None,
                    ) -> AdapterOutput:
        url = input.url or ""
        arxiv_id = parse_arxiv_id(url)
        if not arxiv_id:
            # 走不到这里（accept 已拦），但显式兜底比静默拿错 PDF 好
            raise FatalError(f"不是可识别的 arXiv 链接: {url[:120]}")

        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
        if on_progress:
            on_progress(ProgressEvent(phase="download", status="running",
                                      message=f"下载 arXiv 论文: {arxiv_id}"))

        # ⚠️ 用 safe_get 而不是裸 requests.get —— 与 document.py 同款理由：
        # 公网 URL 302 到 127.0.0.1 就绕过了首跳校验。
        from app.utils.url_security import safe_get

        tmp_dir = Path(tempfile.mkdtemp(prefix="arxiv_dl_"))
        dest = tmp_dir / f"{arxiv_id.replace('/', '_')}.pdf"
        try:
            try:
                resp = safe_get(pdf_url, timeout=120, stream=True,
                                headers={"User-Agent": "LiMo/1.0 (arxiv importer)"})
                resp.raise_for_status()
            except ValueError as e:
                # 安全校验失败不是「网络不好」，分开报（同 document.py）
                raise RuntimeError(f"URL 安全校验失败: {str(e)[:150]}") from e
            except Exception as e:
                raise RuntimeError(f"arXiv 下载失败: {str(e)[:150]}") from e
            dest.write_bytes(resp.content)
            logger.info("arXiv 下载: %s → %.1fKB", arxiv_id, dest.stat().st_size / 1024)

            if on_progress:
                on_progress(ProgressEvent(phase="download", status="done"))

            # 委托给 DocumentAdapter —— 引擎链、子场景解析、图片提取**零复制**
            from app.adapters.document import DocumentAdapter

            out = await DocumentAdapter().adapt(
                PipelineInput(type=InputType.FILE, file_path=str(dest)),
                on_progress=on_progress,
                engine_config=engine_config,
            )
        finally:
            # 源 PDF 不落库（与现有 URL 导入 PDF 一致），临时目录用完即删。
            # ⚠️ 放在 `finally` 里：失败路径同样要清，否则每失败一次留一个 200KB 目录。
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # 补上 arXiv 自己的标识 —— 导入后文档标题来自 PDF 元数据，未必含编号
        if not out.metadata.source_title:
            out.metadata.source_title = f"arXiv {arxiv_id}"
        return out
