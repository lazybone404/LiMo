# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [
    ('bin', 'bin'),
    ('static', 'static'),  # KaTeX + markmap/d3 离线资源
]
binaries = []
hiddenimports = [
    'uvicorn', 'fastapi', 'starlette', 'pydantic',
    'openai', 'httpx', 'aiohttp', 'requests',
    'pyyaml', 'python_multipart', 'dateutil',
    'PIL', 'numpy',
    'gmssl', 'websocket',
    # 关键子依赖（PyInstaller 常漏掉）
    'anyio', 'sniffio', 'h11',
    'typing_extensions', 'annotated_types',
    'urllib3', 'charset_normalizer', 'idna',
    'tqdm', 'click',
    # 直接使用的包
    'pymupdf4llm', 'tiktoken', 'ffmpeg',
    'markdown', 'pygments', 'pygments.lexers', 'pygments.formatters', 'pygments.styles',
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
    'soupsieve', 'beautifulsoup4',
    'magika', 'markdownify',
    'cffi', 'cryptography',
    'rich', 'pygments',
    'sympy', 'mpmath',
]
# 格式别名
hiddenimports += [
    'yaml',       # pyyaml 别名
    'Pillow',     # PIL 别名
    'cv2',        # opencv
    'fitz',       # pymupdf
    'yt_dlp',     # 视频下载
]

# collect-all 处理有复杂原生扩展的包
for pkg in ['faster_whisper', 'pymupdf', 'pymupdf4llm', 'dashscope',
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
