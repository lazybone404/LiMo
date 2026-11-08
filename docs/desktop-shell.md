# Tauri 桌面壳

> 本文是 [CLAUDE.md](../CLAUDE.md) 里「Tauri 桌面壳」一节，2026-11-07 外移。
> 读者是**改启动链路、启动失败屏、进程管理、宿主加速键、CORS** 的人。
> 打包流程见 [BUILD.md](BUILD.md)。

```
LiMo.exe (Rust) ──spawn──▶ python.exe (uvicorn :7413)
       │                              │
       ▼                              ▼
  WebView2 加载                 FastAPI 后端
  frontend/dist/                backend/
```

- **开发模式**：Vite dev server (:5173) + 手动启动后端，Tauri 加载 `devUrl`
- **生产模式**：`frontendDist` 静态文件 + `std::process::Command` 直接启动 PyInstaller 打包的 `LiMoBackend.exe`（非 Tauri sidecar()，避免 ACL 复杂度）
- **就绪探测**：Rust 端 HTTP GET `/health` 轮询（500ms 间隔，45s 超时），就绪后 emit `backend-ready` 事件（超时同理 emit `backend-startup-timeout`）。**前端以该事件为主要唤醒源，不再自己轮询 `/health`** —— 冷启动有 2.5s **静默期**（一条请求都不发，后端实测 ~2s 就绪），静默期后仅单次探测兜底「页面刷新时后端已在运行」的场景，另有 50s 硬兜底。⚠️ 前端轮询与 Rust 探测曾是**重复的**，而 WebView 里失败请求的报错 JS 抑制不掉 —— 详见「关键模式」#49
- **启动失败可见化**（2026-10-21；同日二轮补日志与排障闭环）：探测循环每 500ms 顺带 `try_wait` 子进程 —— **进程一退出就立刻报**（不再干等 45s），三个事件（`backend-startup-failed` / `backend-startup-timeout` / 命令 `get_backend_startup_failure`）共用一个 `StartupFailure` 形状：`stage`（`spawn`/`exited`/`timeout`）、`alive`、`exit_code`、`log_tail`、`log_path`/`log_dir`/`log_dir_writable`/`launcher_log_exists`/`backend_log_exists`/`exe_path`、`port_occupied`（**只在进程已退出时才探**，否则 7413 可能是它自己在监听）、`install_dir_writable`。⚠️ **判据是「有没有诊断」不是 `alive`**（2026-10-21 三轮改）：任何诊断都停在失败屏 `BackendStartupFailure`，放行改由用户点「仍然继续」—— 此前 `alive=true`（进程还在、只是没在 45s 内响应）走的是 `console.warn` + `initOnce()`，用户看到的是一屏 ERR_CONNECTION_REFUSED，而原因只进了控制台。`alive` 现在只决定失败屏上那句「进程仍在运行，可能只是启动慢」（慢 vs 死的下一步动作不同）。⚠️ **`isFatalStartupFailure` 已删**：它唯一的用途就是「什么时候自动放行」，判据存在一天就有人会把它当权威。⚠️ **日志目录是回退链**（`%LOCALAPPDATA%\LiMo\logs` → `%TEMP%\LiMo\logs` → 安装目录旁 `logs\`，取第一个**探针可写**的；全不可写时 `log_dir_writable=false` 就是「为什么没有日志」的答案）：单目录时安全软件拦截 / 磁盘满 / `LOCALAPPDATA` 缺失会让启动器与后端两段日志**同时为空**，而失败屏上一块空白读不出「日志系统自己失败了」。⚠️ `pick_log_dir(candidates)` **接收入参**：开发机上造不出「LOCALAPPDATA 只读」，只有传假候选列表才测得到「第一个写不进去就换下一个」（Rust 单测 5 条；`dir_writable` 的「不可写」用**父级是文件**构造 —— chmod 在管理员身份下会静默变成可写，测了等于没测）。⚠️ **事件 + managed state 两条路缺一不可**：进程在 WebView 加载完成前就死时 emit 会被静默丢弃，故 50s 硬兜底还会 `invoke('get_backend_startup_failure')` 主动拉一次。⚠️ `resolve_backend_exe` 是 `spawn_backend` 与诊断**共用的唯一解析**（否则诊断报的路径可能不是实际尝试拉起的那个）。
  ⚠️ **二轮的四条日志纪律**（全部是静默失效，见 CHANGELOG）：① 后端日志**追加**不截断（`open_backend_log`）—— `File::create` 会让「重试启动」把用户刚看到的崩溃证据清空；② `log_tail` 是**两段**（`build_log_tail` 拼 `[启动器]` + `[后端]`）—— DLL 缺失型故障后端一行不打印，只读后端日志恰在最需要证据时空白；启动器那段按 `SESSION_MARKER` 只取本次（`limo_startup.log` 跨会话 append）；③ `spawn_error` 必须进 summary —— 「exe 在但起不来」的真实原因（拒绝访问 os error 5）此前只落在日志文件里；④ 「找不到 exe」与「起不来」分两句文案（`exe_path.exists()` 判），处置完全不同（重装 vs 改环境）。
  ⚠️ **排障三件**：`exitCodeHint` 翻译 NTSTATUS（`-1073741515` → 缺 VC++ 运行库；**普通退出码不硬套解读**，有对照实验）；`diagnosticsText` 一键复制整块（用户不必在 `<pre>` 里跨行拖选）；安装路径警告**复用** `installWarningText` 的判据（不新建第二份文案，输入 `exe_path` + `install_dir_writable`），且**不看横幅的「已关闭」记忆** —— 那里关的是「每次开机提醒」，「解释当前失败」是另一件事。⚠️ stderr 为空的兜底文案必须点名**安装路径含中文**（中文用户名会让默认安装路径就含非 ASCII，而它正是「一行都不打印」的主力成因）
- **安装位置与运行无关**（2026-10-21 核实）：`exe_dir` 由 `current_exe()` 决定，`_internal` / `ffmpeg/` / `searxng/` 全部相对它；日志与数据走 `LOCALAPPDATA` / `~/.limo` ⇒ **换盘符、换目录都不影响，重装换目录也不丢数据**。⚠️ 真正的三类风险：装到 `C:\Program Files`（安装器是 `currentUser` 模式，**安装期**就失败，不是静默）、路径含中文/空格（启动期，失败屏与顶栏横幅都会提示）、安装不完整/被隔离（失败屏现在直接说「找不到 exe」）。`installer.nsi` 无条件插 `MUI_PAGE_DIRECTORY`，所以这个场景是真实存在的
- **进程管理**：`SidecarHandle(Mutex<Option<Child>>)` 持有进程句柄；窗口关闭 → `RunEvent::ExitRequested` → `child.kill()`
- **重启命令**：`restart_backend_sidecar` Tauri command，kill 旧进程 → spawn 新进程 → 重新探测
- **诊断**：`analyze_install_path()` 检查路径非 ASCII/空格/父目录可写性；`app_log()` 写 `limo_startup.log`；后端 stderr 重定向到 `limo_backend_stderr.log`
- **PATH 增强**：`enhance_path()` 优先追加安装目录下的 `ffmpeg/`（内置），再追加系统常见路径，确保 yt-dlp 等工具能找到 ffmpeg。同时设 `FFMPEG_BIN_PATH` 环境变量指向内置路径
- **宿主加速键放行**（2026-10-05，网页版 vs 打包版的唯一落点）：`.setup()` 里对主窗口注册 WebView2 `AcceleratorKeyPressed` 回调（`#[cfg(windows)] mod webview_keys`），**只对应用自己在用的键**设 `IsBrowserAcceleratorKeyEnabled = FALSE` —— 浏览器跳过自己的处理、事件继续传播到网页，其余（Ctrl+P 打印 / F5 刷新 / Ctrl+F 查找 / DevTools）**原样不动**。⚠️ 不设 `Handled`（那是吞掉、网页也收不到）、不动全局 `AreBrowserAcceleratorKeysEnabled`；键清单与 `App.tsx::handleAppKeyDown` 逐键对应，由 `appShortcutKeys.test.ts` 跨语言守卫（模式 #65）
- **API 适配**：`client.ts` 检测 `__TAURI_INTERNALS__` → 生产用 `http://127.0.0.1:7413/api`，开发用 `/api`（Vite proxy）
- **CORS**：`main.py` 使用 **Origin 白名单**（`http(s)://tauri.localhost` + `tauri://localhost` + 本机 localhost 任意端口）+ `_ForceCORSMiddleware` **仅白名单 origin 回显凭证**——阻隔任意恶意网页跨站读取本地后端（2026-08-02 从 `allow_origin_regex=".*"` 收紧；⚠️ 生产 WebView2 实际 origin 是 `http://tauri.localhost`，白名单必须含它）
- **配置**：`tauri.conf.json` — 1400×900 窗口 + `maximized: true` + `com.limo.app` 标识
- **依赖**：Rust 端 `tauri` (features=["devtools"]) + `tauri-plugin-shell` + `tauri-plugin-log`

---

## 应用内更新（2026-11-08）

安装版可以从 GitHub Release 检查并安装新版本：读 `latest.json` → 比版本 → 下载 →
minisign 验签 → 起 NSIS 安装器 → 重启。代码在 [src-tauri/src/updater.rs](../frontend/src-tauri/src/updater.rs)，
发版侧（签名与生成清单）见 [BUILD.md](BUILD.md) 的「发版：签名与 latest.json」。

- **两个命令，界线是「谁做决定」**：`check_update` 只查（返回 `currentVersion` 与可选的
  `update`），`install_update` 才装（`Channel` 推进度）。检查可以静默，**安装必须由人点** ——
  代码里没有任何自动下载的入口。
- **线路表在 Rust 侧唯一一份**：`tauri.conf.json` 的 `plugins.updater` **不写 endpoints**，
  由 `app.updater_builder().endpoints(...)` 运行时统一覆盖。仓库地址取自
  `env!("CARGO_PKG_REPOSITORY")`，清单地址是 `{repo}/releases/latest/download/latest.json`。
  四条线路 = 直连 + 三个中转前缀；上次成功的线路记在 `app_local_data_dir()/update-route.json`，
  **失败前进一格并写回，成功保持不动**（不记的话，中文网络下每次启动都要先在直连上失败一轮）。
- **检查用 builder 的 `timeout(12s)`，下载用 `update.timeout = Some(30min)`。**
  插件 `update_from_release()` 里 `Update.timeout` 写死为 `None`，builder 的 timeout **不会**
  带到下载请求上 —— 不显式设就是「永不超时」，卡住的连接会把界面挂死。这条此前判断错过一次。
- **Windows 上 `install()` 会 `std::process::exit(0)`**，不经过 `RunEvent::Exit`。所以
  `on_before_exit` 里先 `stop_backend_sidecar_blocking()`（`take()` → `kill()` → 有界等待
  `try_wait` 5s），再手动调一次 `app.cleanup_before_exit()` —— **`on_before_exit` 是覆盖而非追
  加**，插件 `UpdaterBuilder::new()` 默认挂的就是 `cleanup_before_exit`，不补这一句 Tauri 的
  默认清理就丢了。后端没杀干净的表现是安装器覆盖 `LiMoBackend.exe` 时文件被锁、NSIS 中途
  失败，留下一个半更新的安装目录。
- **NSIS 补 `/D=<当前 exe 所在目录>`**（`installer_arg`，插件保证它排在内部参数之后）。
  模板里的 `RestorePreviousInstallLocation` 从注册表恢复，但实测出现过「装在自定义目录、
  更新后回落到默认目录」。
- **前端不知道安装成功**：Windows 分支的 Promise 永远不会 resolve（进程已退出）。所以
  `UpdateCard` 只用 `.catch()` 处理失败，**不能**在 `.then()` 里写「安装完成」——
  那段代码不会执行（见 [api/update.ts](../frontend/src/api/update.ts) 的注释）。
- **有任务在跑时禁用安装**：安装会让应用退出，正在跑的生成 / 转录 / 导入没有任何断点续传。
  判据是 `processingTasks` 里任一条 `status === 'running'`（失败条目保留 5 秒，不算「在跑」）。
- **启动时静默检查**：`App.tsx` 在后端就绪 20 秒后查一次，24 小时节流；同一个版本只提醒一次，
  被跳过的版本不再提醒（三个字段都在 [lib/update-prefs.ts](../frontend/src/lib/update-prefs.ts)）。
  检查失败**静默**，手动检查在 设置 → 通用，那条路会把错误原样显示出来。
- **`update-route.json` 读失败一律回落 0（直连）** —— 线路偏好是纯优化，不能因为它坏掉就
  检查不了更新。
