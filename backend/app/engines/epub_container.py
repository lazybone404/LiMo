"""
EPUB 容器解析层 — 纯标准库（zipfile + xml.etree），替代 ebooklib（AGPL-3.0）。

仅实现 LiMo EpubEngine 需要的接口（对齐 ebooklib book/item 对象的子集）：
- EpubContainer:  get_metadata(ns, field) / get_items() / spine / get_item_with_id(id) / get_toc_map()
- EpubItem:       get_name() / get_content() / get_type() / media_type / id

EPUB 3 容器规范：META-INF/container.xml → OPF（metadata/manifest/spine）→ 各资源。
TOC：EPUB3 nav（XHTML）优先，回退 EPUB2 NCX（toc.ncx）。

设计约束（2026-08-26）：
- 零第三方依赖（zipfile / xml.etree / posixpath 均为标准库）
- 只覆盖 EpubEngine 实际使用的能力，不做完整 EPUB 支持
- 解析失败抛异常 → 调用方（EpubEngine）直接报错（失败显式化，不做隐式降级）
"""

import posixpath
import zipfile
import xml.etree.ElementTree as ET

# 命名空间
NS_CONTAINER = 'urn:oasis:names:tc:opendocument:xmlns:container'
NS_OPF = 'http://www.idpf.org/2007/opf'
NS_DC = 'http://purl.org/dc/elements/1.1/'
NS_DCTERMS = 'http://purl.org/dc/terms/'
NS_XHTML = 'http://www.w3.org/1999/xhtml'
NS_EPUB = 'http://www.idpf.org/2007/ops'
NS_NCX = 'http://www.daisy.org/z3986/2005/ncx/'

# 条目类型常量（对齐 ebooklib：5=ITEM_IMAGE, 7=ITEM_RASTER）
ITEM_IMAGE = 5
ITEM_RASTER = 7


def _local_name(tag: str) -> str:
    """'http://...}name' → 'name'"""
    return tag.rsplit('}', 1)[-1] if '}' in tag else tag


def _norm_href(href: str, base_dir: str, keep_fragment: bool = False) -> str:
    """相对 OPF 目录的 href → zip 内绝对路径（规范化 ..）。

    keep_fragment=True 时保留 #fragment（TOC 用，区分同文件多锚点项）；
    读 zip 内容时 keep_fragment=False（去锚点/查询串）。
    """
    fragment = ''
    if keep_fragment and '#' in href:
        href, fragment = href.split('#', 1)
        fragment = '#' + fragment
    clean = href.split('#')[0].split('?')[0]
    if not clean:
        return ''
    if base_dir:
        result = posixpath.normpath(posixpath.join(base_dir, clean))
    else:
        result = posixpath.normpath(clean)
    return result + fragment if keep_fragment else result


def _find_first_ol(elem) -> object | None:
    """递归找第一个 ol 元素（nav 根列表，嵌套 ol 由 _walk_nav_list 处理）"""
    for child in elem:
        if _local_name(child.tag) == 'ol':
            return child
        nested = _find_first_ol(child)
        if nested is not None:
            return nested
    return None


class EpubItem:
    """manifest 中的一个资源项（对齐 ebooklib EpubItem 子集）"""

    __slots__ = ('id', 'href', 'media_type', 'properties', '_content')

    def __init__(self, item_id: str, href: str, media_type: str,
                 properties: str, content: bytes):
        self.id = item_id
        self.href = href  # zip 内绝对路径
        self.media_type = media_type or ''
        self.properties = properties or ''
        self._content = content

    def get_name(self) -> str:
        return self.href

    def get_content(self) -> bytes:
        return self._content

    def get_type(self) -> int:
        """对齐 ebooklib 类型常量：图片 → ITEM_IMAGE；其余返回 0。"""
        if self.media_type.startswith('image/'):
            return ITEM_IMAGE
        return 0


class EpubContainer:
    """解析后的 EPUB 容器（对齐 ebooklib book 对象子集）"""

    def __init__(self, file_path: str):
        self._zip = zipfile.ZipFile(file_path)
        try:
            self._parse()
        except Exception:
            self._zip.close()
            raise

    # ── 解析主流程 ──

    def _parse(self):
        opf_path = self._find_opf_path()
        self._opf_dir = posixpath.dirname(opf_path)
        opf_xml = self._read_str(opf_path)
        self._parse_opf(opf_xml)
        self._parse_toc()

    def _read_str(self, path: str) -> str:
        """从 zip 读取文本（先 UTF-8，回退 latin-1）。"""
        data = self._zip.read(path)
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            return data.decode('latin-1', errors='replace')

    def _find_opf_path(self) -> str:
        """META-INF/container.xml → rootfile full-path（OPF 位置）"""
        try:
            container_xml = self._read_str('META-INF/container.xml')
        except KeyError:
            raise ValueError('EPUB 缺少 META-INF/container.xml')
        root = ET.fromstring(container_xml)
        # rootfiles > rootfile[@media-type 含 oebps-package 或 full-path 以 .opf 结尾]
        for rootfile in root.iter():
            if _local_name(rootfile.tag) != 'rootfile':
                continue
            full_path = rootfile.get('full-path', '')
            media_type = rootfile.get('media-type', '')
            if full_path and (
                'oebps-package' in media_type or full_path.lower().endswith('.opf')
            ):
                return full_path
        # 兜底：取第一个 rootfile
        for rootfile in root.iter():
            if _local_name(rootfile.tag) == 'rootfile' and rootfile.get('full-path'):
                return rootfile.get('full-path')
        raise ValueError('EPUB container.xml 无有效 rootfile')

    def _parse_opf(self, opf_xml: str):
        root = ET.fromstring(opf_xml)
        self.items: dict[str, EpubItem] = {}
        self.spine: list[tuple] = []  # [(idref, linear_bool)]
        self._metadata: dict[tuple, list] = {}

        for elem in root:
            tag = _local_name(elem.tag)
            if tag == 'metadata':
                self._parse_metadata(elem)
            elif tag == 'manifest':
                self._parse_manifest(elem)
            elif tag == 'spine':
                self._parse_spine(elem)

    def _parse_metadata(self, meta_elem):
        """收集 dc/dcterms/opf 命名空间元素 → {(ns, field): [(value, attrs)]}"""
        for child in meta_elem:
            ns, field = _split_ns(child.tag)
            if not ns or not field:
                continue
            value = child.text or ''
            attrs = dict(child.attrib)
            self._metadata.setdefault((ns, field), []).append([value, attrs])

    def _parse_manifest(self, manifest_elem):
        for item in manifest_elem:
            if _local_name(item.tag) != 'item':
                continue
            item_id = item.get('id', '')
            href = _norm_href(item.get('href', ''), self._opf_dir)
            media_type = item.get('media-type', '')
            if not item_id or not href:
                continue
            try:
                content = self._zip.read(href)
            except KeyError:
                content = b''  # href 指向缺失资源（如外部链接）→ 空内容
            self.items[item_id] = EpubItem(
                item_id, href, media_type, item.get('properties', ''), content,
            )

    def _parse_spine(self, spine_elem):
        for itemref in spine_elem:
            if _local_name(itemref.tag) != 'itemref':
                continue
            idref = itemref.get('idref', '')
            if not idref:
                continue
            linear = itemref.get('linear', 'yes').lower() != 'no'
            self.spine.append((idref, linear))

    # ── TOC 解析 ──

    def _parse_toc(self):
        """优先 EPUB3 nav（properties=nav），回退 EPUB2 NCX。"""
        self._toc_flat: list[dict] = []
        nav_href = ''
        ncx_href = ''
        for item in self.items.values():
            if 'nav' in (item.properties or '').split():
                nav_href = item.href
                break
            if item.media_type == 'application/x-dtbncx+xml':
                ncx_href = item.href
        # 兜底：无 properties=nav 时按文件名约定（nav.xhtml / toc.xhtml）
        if not nav_href:
            for item in self.items.values():
                if item.media_type == 'application/xhtml+xml' and (
                    item.href.lower().endswith('nav.xhtml')
                    or item.href.lower().endswith('toc.xhtml')
                ):
                    nav_href = item.href
                    break

        if nav_href and self._parse_nav(nav_href):
            return
        if ncx_href:
            self._parse_ncx(ncx_href)

    def _parse_nav(self, href: str) -> bool:
        """EPUB3 nav：解析 XHTML 中 epub:type="toc" 的 nav > ol > li（嵌套）"""
        try:
            xhtml = self._read_str(href)
            root = ET.fromstring(xhtml)
        except (KeyError, ET.ParseError, ValueError):
            return False

        nav = None
        for elem in root.iter():
            if _local_name(elem.tag) == 'nav':
                etype = elem.get(f'{{{NS_EPUB}}}type', '')
                if 'toc' in etype:
                    nav = elem
                    break
        if nav is None:
            # 兜底：第一个 nav
            for elem in root.iter():
                if _local_name(elem.tag) == 'nav':
                    nav = elem
                    break
        if nav is None:
            return False

        found = False
        root_ol = _find_first_ol(nav)
        if root_ol is not None:
            found = self._walk_nav_list(root_ol, 1)
        return found

    def _walk_nav_list(self, ol, level: int) -> bool:
        """递归遍历 nav 的 ol > li > a（+嵌套 ol）"""
        found = False
        for li in ol:
            if _local_name(li.tag) != 'li':
                continue
            title = ''
            href = ''
            for child in li:
                if _local_name(child.tag) == 'a':
                    title = (child.text or '').strip() or ''.join(
                        t for t in child.itertext() if t.strip()
                    ).strip()
                    href = _norm_href(child.get('href', ''), self._opf_dir,
                                      keep_fragment=True)
                    break
            if title and href:
                self._toc_flat.append({'title': title, 'href': href, 'level': level})
                found = True
            # 嵌套列表（子章节）
            for child in li:
                if _local_name(child.tag) == 'ol':
                    self._walk_nav_list(child, level + 1)
        return found

    def _parse_ncx(self, href: str) -> bool:
        """EPUB2 NCX：navMap > navPoint（嵌套）"""
        try:
            ncx = self._read_str(href)
            root = ET.fromstring(ncx)
        except (KeyError, ET.ParseError, ValueError):
            return False

        nav_map = None
        for elem in root.iter():
            if _local_name(elem.tag) == 'navMap':
                nav_map = elem
                break
        if nav_map is None:
            return False
        self._walk_ncx(nav_map, 1)
        return bool(self._toc_flat)

    def _walk_ncx(self, parent, level: int):
        for nav_point in parent:
            if _local_name(nav_point.tag) != 'navPoint':
                continue
            title = ''
            href = ''
            for child in nav_point:
                cn = _local_name(child.tag)
                if cn == 'navLabel':
                    label = child.find(f'{{{NS_NCX}}}text')
                    title = (label.text or '').strip() if label is not None else ''
                elif cn == 'content':
                    href = _norm_href(child.get('src', ''), self._opf_dir,
                                      keep_fragment=True)
            if title and href:
                self._toc_flat.append({'title': title, 'href': href, 'level': level})
            self._walk_ncx(nav_point, level + 1)

    # ── 公开接口（对齐 ebooklib book 子集）──

    def get_metadata(self, namespace: str, field: str) -> list:
        """返回 [(value, attrs)]，对齐 ebooklib：vals[0][0] 取第一个 value。"""
        return self._metadata.get((namespace, field), [])

    def get_items(self) -> list:
        return list(self.items.values())

    def get_item_with_id(self, item_id: str):
        return self.items.get(item_id)

    def get_toc_map(self) -> dict:
        """{href: {title, level}}，替代 EpubEngine 的 _build_toc_map(book)。"""
        return {
            e['href']: {'title': e['title'], 'level': e['level']}
            for e in self._toc_flat
        }

    def close(self):
        """关闭 zip 句柄（幂等；异常路径由 __del__ 兜底，防句柄泄漏）。"""
        if getattr(self, '_closed', False):
            return
        self._closed = True
        self._zip.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def _split_ns(tag: str) -> tuple:
    """'http://...}field' → ('DC'|'DCTERMS'|'OPF', field)"""
    if '}' not in tag:
        return '', ''
    ns = tag[1:tag.index('}')]
    field = tag[tag.index('}') + 1:]
    if ns == NS_DC:
        return 'DC', field
    if ns == NS_DCTERMS:
        return 'DCTERMS', field
    if ns == NS_OPF:
        return 'OPF', field
    return '', ''
