"""
EPUB 电子书引擎 — 自研容器（epub_container，纯标准库）提取章节+图片+元数据

2026-08-26 起：ebooklib（AGPL-3.0）已彻底移除，仅用自研解析器（失败显式报错）。

转录层：将 EPUB 转为结构化 Markdown（章节层级 + 图片文件引用 + 机械骨架）
对标 PyMuPDF/DocxEngine，遵循 Engine → DocResult 协议。
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.core.protocols import Engine

logger = logging.getLogger(__name__)


@dataclass
class EpubResult:
    text: str
    pages: int = 1
    engine: str = ""
    is_scanned: bool = False
    scanned_pages: int = 0
    epub_metadata: dict | None = None


# ── 骨架提取规则（纯机械，不经过 LLM）──

# 首段最少取多少字
SKELETON_HEAD_CHARS = 300
# 末段最少取多少字
SKELETON_TAIL_CHARS = 200


def _html_to_markdown(html_content: str, media_dir: str, img_idx: int,
                      manifest_items: dict) -> tuple[str, int]:
    """将 EPUB 章节 HTML 转为 Markdown 文本。

    提取：标题层级、段落文本、加粗/斜体、列表、表格、图片引用。
    返回 (markdown_text, new_img_idx)。
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        # 无 BeautifulSoup → 回退纯文本提取
        import re
        text = re.sub(r'<[^>]+>', '', html_content)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip(), img_idx

    soup = BeautifulSoup(html_content, 'html.parser')

    # 移除 script/style 标签
    for tag in soup(['script', 'style', 'nav', 'footer']):
        tag.decompose()

    lines: list[str] = []
    _walk(soup, lines, media_dir, img_idx, manifest_items)
    return '\n\n'.join(lines).strip(), img_idx


def _walk(element, lines: list[str], media_dir: str,
          img_idx: int, manifest_items: dict) -> int:
    """递归遍历 HTML 元素树，转为 Markdown 行。"""
    from bs4 import NavigableString, Tag

    for child in element.children:
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                lines.append(text)
            continue

        if not isinstance(child, Tag):
            continue

        tag_name = child.name.lower() if child.name else ''

        # 标题
        if tag_name in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            level = int(tag_name[1])
            text = child.get_text().strip()
            if text:
                lines.append(f"{'#' * level} {text}")

        # 段落
        elif tag_name == 'p':
            text = child.get_text().strip()
            if text:
                # 保留加粗/斜体内联标记
                for strong in child.find_all(['strong', 'b']):
                    s_text = strong.get_text()
                    if s_text:
                        text = text.replace(s_text, f"**{s_text}**")
                for em in child.find_all(['em', 'i']):
                    e_text = em.get_text()
                    if e_text:
                        text = text.replace(e_text, f"*{e_text}*")
                lines.append(text)

        # 图片
        elif tag_name == 'img':
            src = child.get('src', '')
            alt = child.get('alt', 'img')
            if src:
                resolved = _resolve_image(src, manifest_items)
                if resolved:
                    ext = resolved.get('ext', 'png')
                    if media_dir:
                        fname = f"img_{img_idx:04d}.{ext}"
                        img_idx += 1
                        (Path(media_dir) / fname).write_bytes(resolved['data'])
                        lines.append(f"![{alt or fname}]({fname})")
                    else:
                        import base64
                        b64 = base64.b64encode(resolved['data']).decode()
                        lines.append(f"![{alt or 'img'}](data:image/{ext};base64,{b64})")

        # 列表
        elif tag_name in ('ul', 'ol'):
            for li in child.find_all('li', recursive=False):
                text = li.get_text().strip()
                if text:
                    prefix = '- ' if tag_name == 'ul' else '1. '
                    lines.append(f"{prefix}{text}")

        # 表格
        elif tag_name == 'table':
            table_md = _table_to_md(child)
            if table_md:
                lines.append(table_md)

        # 引用
        elif tag_name == 'blockquote':
            text = child.get_text().strip()
            if text:
                for line in text.split('\n'):
                    lines.append(f"> {line.strip()}")

        # 其他块级元素 — 递归处理
        elif tag_name in ('div', 'section', 'article', 'main', 'body',
                           'header', 'aside', 'figure', 'figcaption'):
            img_idx = _walk(child, lines, media_dir, img_idx, manifest_items)

        # 内联元素 — 提取文字
        elif tag_name in ('span', 'a', 'code', 'pre', 'sub', 'sup', 'small',
                           'label', 'cite', 'abbr', 'time'):
            text = child.get_text().strip()
            if text:
                lines.append(text)

    return img_idx


def _table_to_md(table) -> str | None:
    """HTML table → Markdown table"""
    rows = table.find_all('tr')
    if not rows:
        return None
    md_rows: list[str] = []
    for i, tr in enumerate(rows):
        cells = tr.find_all(['th', 'td'])
        if not cells:
            continue
        cell_texts = [c.get_text().strip().replace('\n', ' ') for c in cells]
        md_rows.append('| ' + ' | '.join(cell_texts) + ' |')
        if i == 0:
            md_rows.append('| ' + ' | '.join(['---'] * len(cells)) + ' |')
    return '\n'.join(md_rows) if md_rows else None


def _resolve_image(src: str, manifest_items: dict) -> dict | None:
    """从 manifest 中解析图片 src → {ext, data}"""
    # 处理相对路径
    for href, item in manifest_items.items():
        if src in href or href.endswith(src) or src.endswith(href.split('/')[-1]):
            try:
                return {
                    'ext': href.rsplit('.', 1)[-1].lower() if '.' in href else 'png',
                    'data': item.get_content(),
                }
            except Exception:
                continue
    return None


# ── EpubEngine ──


class EpubEngine(Engine):
    """epub-container（自研）— EPUB 电子书 → 结构化 Markdown

    提取章节层级（TOC/spine）、章节全文（text）、机械骨架（skeleton）。
    骨架 = 标题 + 首段 + 末段 + 加粗/斜体 + 列表/表格 + 图片引用。
    对标 PyMuPDF：media_dir 写图片文件，不走 base64。
    """

    capabilities = ["text", "image", "office"]

    def execute(self, file_path: str, **kwargs) -> EpubResult:
        media_dir = kwargs.get("media_dir", "")

        # 自研 EPUB 容器解析层（纯标准库 zipfile+xml，替代 ebooklib AGPL-3.0）
        # 失败显式化：解析失败直接报错，不做隐式降级（引擎绑定只取第一个）
        from app.engines.epub_container import EpubContainer
        book = EpubContainer(str(file_path))

        # 1. 元数据
        metadata = _extract_metadata(book)

        # 2. 索引 manifest 中的图片项
        manifest_items: dict[str, object] = {}
        for item in book.get_items():
            # 记录所有图片项，供后续查找
            mt = item.get_type()
            if mt in (
                # 图片类型常量（对齐自研容器 / ebooklib 历史）
                5,  # ITEM_IMAGE
                7,  # ITEM_RASTER (from EPUB3)
            ) or (isinstance(mt, int) and mt in (5, 7)):
                manifest_items[item.get_name()] = item
            # 也记录 media-type 为 image/* 的项
            elif hasattr(item, 'media_type') and item.media_type and \
                    item.media_type.startswith('image/'):
                manifest_items[item.get_name()] = item

        # 也给每个 item 按 href 索引
        for item in book.get_items():
            href = item.get_name()
            if href and href not in manifest_items:
                mt = item.get_type()
                if isinstance(mt, int) and mt in (5, 7):
                    manifest_items[href] = item

        # 3. 构建 TOC → 章节映射（自研容器直接提供）
        toc_map = book.get_toc_map()

        # 4. 遍历 spine，提取每章
        chapters: list[dict] = []
        all_sections: list[dict] = []  # 扁平化 sections 列表
        global_img_idx = 0
        sec_idx = 0

        for spine_item in book.spine:
            item_id = spine_item[0] if isinstance(spine_item, tuple) else spine_item
            linear = True
            if isinstance(spine_item, tuple) and len(spine_item) > 1:
                linear = spine_item[1] is not False

            item = book.get_item_with_id(item_id)
            if not item:
                continue

            # 跳到非线性的辅助内容（版权页、致谢等）
            if not linear:
                continue

            content = item.get_content()
            # 尝试解码
            try:
                html_content = content.decode('utf-8')
            except UnicodeDecodeError:
                try:
                    html_content = content.decode('latin-1')
                except Exception:
                    continue

            # 查找此 spine 对应的 TOC 标题
            # TOC 项可能带 #fragment（同文件多锚点）——先精确匹配，miss 时按去锚匹配
            href = item.get_name()
            toc_info = toc_map.get(href) or next(
                (v for k, v in toc_map.items() if k.split('#')[0] == href), {}
            )
            chapter_title = toc_info.get('title', '')
            chapter_level = toc_info.get('level', 1)

            # 在 HTML 中尝试检测章节标题（如 TOC 未覆盖）
            sub_sections = _detect_sections(html_content)
            if sub_sections:
                for sub in sub_sections:
                    sub_title = sub['title']
                    sub_html = sub['html']
                    sub_text, global_img_idx = _html_to_markdown(
                        sub_html, media_dir, global_img_idx, manifest_items
                    )
                    skeleton = _build_skeleton(sub_text, sub_title)
                    sec = {
                        'index': sec_idx,
                        'title': sub_title,
                        'level': sub.get('level', 2),
                        'text': sub_text,
                        'skeleton': skeleton,
                    }
                    all_sections.append(sec)
                    sec_idx += 1
            else:
                # 无子标题 → 整章作为一节
                full_text, global_img_idx = _html_to_markdown(
                    html_content, media_dir, global_img_idx, manifest_items
                )
                skeleton = _build_skeleton(full_text, chapter_title or '')
                title = chapter_title or f'第{len(chapters)+1}章'
                sec = {
                    'index': sec_idx,
                    'title': title,
                    'level': 1,
                    'text': full_text,
                    'skeleton': skeleton,
                }
                all_sections.append(sec)
                sec_idx += 1

        # 5. 按章组织 sections（根据章节标题分组）
        if all_sections:
            chapters = _group_sections_by_chapter(all_sections)
        else:
            # 完全没有 spine → 回退：全书一章一节
            chapters = [{
                'index': 0,
                'title': metadata.get('title', '全文'),
                'sections': [{
                    'index': 0,
                    'title': '全文',
                    'level': 1,
                    'text': '',
                    'skeleton': '',
                }],
            }]

        # 6. 封面提取
        cover_ext = _extract_cover(book, media_dir)

        # 7. 构建输出 JSON
        result_json = json.dumps({
            'chapters': chapters,
            'metadata': metadata,
            'total_sections': len(all_sections),
            'cover': cover_ext,
        }, ensure_ascii=False)

        logger.info(
            "EpubEngine: %d chapters, %d sections, title=%s",
            len(chapters), len(all_sections),
            metadata.get('title', 'Unknown'),
        )

        book.close()  # 自研容器释放 zip 句柄（幂等，异常路径 __del__ 兜底）

        return EpubResult(
            text=result_json,
            pages=len(all_sections) or 1,
            engine="epub-container",
            epub_metadata=metadata,
        )


# ── 辅助函数 ──


def _extract_metadata(book) -> dict:
    """提取 DC/OPF 元数据"""
    meta = {}
    for namespace in ('DC', 'OPF', 'DCTERMS'):
        for field in ('title', 'creator', 'language', 'publisher', 'date', 'description'):
            try:
                vals = book.get_metadata(namespace, field)
                if vals and not meta.get(field):
                    meta[field] = vals[0][0] if vals[0] else None
            except (KeyError, IndexError, TypeError):
                pass
    if meta.get('creator'):
        meta['author'] = meta.pop('creator')
    return meta


def _detect_sections(html_content: str) -> list[dict]:
    """从 HTML 中检测 h1-h4 标题，按标题切分为 section 列表"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    soup = BeautifulSoup(html_content, 'html.parser')
    sections: list[dict] = []
    current_title = ''
    current_level = 1
    current_parts: list[str] = []

    body = soup.find('body') or soup
    for child in body.children:
        # 跳过纯空白文本节点（换行/缩进），否则产生空 section
        if not str(child).strip():
            continue
        if hasattr(child, 'name') and child.name in ('h1', 'h2', 'h3', 'h4'):
            # 保存上一个 section
            if current_parts:
                sections.append({
                    'title': current_title or '',
                    'level': current_level,
                    'html': '\n'.join(current_parts),
                })
                current_parts = []
            current_title = child.get_text().strip()
            current_level = int(child.name[1])
        else:
            current_parts.append(str(child))

    # 最后一个 section
    if current_parts:
        sections.append({
            'title': current_title or '',
            'level': current_level,
            'html': '\n'.join(current_parts),
        })

    # 如果只有一个 section 且无标题 → 返回空（表示不需要拆分）
    if len(sections) == 1 and not sections[0]['title']:
        return []

    return sections


def _build_skeleton(md_text: str, title: str = '') -> str:
    """从 Markdown 全文提取机械骨架"""
    lines = md_text.split('\n')
    skeleton_lines: list[str] = []

    if title:
        skeleton_lines.append(f"## {title}")

    # 收集非空行
    content_lines = [l for l in lines if l.strip()
                     and not l.startswith('#')  # 标题单独处理
                     and not l.startswith('![')]  # 图片单独处理

    # 图片引用行全部保留
    img_lines = [l for l in lines if l.startswith('![')]

    if not content_lines and not img_lines:
        skeleton_lines.append(md_text[:SKELETON_HEAD_CHARS])
        return '\n'.join(skeleton_lines)

    # 首段（前 N 字，跨行拼接直到达到阈值）
    head_chars = 0
    head_lines: list[str] = []
    for line in content_lines:
        if head_chars >= SKELETON_HEAD_CHARS:
            break
        head_lines.append(line)
        head_chars += len(line)
    if head_lines:
        skeleton_lines.append('**首段**: ' + ' '.join(head_lines))

    # 加粗/斜体行
    bold_lines = [l for l in content_lines
                  if '**' in l or '*' in l or '__' in l or '_' in l]
    if bold_lines:
        skeleton_lines.append('**重点**: ' + ' '.join(bold_lines[:3]))

    # 列表行
    list_lines = [l for l in content_lines if l.strip().startswith(('- ', '* ', '1. '))]
    if list_lines:
        skeleton_lines.extend(list_lines[:10])

    # 表格行
    table_lines = [l for l in content_lines if l.strip().startswith('|')]
    if table_lines:
        skeleton_lines.extend(table_lines[:15])

    # 图片
    if img_lines:
        skeleton_lines.extend(img_lines[:5])

    # 末段（后 N 字）
    tail_chars = 0
    tail_lines: list[str] = []
    for line in reversed(content_lines):
        if tail_chars >= SKELETON_TAIL_CHARS:
            break
        if line not in head_lines and line not in list_lines:
            tail_lines.insert(0, line)
            tail_chars += len(line)
    if tail_lines:
        skeleton_lines.append('**末段**: ' + ' '.join(tail_lines))

    return '\n'.join(skeleton_lines)


def _extract_cover(book, media_dir: str) -> str | None:
    """提取封面图到 media_dir，返回扩展名"""
    if not media_dir:
        return None
    try:
        # 方法1: guide 中查找 cover
        for guide_item in book.get_metadata('OPF', 'reference') or []:
            pass  # 复杂，跳过
    except Exception:
        logger.warning("Failed to extract cover via OPF guide references", exc_info=True)

    try:
        # 方法2: 查找 id='cover-image' 或 properties='cover-image' 的 manifest 项
        for item in book.get_items():
            item_id = getattr(item, 'id', '') or ''
            if 'cover' in item_id.lower():
                try:
                    data = item.get_content()
                    ext = item_id.rsplit('.', 1)[-1] if '.' in item_id else 'png'
                    dest = Path(media_dir) / f"cover.{ext}"
                    dest.write_bytes(data)
                    return ext
                except Exception:
                    continue
    except Exception:
        logger.warning("Failed to iterate EPUB items for cover detection", exc_info=True)

    return None


def _group_sections_by_chapter(sections: list[dict]) -> list[dict]:
    """将扁平 sections 按 level=1 的标题分组为章节"""
    chapters: list[dict] = []
    current_chapter: dict | None = None

    for sec in sections:
        if sec.get('level') == 1 and current_chapter is not None:
            chapters.append(current_chapter)
            current_chapter = {
                'index': len(chapters),
                'title': sec['title'],
                'sections': [sec],
            }
        elif sec.get('level') == 1:
            current_chapter = {
                'index': len(chapters),
                'title': sec['title'],
                'sections': [sec],
            }
        elif current_chapter is not None:
            current_chapter['sections'].append(sec)
        else:
            # 无 level=1 的章节，创建默认章
            current_chapter = {
                'index': 0,
                'title': '正文',
                'sections': [sec],
            }

    if current_chapter is not None:
        chapters.append(current_chapter)

    return chapters
