# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [
    ('bin', 'bin'),
    ('static', 'static'),  # KaTeX + markmap/d3 离线资源
]
# tiktoken_ext / dateparser_data — namespace package，collect_all 不会自动收集
# PyInstaller 对 namespace package 的处理不可靠，显式添加每个文件
import sys as _spec_sys
_sp_dir = os.path.join(_spec_sys.prefix, 'Lib', 'site-packages')
for _ns_pkg in ['tiktoken_ext', 'dateparser_data']:
    _ns_dir = os.path.join(_sp_dir, _ns_pkg)
    if os.path.isdir(_ns_dir):
        for _f in os.listdir(_ns_dir):
            _fp = os.path.join(_ns_dir, _f)
            if os.path.isfile(_fp) and not _f.startswith('.'):
                datas.append((_fp, _ns_pkg))
binaries = []
hiddenimports = [
    'uvicorn', 'fastapi', 'starlette', 'pydantic',
    'openai', 'httpx', 'aiohttp', 'requests',
    'python_multipart', 'dateutil',
    'PIL', 'numpy',
    'gmssl', 'websocket',
    # 关键子依赖（PyInstaller 常漏掉）
    'anyio', 'sniffio', 'h11',
    'typing_extensions', 'annotated_types',
    'urllib3', 'charset_normalizer', 'idna',
    'tqdm', 'click',
    # 直接使用的包
    'pymupdf4llm', 'tiktoken', 'ffmpeg',
    'dateparser_data',
    'markdown', 'pygments', 'pygments.lexers', 'pygments.formatters', 'pygments.styles',
    # app 模块（函数内动态 import，PyInstaller 静态分析遗漏）
    'app.generation.generator', 'app.generation.content_preparer',
    'app.generation.prompt_builder', 'app.generation.prompt',
    'app.generation.fact_check', 'app.core.model_resolver',
    'app.core.cookie_parser', 'app.utils.url_security',
    'app.utils.screenshot_marker', 'app.utils.video_helper',
    'app.utils.video_reader', 'app.downloaders.douyin_downloader',
    'app.downloaders.douyin_helper',
    'app.llm', 'app.llm.service', 'app.llm.agent', 'app.llm.errors',
    'app.agent', 'app.agent.tools', 'app.agent.evidence',
    'app.generation.review',
]

# 引擎相关
hiddenimports += [
    'pymupdf', 'fitz',
    'cv2', 'numpy',
    'docx', 'pptx', 'openpyxl',
    'markitdown', 'ebooklib',
    'faster_whisper', 'faster_whisper.assets',
    'trafilatura',
    'dashscope', 'dashscope.audio.asr',
    'youtube_transcript_api',
    'pytesseract', 'pytesseract.pytesseract',
    'av', 'ctranslate2', 'onnxruntime', 'tokenizers',
    'huggingface_hub', 'filelock', 'fsspec',
    'lxml', 'courlan', 'htmldate', 'justext', 'tld',
    'babel', 'dateparser', 'regex', 'tzlocal',
    'soupsieve', 'bs4',
    'magika', 'markdownify', 'html2text',
    'cffi', 'cryptography',
    'rich', 'pygments',
    'sympy', 'mpmath',
]
# 格式别名
hiddenimports += [
    'yaml',       # pyyaml import 名
    'cv2',        # opencv
    'fitz',       # pymupdf
    'yt_dlp',     # 视频下载
]

# collect-all 处理有复杂原生扩展的包
for pkg in ['duckduckgo_search', 'faster_whisper', 'pymupdf', 'pymupdf4llm', 'dashscope',
            'youtube_transcript_api', 'trafilatura', 'av', 'ctranslate2',
            'onnxruntime', 'tokenizers', 'tiktoken',
            'huggingface_hub', 'markitdown', 'pytesseract', 'lxml',
            'courlan', 'justext', 'htmldate', 'magika', 'babel',
            'cffi', 'cryptography', 'aiohttp', 'httpx', 'openai',
            'certifi', 'requests']:
    try:
        tmp_ret = collect_all(pkg)
        datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    except Exception:
        pass

# ⚠️ cryptography 携带的 libcrypto-3-x64.dll 版本与 Python 原生 _ssl.pyd 不兼容，
# 必须排除并用 Python 基础安装的版本替换
# PyInstaller 6.x binaries 格式: (dest_name, src_path)
binaries = [(dest, src) for dest, src in binaries
            if not (dest.startswith('libcrypto') and 'cryptography' in src)]
binaries = [(dest, src) for dest, src in binaries
            if not (dest.startswith('libssl') and 'cryptography' in src)]

# 显式添加 Python 原生的 OpenSSL DLL（与 _ssl.pyd 版本匹配）
import sys as _sys
_py_dlls = os.path.join(getattr(_sys, 'base_prefix', _sys.prefix), 'DLLs')
for _dll in ['libcrypto-3-x64.dll', 'libssl-3-x64.dll']:
    _dll_path = os.path.join(_py_dlls, _dll)
    if os.path.exists(_dll_path):
        binaries.append((_dll_path, '.'))
    else:
        print(f'WARNING: Python native OpenSSL DLL not found: {_dll_path}')


a = Analysis(
    ['main.py'],
    pathex=['backend'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LiMoBackend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LiMoBackend',
)
