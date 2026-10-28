# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
import glob as _glob

datas = [
    ('static', 'static'),  # KaTeX + markmap/d3 离线资源
    ('app/agent/prompts', 'app/agent/prompts'),  # 分层 system prompt
    # 示例文档内容（笔记 + 整理版原文），seed_sample_doc 启动时读取
    ('app/core/sample_docs', 'app/core/sample_docs'),
    # 许可证全文（GPL/Apache/MIT + 说明），随包附带满足再分发义务
    # ⚠️ PyInstaller 6.x 的 datas 相对路径按 spec 所在目录（backend/）解析，
    #    故 licenses 在项目根需用 ../licenses 回退（其余三项恰好在 backend/ 下）
    ('../licenses', 'licenses'),
    # 第三方组件总清单（组件/许可证/全文位置，四类），落到 licenses/ 内与全文同处——
    # 它此前只存在于 GitHub 仓库，装机用户在安装目录里看不到那份唯一的总清单。
    # 目录读者从 licenses/README.md 指路过来。
    ('../THIRD-PARTY-NOTICES.md', 'licenses'),
    # 卡片骨架 CSS —— ⚠️ **唯一真相源在前端**（`frontend/src/styles/cards.css`），
    # 前端 `main.tsx` 直接 import 它、后端 `export.py::_load_card_css()` 从磁盘读
    # 同一份并内联进导出 HTML。**刻意不往 backend/static/ 放副本** —— 副本就是
    # 第二份拷贝，正是「同一份判据不得有两份实现」这条纪律要消除的形态，绕到
    # 打包路径上发生也一样。落到 _MEIPASS/styles/ 下，与 _load_card_css 的
    # 候选路径逐字对应（改这里必须同步改那里，两处的名字是成对常量）。
    ('../frontend/src/styles/cards.css', 'styles'),
    # 语义标记（limo-block）CSS —— 与上一行同款：唯一真相源在前端
    # （`frontend/src/styles/blocks.css`），`export.py::_load_block_css()` 读同一份。
    # ⚠️ **与 cards.css 是两份文件、两行 datas**（一份管骨架、一份管标记），
    # 合并成一行目录收集会在「只想带一份」时被误伤。落到 _MEIPASS/styles/ 下，
    # 与 `_load_block_css` 的候选路径逐字对应。
    ('../frontend/src/styles/blocks.css', 'styles'),
    # 主题层 CSS（六轴的**纯数据层**）—— 与上两行同款：唯一真相源在前端
    # （`frontend/src/styles/theme-layer.css`），`index.css` 用 `@import` 引入它
    # 给应用自己用，`export.py::_load_theme_layer_css()` 从磁盘读同一份内联进
    # 「跟随应用主题」的导出 HTML。**刻意不往 backend/static/ 放副本**（同上）。
    # ⚠️ 少了这一行，**开发版全绿而打包版导出直接 500**（读不到文件时按纪律显式
    # 抛错，不静默输出无主题的 HTML）—— 那个 500 只会在用户装了安装包之后出现。
    ('../frontend/src/styles/theme-layer.css', 'styles'),
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
    'websocket',
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
    'app.core.model_resolver',
    'app.core.cookie_parser', 'app.core.text_utils',
    'app.utils.url_security',
    'app.utils.screenshot_marker', 'app.utils.video_helper',
    'app.utils.video_reader',
    'app.llm', 'app.llm.service', 'app.llm.agent', 'app.llm.errors',
    'app.agent', 'app.agent.tools',
    'app.generation.ocr_preprocessor',
    'app.core.disconnect',
    'app.services.chart_renderer',  # 图表渲染（PIL 自绘；PIL 已随图片优化收集）
    'app.services.markdown_render', # Markdown→HTML 公共渲染（HTML/Word 导出共用）
    'app.services.docx_export',     # Word 导出（python-docx + bs4）
    # 语义检索（2026-10-26 阶段 1-4）—— 全部在函数内延迟导入
    # （`main.py` 的 startup/shutdown 与 routers/search.py），PyInstaller 静态
    # 分析追踪不到。⚠️ 少了这四行，**开发版全绿而装好的版本设置页直接塌**
    # （开了向量开关、worker 起不来，且界面上只说「不可用」）。
    # ⚠️ 刻意**不加** `onnxruntime` / `tokenizers` 进 collect_all —— 它们不在
    # `pyproject.toml`，本地 embedding 引擎在打包版按设计**不可用**
    # （缺依赖时 `embedding_local` 抛 MissingDependency，设置页显式说明）。
    'app.core.vector_store', 'app.core.vector_index',
    'app.services.vector_runtime', 'app.services.vector_worker',
    'app.services.vector_search',
    # 全文索引的写入入口（`core/search.py::update_index` 被五个写点延迟导入）
    'app.core.search',
    # 引擎模块（init_engines() 函数内动态导入，PyInstaller 无法静态追踪）
    'app.engines.doc_types', 'app.engines.doc_markitdown',
    'app.engines.doc_docx', 'app.engines.doc_pptx', 'app.engines.doc_excel',
    'app.engines.epub', 'app.engines.epub_container',  # EPUB 自研解析器（2026-08-26）
    # 向量化引擎（`init_engines()` 函数内延迟导入，同上一组）
    # ⚠️ `embedding_local` 顶部**零重依赖 import**（onnxruntime / tokenizers 都在
    # `Embedder.__init__` 里惰性载入），故它本身能安全进包；打包版调用时才抛
    # MissingDependency 并给出安装命令。
    'app.engines.embedding_dashscope', 'app.engines.embedding_local',
]

# 引擎相关
hiddenimports += [
    'numpy',
    'docx', 'pptx', 'openpyxl', 'xlrd',
    'markitdown',
    # faster_whisper/ctranslate2/av/tokenizers 已移除（faster-whisper 不再内置）
    'trafilatura',
    # RSS / Atom 订阅源解析（2026-10-29）。⚠️ 在 `webpage.py::_merge_feed` 里是
    # **函数内延迟导入**，PyInstaller 静态分析追踪不到 —— 少了这行，开发版导 RSS
    # 全绿而打包版一导就 ModuleNotFoundError（且只在这一条路径上炸）。
    'feedparser',
    'dashscope', 'dashscope.audio.asr',
    'youtube_transcript_api',
    'onnxruntime',  # magika 文件类型检测依赖（markitdown），必须保留
    'lxml', 'courlan', 'htmldate', 'justext', 'tld',
    'babel', 'dateparser', 'regex', 'tzlocal',
    'soupsieve', 'bs4',
    'magika', 'markdownify',
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
            'feedparser',
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

# resvg_py 已移除（2026-08-16 resvg→PIL 切换：resvg 全系在 Windows 无法渲染 SVG 文字
# —— usvg text 解析 "No match for <font>"，fontdb 加载正常但匹配不上，上游 bug）

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
