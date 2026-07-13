# 打包指南

一次完整的打包流程：PyInstaller → 复制 ffmpeg → Tauri。

产物在 `frontend/src-tauri/target/release/bundle/`：NSIS ~374MB，MSI ~514MB。

---

## 打包步骤

### 1. 清理 + PyInstaller

```bash
rm -rf frontend/src-tauri/bin/LiMoBackend
uv run pyinstaller backend/LiMoBackend.spec --distpath frontend/src-tauri/bin
```

必须在项目根目录执行。spec 里 `pathex=['backend']` 依赖 CWD。

### 2. 重命名 + 复制 ffmpeg

```bash
cd frontend/src-tauri/bin/LiMoBackend
cp LiMoBackend.exe LiMoBackend-x86_64-pc-windows-msvc.exe
mkdir -p ffmpeg
cp /path/to/ffmpeg.exe ffmpeg/
cp /path/to/ffprobe.exe ffmpeg/
```

Tauri v2 的 `externalBin` 自动加平台后缀，所以需要重命名。

### 3. Tauri

```bash
cd frontend
npx tauri build
```

---

## 新增 Python 依赖时

`uv add` 新包后，检查两件事：

### 函数内 import 是否漏了

函数内的 `from app.xxx import yyy` 不会被 PyInstaller 静态分析抓到，需要手动加到 spec 的 `hiddenimports`。

```bash
# 扫描所有函数内 import，和 spec 里的 hiddenimports 比对
rg "^\s+from app\.\S+ import" --glob '*.py' app/
```

已固化在 spec 中的（不要重复加）：`app.generation.*`、`app.core.model_resolver`、`app.core.cookie_parser`、`app.utils.url_security`、`app.utils.screenshot_marker`、`app.utils.video_helper`、`app.utils.video_reader`、`app.downloaders.douyin_downloader`、`app.llm.*`、`app.agent.*`。

### 验证 exe 能启动

```bash
./frontend/src-tauri/bin/LiMoBackend/LiMoBackend.exe
# 看到 "Uvicorn running on http://127.0.0.1:7413" 即成功
```

---

## spec 中已固化的配置（不要动）

以下问题已经在 spec 中处理好了，不是每次打包的步骤：

- **SSL 证书**：certifi + 启动时设 `SSL_CERT_FILE`
- **OpenSSL DLL 冲突**：排除 cryptography 的 DLL，用 Python 原生版本
- **KaTeX 字体**：`datas` 打包 `backend/static/`，字体从 node_modules 复制到 static 后就不用再管
- **searxng .git**：已清理，除非更新 searxng 版本
- **console=False**：PyInstaller 不弹黑框
- **Tauri resources**：`_internal/` 和 `ffmpeg/` 已在 tauri.conf.json 配置
