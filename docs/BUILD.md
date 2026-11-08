# 打包指南

一次完整的打包流程：PyInstaller → 复制 ffmpeg → Tauri。

产物在 [bundle/](../frontend/src-tauri/target/release/bundle/)（当前版本见根目录 [VERSION](../VERSION)，本文示例按 0.5.15）：
- NSIS → [nsis/](../frontend/src-tauri/target/release/bundle/nsis/)（`LiMo_0.5.15_x64-setup.exe`，~148MB）
- MSI → [msi/](../frontend/src-tauri/target/release/bundle/msi/)（`LiMo_0.5.15_x64_en-US.msi`，~194MB）

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

## 发版：签名与 `latest.json`

上面的 1→2→3 是**日常打包**（打了包不发）。发版要多做三件事：签名、生成清单、上传。

### 为什么 `createUpdaterArtifacts` 不在主配置里

它住在[独立片段](../frontend/src-tauri/tauri.release.conf.json)，发版时用 `--config` 打开。
放进主配置的代价是：**每次** `npx tauri build` 都要求环境里有 `TAURI_SIGNING_PRIVATE_KEY`，
缺了直接构建失败。本仓库的日常是「打包 ≠ 发布」，把发布私钥变成打包的前提，等于让
「密钥丢了」升级成「连本地打包都做不了」。

忘加 `--config` 的后果是**没有 `.sig` 文件** —— 这一步由 `release.py --check` 拦下（响亮），
不会静默发出一个装不上的更新。

### 发版构建

```bash
export TAURI_SIGNING_PRIVATE_KEY="C:/WorkPlace/Tool/LiMo/.release-keys/limo-updater.key"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="$(cat C:/WorkPlace/Tool/LiMo/.release-keys/password.txt)"

cd frontend
npx tauri build --config src-tauri/tauri.release.conf.json
# → target/release/bundle/nsis/LiMo_{v}_x64-setup.exe
#   target/release/bundle/nsis/LiMo_{v}_x64-setup.exe.sig   ← 少了它客户端验签必失败
```

### 生成并校验清单

```bash
python scripts/release.py --write          # → dist-release/latest.json
python scripts/release.py --check          # 发版门禁，退出码 0/1/2
python scripts/release.py --print-upload   # 打印 gh release create 命令 + SHA256
```

`--print-upload` 打出来的命令含**三个文件**：安装包、`.sig`、`latest.json`。
少一个 `.sig` 时脚本直接报错退出（退出码 1）而不打印半条命令 —— 缺签名的 Release
在页面上看不出任何异常，只有客户端下载完才会「装不上」。

`--check` 的九条判据见脚本里 `collect_problems` 的注释。其中最关键的一条是
**清单 `signature` 必须与 `.sig` 文件逐字相同**：重新构建后忘了重跑 `--write`，
签名与包对不上，客户端下载完验签失败、更新装不上，而发版侧一切正常。

`latest.json` 与 `*.sig` 都**不进 git**（见 `.gitignore`），它们只存在于 Release 上。

### 密钥

- 位置：`C:\WorkPlace\Tool\LiMo\.release-keys\`（仓库外）：`limo-updater.key` /
  `limo-updater.key.pub` / `password.txt` / `README.md`。
- **私钥与密码必须一起备份到仓库之外。**
- **第一个带 updater 的版本发出去之前换钥零代价**，发出去之后就换不了 ——
  存量客户端只认已内置的公钥，换钥后它们再也收不到自动更新。
- 公钥有三处副本必须一致：`tauri.conf.json` 的 `plugins.updater.pubkey`、
  `.release-keys/*.pub`、以及**每一台发版机**。`release.py --check` 的前两条判据就在查这个
  （第 2 条另查 `Cargo.toml` 的 `repository` 与 `git remote` 是否指向同一个仓库 ——
  客户端把仓库地址编译进了 `env!("CARGO_PKG_REPOSITORY")`，不一致的表现是「检查得到、下载 404」）。

### 客户端怎么连（中文网络）

清单 url 写的是**直连地址**，三个中转前缀（`gh-proxy.com` / `ghproxy.net` / `gh-proxy.org`）
由客户端运行时拼 —— 不让所有人默认走第三方。检查时插件按顺序逐个 endpoint 试到成功为止，
下载失败就换下一条；上次成功的线路记在 `app_local_data_dir()/update-route.json`，
失败前进一格、成功保持不动（见 [desktop-shell.md](desktop-shell.md) 的「应用内更新」）。

⚠️ **中转前缀只能用于资源下载，不能拿去当网页链接** —— 打开会是空白页。
「在浏览器中打开 Releases」用的是直连地址。

### 这条链路只有第一次真实发版才能完整验证

`--check` 能证明清单是对的，但「下载 → 验签 → 装 → 重启」整条路只有走一次才知道。
第一次发版时确认四件事：

1. 旧版应用能检查到新版本（中转线路也在这一步被实测）；
2. 下载过程中后端 sidecar 被正确杀掉（否则安装器覆盖 `LiMoBackend.exe` 会失败在半路）；
3. 装在自定义目录的用户更新后仍在原目录（`/D=` 的作用）；
4. 更新完成后应用自动重启、文档与配置完好。

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
- `app.engines.embedding_dashscope` / `app.engines.embedding_local`（同上，2026-10-26 语义检索）
- `app.core.vector_store` / `app.core.vector_index`、`app.services.vector_runtime` / `vector_worker` /
  `vector_search`（`main.py` 的 startup/shutdown 与 `routers/search.py` 函数内导入）
- `app.core.search`（五个写点延迟导入 `update_index`）

⚠️ **`onnxruntime` / `tokenizers` 刻意不进 `collect_all`** —— 本地 embedding 引擎在打包版
按设计不可用（两者不在 `pyproject.toml`，桌面安装版没有 pip）。`embedding_local` 顶部零重依赖
import，进包安全，调用时才抛 `MissingDependency`。

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

---

## 开发与构建命令（2026-11-07 从 CLAUDE.md 迁入）

```bash
cd frontend
npx tauri dev       # Tauri 开发模式（需先启动后端 7413）
npx tauri build     # 生产构建 → NSIS/MSI 安装包
cargo check         # 仅 Rust 类型检查（不完整编译）
```

三条这里独有的约束：

- **`collect_all` 的返回格式与 `Analysis(binaries=[])` 不同**：`collect_all` 返回
  `(dest_name, src_path)`，而 `Analysis(binaries=[])` 期望 `(src_path, dest_dir)`。
  追加 binaries 时用 `(abs_src_path, '.')`。
- **OpenSSL DLL 版本冲突**：`cryptography` 携带的 `libcrypto-3-x64.dll`（~5MB）会覆盖
  Python 原生的（~8MB），导致 `_ssl.pyd` 加载失败（`ImportError: DLL load failed`），
  后端起不来。spec 中排除 cryptography 的 DLL，并显式添加 `sys.base_prefix/DLLs/` 下的原始版本。
  **每次新增依赖后验证**：直接跑 `LiMoBackend.exe`，看到
  `Uvicorn running on http://127.0.0.1:7413` 才算过。
- **`--distpath` 不能省**：不指定时输出到 `dist/`，而 Tauri 打包的是 `bin/` 里的旧文件。
