# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [
    ('static', 'static'),  # KaTeX + markmap/d3 离线资源
    ('app/agent/prompts', 'app/agent/prompts'),  # 分层 system prompt
    # 示例文档内容（笔记 + 整理版原文），seed_sample_doc 启动时读取
    ('app/core/sample_docs', 'app/core/sample_docs'),
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
    'tiktoken', 'ffmpeg',
    'dateparser_data',
    'markdown', 'pygments', 'pygments.lexers', 'pygments.formatters', 'pygments.styles',
    # app 模块（函数内动态 import，PyInstaller 静态分析遗漏）
    'app.generation.generator', 'app.generation.content_preparer',
    'app.generation.prompt_builder', 'app.generation.prompt',
    'app.generation.fact_check', 'app.core.model_resolver',
    'app.core.cookie_parser', 'app.core.text_utils',
    'app.utils.url_security',
    'app.utils.screenshot_marker', 'app.utils.video_helper',
    'app.utils.video_reader', 'app.downloaders.douyin_downloader',
    'app.downloaders.douyin_helper',
    'app.llm', 'app.llm.service', 'app.llm.agent', 'app.llm.errors',
    'app.agent', 'app.agent.tools', 'app.agent.evidence',
    'app.generation.review', 'app.generation.ocr_preprocessor',
    'app.core.disconnect',
    # 引擎模块（init_engines() 函数内动态导入，PyInstaller 无法静态追踪）
    'app.engines.doc_types', 'app.engines.doc_markitdown',
    'app.engines.doc_docx', 'app.engines.doc_pptx', 'app.engines.doc_excel',
]

# 引擎相关
hiddenimports += [
    'numpy',
    'docx', 'pptx', 'openpyxl',
    'markitdown', 'ebooklib',
    # faster_whisper/ctranslate2/av/tokenizers 已移除（faster-whisper 不再内置）
    'trafilatura',
    'dashscope', 'dashscope.audio.asr',
    'youtube_transcript_api',
    'onnxruntime',  # magika 文件类型检测依赖（markitdown），必须保留
    'filelock',
    'lxml', 'courlan', 'htmldate', 'justext', 'tld',
    'babel', 'dateparser', 'regex', 'tzlocal',
    'soupsieve', 'bs4',
    'magika', 'markdownify', 'html2text',
    'cffi', 'cryptography',
    'rich', 'pygments',
]
# 格式别名
hiddenimports += [
    'yaml',       # pyyaml import 名
    'yt_dlp',     # 视频下载
]

# collect-all 处理有复杂原生扩展的包
# docling/docling_parse/rapidocr 已随 DoclingEngine 移除（2026-08-02，依赖 torch ~2GB 无法打包）
for pkg in ['duckduckgo_search', 'dashscope',
            'youtube_transcript_api', 'trafilatura',
            'onnxruntime', 'tiktoken',
            'markitdown', 'lxml',
            'courlan', 'justext', 'htmldate', 'magika', 'babel',
            'cffi', 'cryptography', 'aiohttp', 'httpx', 'openai',
            'certifi', 'requests', 'pypdfium2']:
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
    excludes=['torch', 'torchvision', 'torchaudio', 'triton',
        'nvidia', 'nvidia_cublas', 'nvidia_cuda_cupti', 'nvidia_cuda_nvrtc',
        'nvidia_cuda_runtime', 'nvidia_cudnn', 'nvidia_cufft', 'nvidia_curand',
        'nvidia_cusolver', 'nvidia_cusparse', 'nvidia_nccl', 'nvidia_nvtx',
        'nvidia_nvjitlink',
        # cv2/opencv：backend 零使用（无 opencv 依赖），仅被依赖链意外拖入，排除省 ~112MB
        'cv2', 'opencv', 'cv2.data',
        # av(PyAV)/faker：docling 运行时主链路不加载（transformers 视频模型静态 import av /
        # polyfactory 收集 faker），排除省 ~12MB
        'av', 'faker'],
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
