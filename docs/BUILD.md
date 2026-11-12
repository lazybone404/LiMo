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

## 安装器的两个必需配置（2026-11-10 实测得出）

`tauri.conf.json` 的 `bundle.windows.nsis` 里有两项不是可选项，缺任何一项都会在真实装机上出问题，而构建过程不会报错。

```json
"nsis": {
  "template": "nsis/installer.nsi",
  "installerHooks": "nsis/hooks.nsh",
  "installMode": "perMachine"
}
```

### `installMode: "perMachine"` —— 不给它，装到一半就失败

默认值是 `currentUser`，安装器落点是 `%LOCALAPPDATA%\LiMo`，而**那个目录同时是 LiMo 的数据目录**（`logs/`、`secrets.bin` 就住在那儿），程序文件和数据会混在一起。

更麻烦的是升级路径：如果机器上原本有一份装在 `C:\Program Files\LiMo`（早期版本留下的），安装器会沿用那个目录，但自己的 manifest 是 `asInvoker` —— 不提权。结果就是往 Program Files 写第一个文件时被拒：

```
Error opening file for writing: C:\Program Files\LiMo\_internal\aiohttp\_headers.pxi
```

报错停留在哪个文件是随机的（取决于解包顺序），很容易被误判成「文件被占用」。实测排除过的说法：

- **不是文件锁**：应用完全退出后跑，退出码仍是 1、零写入，`_headers.pxi` 在运行时也不会被任何进程打开；
- **不是磁盘满**：当时 C 盘剩 133 GB；
- **不是安装包损坏**：SHA256 与 Release 上的一致。

根因是权限：`icacls "C:\Program Files\LiMo"` 显示 `BUILTIN\Users:(RX)`，普通用户只能读和执行。改成 `perMachine` 后 manifest 变成 `requireAdministrator`，双击直接弹 UAC，安装落点固定为 `Program Files`。

> 验证方法不用装机：从 exe 里把 manifest 读出来看一行即可。`perMachine` 应为 `requireAdministrator`，默认值应为 `asInvoker`。

### `installerHooks` —— 不给它，覆盖安装会留下占端口的孤儿

模板自带的 `CheckIfAppIsRunning` 只按主程序名关进程，也就是只认识 `limo.exe`、不认识侧车 `LiMoBackend.exe`。覆盖安装时主程序被关掉，侧车继续活着并占着 `127.0.0.1:7413`，**安装器退出码 0、看起来一切正常**，但新版本启动时后端起不来，用户看到的是启动失败屏。

`nsis/hooks.nsh` 在 `NSIS_HOOK_PREINSTALL` 与 `NSIS_HOOK_PREUNINSTALL` 里各杀一次侧车：

```nsh
nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM LiMoBackend.exe'
```

只在 PREINSTALL 杀侧车、不杀 `limo.exe`：主程序交给模板自己的检查处理，交互模式下它会先问「是否关闭正在运行的应用」，比直接杀掉礼貌。

---

## 两种安装包：各走各的更新（2026-11-12 实测）

`bundle.targets: "all"` 会同时产出 NSIS 与 MSI，两种都发给用户。应用内更新也会**按当前
这份是怎么装的**去取对应的包，不会再出现「更新一次安装格式就变了」。

### 客户端怎么知道自己是哪种装的

bundler 在打包时把类型**逐格式 patch 进主程序二进制**（`__TAURI_BUNDLE_TYPE` 那个静态串）。
2026-11-12 的构建日志直接印证了这个顺序 —— 打 msi 之前 patch 成 msi，打 nsis 之前
patch 成 nsis：

```
Info Patching …\target\release\limo.exe with bundle type information: msi
     Running candle … light … bundle\msi\LiMo_0.5.26_x64_en-US.msi
Info Patching …\target\release\limo.exe with bundle type information: nsis
     Running makensis … bundle\nsis\LiMo_0.5.26_x64-setup.exe
```

所以 `bundle/msi/` 里嵌的 exe 标记是 MSI，`bundle/nsis/` 里那份是 NSIS。
（`target/release/limo.exe` 本身打完包会被还原成 `UNK`，别拿它当判据。）

`tauri-plugin-updater`（≥2.10）在 `Updater::get_urls()` 里按这个标记查清单：先试
`windows-x86_64-{nsis|msi}`，取不到再回落 `windows-x86_64`。因此 `latest.json` 写三个键：

| 键 | 指向 | 谁读它 |
|---|---|---|
| `windows-x86_64-nsis` | `.exe` + 它的 `.sig` | `.exe` 装的客户端 |
| `windows-x86_64-msi` | `.msi` + 它的 `.sig` | `.msi` 装的客户端 |
| `windows-x86_64` | 与 nsis 键逐字相同 | 0.5.25 及更早的客户端（二进制里是 `UNK`，只认这个键） |

⚠️ **三个键都要在。** 少了 msi 键时，MSI 客户端会回落到兜底键拿到 NSIS 包，更新完就是
两份并存、控制面板两条 —— 这个回落是插件写死的，只能靠清单齐全来避免，`release.py
--check` 会拦。

### MSI 更新不能带 `/D=`

`updater.rs` 原来无条件给安装器加 `/D=<当前目录>`（NSIS 自定义目录会被重置，P-ai v0.9.10
踩过）。这条参数**只对 NSIS 有效**：插件把所有 `installer_arg` 原样追加在 msiexec 那条
命令行末尾，多一个 `/D=…` 就是未知选项，msiexec 弹出用法框、安装不执行 —— 症状是
「下载成功、验签通过、然后什么都没有」。

现在按 `bundle_type()` 判断，明确是 MSI 才跳过。`bundle_type()` 返回 `None`（`tauri dev`、
未 patch 的二进制）时仍按 NSIS 处理，保持原行为不变。

### 手动交叉安装仍然是两份

自动更新各走各的之后，交叉只剩**手动下载安装包**这一种场景：

| 场景 | 结果 |
|---|---|
| 已装 NSIS → 手动装 MSI | 程序文件仍是 `C:\Program Files\LiMo` 一份（**两者同目录**），控制面板多出**第二条** LiMo |
| 已装 MSI → 手动装 NSIS（修复前） | 同上，两条卸载项，开始菜单出现**两个同名图标** |
| 已装 MSI → 手动装 NSIS（2026-11-12 起） | MSI 那条被自动移除，只剩一条 |

机制：两个安装器各按自己的登记项判断「有没有装过」，而那两套键互不可见、还分住两个
注册表视图：

- NSIS（`perMachine`）：落 `C:\Program Files\LiMo`，登记 `HKLM\...\Uninstall\LiMo`（**32 位视图**）
- MSI（`ALLUSERS=1`，也是 perMachine）：**同样落 `C:\Program Files\LiMo`**，登记
  `HKLM\...\Uninstall\{ProductCode}`（**64 位视图**）

⚠️ MSI 的落点此前在本文档里写成 `%LOCALAPPDATA%\LiMo`，那是错的。2026-11-12 用
`msilib` 读 0.5.25 与 0.5.26 两版 MSI 的 `Directory` 表，`INSTALLDIR` 的父目录都是
`ProgramFiles64Folder`，生成的 `main.wxs` 里也写着 `PlatformProgramFilesFolder =
"ProgramFiles64Folder"`。

**两者同目录**意味着「两份并存」不是两份独立的程序：文件被后装的那份覆盖，而控制面板
留着两条卸载项 —— 卸载任意一条都会删掉共用的程序文件，另一条变成指向空目录的僵尸。
这是交叉安装比「各占一个目录」更糟的地方，也是自动更新必须各取各包的另一个理由。

NSIS 模板的 `RestorePreviousInstallLocation` 从 `...\Uninstall\LiMo` 读旧目录，MSI 那个键是
GUID，读不到，于是回落到默认目录再装一份。

### NSIS 安装器会主动清掉 MSI 那份

[nsis/hooks.nsh](../frontend/src-tauri/nsis/hooks.nsh) 的 `NSIS_HOOK_PREINSTALL` 在覆盖安装前
枚举卸载登记，找到 MSI 那份（子键名是 `{GUID}`、DisplayName 与 Publisher 与本产品一致）就
`msiexec /x ... /qn /norestart` 卸掉它，再继续安装。

这条只兜「用户手动下载了另一种格式的安装包」——自动更新不会走它（MSI 用户的更新直接
交给 msiexec，不经过 NSIS 安装器）。实测这种切换不丢数据：`~/.limo` 的文档库与
`%LOCALAPPDATA%\LiMo` 下的 `logs/`、`secrets.bin` 都还在，只有程序文件被删。

⚠️ **反方向（NSIS → MSI）没有钩子。** Tauri 不开放 WiX 侧的自定义点，只能靠下载页写明
「换格式前先卸载旧的」。

### 模板自带的 WiX 检测为什么没救场

Tauri bundler 从 1.3.0 起就有「检测到旧 WiX 安装时提示卸载」
（[tauri-apps/tauri#6859](https://github.com/tauri-apps/tauri/issues/6859)，落在
`installer.nsi` 的 `PageReinstall`）。它在真机上一直没生效，有两个独立原因：

1. **它住在自定义页面函数里。** `/S` 静默安装时 NSIS 不执行任何页面函数，`$WixMode`
   停在默认值 0，后面的 `PageLeaveReinstall` 也就不会去卸 WiX 那份。
2. **它没切注册表视图。** 安装器是 32 位进程，读 `HKLM\SOFTWARE\...` 会被 WOW64 重定向到
   `WOW6432Node`，而 MSI 的登记在 64 位视图。整份 `installer.nsi` 里没有一处 `SetRegView`，
   所以即使页面跑了也读不到。

`hooks.nsh` 两条都补上了：不看当前是不是静默，PREINSTALL 一律自查一遍；查之前
`SetRegView 64`，查完恢复 32（后面的安装步骤照旧写 32 位视图）。

### 第三个坑：判据不要用 `${StrLoc}`

`StrFunc.nsh` 的 `${StrLoc}` 内部会用 `$R1`/`$R2` 当临时变量，把结果变量传成这几个时
会**静默归零**。2026-11-12 实测：明明命中 MSI 项，返回值却是 0，循环继续，最后选中本产品
自己的 NSIS 卸载项，执行 `msiexec /x LiMo` 必然失败。判据改用键名（`{` 开头）后正常 ——
本产品自己的 NSIS 项键名是 `LiMo`，两者靠这一点就能分开。

### 用户侧规则

- **同一格式内可以覆盖升级**；跨格式是两份并存（NSIS 方向有钩子清掉，反向没有）。
- 下载页仍推荐普通用户用 NSIS；MSI 是给需要标准 MSI 部署的场景（Intune / SCCM /
  组策略）用的。**两种格式都走应用内更新**，各取各的包。
- WOW6432Node 下可能残留一条 `LiMo` 卸载项，指向已不存在的 `Program Files\LiMo\uninstall.exe`
  （[tauri-apps/tauri#16183](https://github.com/tauri-apps/tauri/issues/16183)，卸载器用了
  `_?=` 无法自删）。它不影响下次安装，`RestorePreviousInstallLocation` 读到的仍是默认目录。

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
# ⚠️ `TAURI_SIGNING_PRIVATE_KEY` 要的是**密钥内容**，不是路径。
# 写成路径时 CLI 2.11.4 会拿它当 base64 解，报
# `failed to decode base64 secret key: Invalid symbol 58, offset 1`（偏移 1 是 `C:` 的冒号）。
export TAURI_SIGNING_PRIVATE_KEY="$(cat C:/WorkPlace/Tool/LiMo/.release-keys/limo-updater.key)"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="$(cat C:/WorkPlace/Tool/LiMo/.release-keys/password.txt)"

cd frontend
npx tauri build --config src-tauri/tauri.release.conf.json
# → target/release/bundle/nsis/LiMo_{v}_x64-setup.exe
#   target/release/bundle/nsis/LiMo_{v}_x64-setup.exe.sig   ← 少了它 .exe 用户验签必失败
#   target/release/bundle/msi/LiMo_{v}_x64_en-US.msi
#   target/release/bundle/msi/LiMo_{v}_x64_en-US.msi.sig    ← 少了它 .msi 用户验签必失败
```

### 生成并校验清单

```bash
python scripts/release.py --write          # → dist-release/latest.json
python scripts/release.py --check          # 发版门禁，退出码 0/1/2
python scripts/release.py --print-upload   # 打印 gh release create 命令 + SHA256
```

`--print-upload` 打出来的命令含**五个文件**：NSIS 安装包、它的 `.sig`、MSI、它的 `.sig`、
`latest.json`。少任何一个 `.sig` 时脚本直接报错退出（退出码 1）而不打印半条命令 ——
缺签名的 Release 在页面上看不出任何异常，只有对应那一半客户端下载完才会「装不上」。

`--check` 的判据见脚本里 `collect_problems` 的注释。其中最关键的一条是
**清单每个键的 `signature` 必须与它自己那份产物的 `.sig` 逐字相同**：重新构建后忘了
重跑 `--write`，签名与包对不上，客户端下载完验签失败、更新装不上，而发版侧一切正常。
把 exe 的签名填进 msi 键是同一类错误，症状一致。

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
