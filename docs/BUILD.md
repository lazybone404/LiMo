# 打包指南

一次完整的打包流程：PyInstaller → 复制 ffmpeg → Tauri。

产物在 [bundle/](../frontend/src-tauri/target/release/bundle/)（当前版本 0.5.10，2026-10-18 表格块 flex 压缩与关装饰消失修复后）：
- NSIS → [nsis/](../frontend/src-tauri/target/release/bundle/nsis/)（`LiMo_0.5.10_x64-setup.exe`，~148MB）
- MSI → [msi/](../frontend/src-tauri/target/release/bundle/msi/)（`LiMo_0.5.10_x64_en-US.msi`，~194MB）

> **体积优化（2026-08-14）**：docling/pytesseract 依赖残留清理 + ffmpeg 换精简版后，安装包从 ~265M/~366M 降到 ~148M/~195M。详见 [CHANGELOG.md](CHANGELOG.md)（2026-08-14 打包体积优化条目）。

---

## 打包步骤

### 1. 清理 + PyInstaller

```bash
# ⚠️ 必须在项目根目录执行（spec 的 pathex=['backend'] 和 datas 相对 CWD）
# ⚠️ 必须用 venv python -m 方式：uv run（根目录无 pyproject）/ pyinstaller.exe trampoline 都会 canonicalize 失败
rm -rf frontend/src-tauri/bin/LiMoBackend
backend/.venv/Scripts/python.exe -m PyInstaller --noconfirm --clean backend/LiMoBackend.spec --distpath frontend/src-tauri/bin
```

### 2. 重命名 + 复制 ffmpeg

```bash
cd frontend/src-tauri/bin/LiMoBackend
cp LiMoBackend.exe LiMoBackend-x86_64-pc-windows-msvc.exe
mkdir -p ffmpeg
cp ~/.limo/ffmpeg-portable/ffmpeg.exe ffmpeg/
cp ~/.limo/ffmpeg-portable/ffprobe.exe ffmpeg/
# ⚠️ ffmpeg 为 GPLv3 构建（gyan.dev 全系，含 libx264/x265）。独立进程调用无传染，
#    但必须随附全文 + 构建说明——这两个文件同样被 --noconfirm 删掉，须一并重拷
cp ../../../../licenses/GPL-3.0.txt ffmpeg/
cp ../../../../licenses/README-ffmpeg.txt ffmpeg/
```

> `licenses/` 目录与根目录 [`THIRD-PARTY-NOTICES.md`](../THIRD-PARTY-NOTICES.md)（第三方组件总清单）由 spec 的 `datas` **一并**打包进 `_internal/licenses/`（自动，无需手动）；上面两步是给 `ffmpeg/` 内**紧邻二进制**再放一份，合规上更明确。

Tauri v2 的 `externalBin` 自动加平台后缀，所以需要重命名。

> **ffmpeg 精简版（2026-08-14 起）**：内置 ffmpeg 已从 gyan.dev full build（434M）换成 **gyan.dev essentials 8.1.1**（194M，`~/.limo/ffmpeg-portable/`，已验证含 libmp3lame/pcm_s16le/mjpeg + h264/hevc/av1/aac/mp3/opus 解码 + mp4/mkv/webm/avi/flv 容器）。旧完整版备份在 `~/.limo/ffmpeg-backup-full/`（434M）。若精简版对某些输入失败，用备份回退并在 [CHANGELOG.md](CHANGELOG.md) 记录支持范围。

> **⚠️ 顺序铁律**：步骤 1 的 `--noconfirm` 会重建整个 `bin/LiMoBackend/` 目录——**删除**手动复制的带后缀 exe 和 `ffmpeg/`。因此「重命名 exe + 复制 ffmpeg」必须在 PyInstaller 之后、Tauri 之前重新执行，绝不能只在第一次做。新增/更新依赖后重跑打包时必须完整走一遍步骤 1→2→3。

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

已固化在 spec 中的（不要重复加）：
- `app.generation.*`（generator/content_preparer/prompt_builder/prompt/ocr_preprocessor）
- `app.core.model_resolver`、`app.core.cookie_parser`、`app.core.text_utils`、`app.core.disconnect`
- `app.utils.url_security`、`app.utils.screenshot_marker`、`app.utils.video_helper`、`app.utils.video_reader`
- `app.llm`、`app.llm.service`、`app.llm.agent`、`app.llm.errors`
- `app.agent`、`app.agent.tools`
- `app.engines.*`（doc_types/doc_markitdown/doc_docx/doc_pptx/doc_excel/epub 等，`init_engines()` 动态导入）

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
- **searxng**：不随包（AGPL-3.0，tauri resources 未配置）。如需手动部署到安装目录 `searxng/`：清理 `.git` 目录 + 附 AGPL-3.0 全文（从 [searxng 官方仓库](https://github.com/searxng/searxng) 获取；主包已零 AGPL 库，`licenses/` 不再附带 AGPL 全文）
- **console=False**：PyInstaller 不弹黑框
- **Tauri resources**：`_internal/` 和 `ffmpeg/` 已在 tauri.conf.json 配置
