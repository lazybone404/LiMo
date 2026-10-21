use std::env;
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager};

const BACKEND_STARTUP_TIMEOUT_SECS: u64 = 45;
const BACKEND_PORT: u16 = 7413;
/// 诊断里回传的日志尾部字节数。取 8 KiB 足够装下一条 Python traceback，
/// 又不会让事件 payload 大到前端渲染卡顿（日志文件本身可能几十 MB）。
const STDERR_TAIL_BYTES: u64 = 8 * 1024;
/// 后端日志超过这个大小就轮转一份 `.1.log` —— 它是**追加**写入的（见
/// `open_backend_log`），不设上限会随使用年限无限长，而用户并不会清理它。
const BACKEND_LOG_ROTATE_BYTES: u64 = 4 * 1024 * 1024;
/// 每次冷启动在 `limo_startup.log` 里留的会话分隔行。
/// ⚠️ 那份日志是**跨会话累积**的（append），失败屏只该显示**本次**启动的信息 ——
/// 否则上周那次成功启动的 `spawning ...` 会混进来，看起来像本次的证据。
const SESSION_MARKER: &str = "===== LiMo 启动 =====";

struct SidecarHandle(Mutex<Option<Child>>);

/// 后端启动失败的原因 —— 与 `backend-startup-failed` 事件同形，也存进
/// `BackendStartupState` 供前端在事件早于监听时主动拉取（进程在 WebView
/// 加载完成前就死掉，emit 会被丢掉，故事件与 state 两条路缺一不可）。
#[derive(serde::Serialize, Clone)]
struct StartupFailure {
    /// `spawn`（进程都没创建出来）/ `exited`（起来后死掉）/ `timeout`（还活着但没就绪）
    stage: String,
    /// 给用户看的一句话结论
    summary: String,
    /// 后端进程此刻是否还活着（`timeout` 下为 true，另两种为 false）
    alive: bool,
    exit_code: Option<i32>,
    /// **两段日志的尾部**，各自带 `[启动器]` / `[后端]` 行首前缀（无输出时为空串）。
    ///
    /// ⚠️ 不只是后端 stderr：启动器自己那份才是「在哪个目录、拉起哪个 exe、为什么
    /// 拉不起来」的唯一记录 —— `spawn_backend` 返回的错误字符串此前只写进
    /// `limo_startup.log`，失败屏上看不到，等于「exe 存在但被拒绝访问」这类故障
    /// 在界面上只是一句「没能启动」。
    log_tail: String,
    log_path: String,
    /// 实际使用的日志目录（可能与 `%LOCALAPPDATA%\LiMo\logs` 不同 —— 首选不可写时
    /// 会回退到 `%TEMP%\LiMo\logs` 或安装目录旁的 `logs\`）。
    log_dir: String,
    /// 日志目录是否真的可写。**日志为空时它是唯一的解释**：
    /// false = 日志系统自己没能落盘（安全软件拦截 / 磁盘满 / 环境变量异常），
    /// true 且两份文件都不存在 = 后端一行输出都没产生（DLL 缺失等的典型形态）。
    log_dir_writable: bool,
    launcher_log_exists: bool,
    backend_log_exists: bool,
    exe_path: String,
    /// TCP 能连上 7413 但 /health 不通 —— 通常是端口被别的程序占着
    port_occupied: bool,
    /// 安装目录是否可写（在它旁边写一个探针文件来判）。
    /// ⚠️ 只回**这一个布尔**、不回完整路径诊断：`exe_path` 已经在前端，
    /// 「含空格 / 含非 ASCII」由它当场算得出来 —— 同一个事实不回两遍。
    install_dir_writable: bool,
}

struct BackendStartupState(Mutex<Option<StartupFailure>>);

/// 用户数据目录 — 有写入权限（Program Files 只读）
fn user_data_dir() -> PathBuf {
    // 优先 LOCALAPPDATA（C:\Users\xxx\AppData\Local），
    // 回退 APPDATA（C:\Users\xxx\AppData\Roaming）
    let base = env::var("LOCALAPPDATA")
        .or_else(|_| env::var("APPDATA"))
        .unwrap_or_else(|_| {
            env::var("USERPROFILE").unwrap_or_else(|_| ".".to_string())
        });
    PathBuf::from(base).join("LiMo").join("logs")
}

/// 日志目录候选（按优先级）。
///
/// ⚠️ 这是「日志系统自己失败」的那条路：`%LOCALAPPDATA%` 不可写（被安全软件拦、
/// 磁盘满、环境变量缺失时 `user_data_dir()` 会退到 `"."` 即安装目录、而安装目录
/// 常常只读）时，日志会**一条都留不下**，失败屏上看起来就是「没有任何日志」——
/// 用户与开发者同时失去唯一的证据。所以日志目录不能只有一个。
fn log_dir_candidates() -> Vec<PathBuf> {
    let mut v = vec![user_data_dir()];
    // %TEMP% 基本总是可写，且与安装位置、账户同步策略都无关。
    v.push(env::temp_dir().join("LiMo").join("logs"));
    if let Ok(exe) = env::current_exe() {
        if let Some(dir) = exe.parent() {
            v.push(dir.join("logs"));
        }
    }
    v
}

/// 目录能不能真正写进去（`create_dir_all` 成功 ≠ 可写）。
fn dir_writable(dir: &Path) -> bool {
    if std::fs::create_dir_all(dir).is_err() {
        return false;
    }
    let probe = dir.join(".limo_log_probe");
    let ok = std::fs::write(&probe, b"x").is_ok();
    let _ = std::fs::remove_file(&probe);
    ok
}

/// 从候选里挑第一个真正可写的。
///
/// ⚠️ **接收入参而不是自己去取环境**：这是整条回退链里唯一有分支的判定，
/// 写成 `log_dir()` 的内联循环就只剩「跑一次看日志落哪」这一种验证方式，
/// 而那条路要坏环境（`%LOCALAPPDATA%` 只读）—— 开发机上根本造不出来。
/// 传一个假的候选列表，就能在 CI 上钉住「第一个写不进去时必须换下一个」。
fn pick_log_dir(candidates: &[PathBuf]) -> Option<PathBuf> {
    candidates.iter().find(|d| dir_writable(d)).cloned()
}

/// 本次会话实际使用的日志目录：第一个真正可写的候选。
/// 全部不可写时返回首选路径 —— 此时日志内容为空，而失败屏上的
/// `log_dir_writable=false` 就是「为什么没有日志」的答案。
///
/// ⚠️ **会话内缓存**：`dir_writable` 每次都要写一个探针文件。不缓存的话
/// `app_log` 每打一行就要写+删一次探针（`limo_startup.log` 一轮启动十几行）。
/// 更要紧的是**一致性**：启动器日志、后端 stderr、失败屏读回必须落在同一个目录，
/// 每次重算等于让它们在目录状态变化时各走各的。
/// 诊断字段 `log_dir_writable` 仍是**现测**的（见 `build_startup_failure`）——
/// 那一项要反映此刻的真实状态，而不是选目录时的快照。
fn log_dir() -> PathBuf {
    static CACHED: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();
    CACHED
        .get_or_init(|| pick_log_dir(&log_dir_candidates()).unwrap_or_else(user_data_dir))
        .clone()
}

/// 启动器自己的日志（`limo_startup.log`）。
///
/// ⚠️ **目录不由调用方给**：此前签名是 `app_log(dir: &Path, msg)`，而函数体早就不看
/// 那个参数了 —— 十二个调用点都传 `exe_dir`，读起来像「日志写在安装目录里」，实际
/// 写在 `log_dir()`（回退链挑出来的那个）。日志往哪写只有一处判据，就不该在签名上
/// 留一个会被误读的入口。
fn app_log(msg: &str) {
    let dir = log_dir();
    let _ = std::fs::create_dir_all(&dir);
    let log_path = dir.join("limo_startup.log");
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs();
    let line = format!("[{}] {}\n", ts, msg);
    let _ = std::fs::OpenOptions::new().append(true).create(true)
        .open(&log_path)
        .map(|mut f| { let _ = f.write_all(line.as_bytes()); });
}

/// UNIX 秒 → `YYYY-MM-DD HH:MM:SS UTC`。
///
/// 不引 chrono：这里只要一个能跟用户口述的「几点钟」对上号的时间戳，
/// 民用历法换算十几行算术就够。**标 UTC 是为了不骗人** —— 本地时区要用
/// Windows API 才拿得到，写个假本地时间比写清楚 UTC 更糟。
fn fmt_ts(secs: u64) -> String {
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    // 民用历法（Howard Hinnant: days_from_civil 的逆运算）
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { yoe + era * 400 + 1 } else { yoe + era * 400 };
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02} UTC",
        y, m, d, rem / 3600, (rem % 3600) / 60, rem % 60
    )
}

/// 打开后端日志 —— **追加**写入，并在开头留一行时间分隔。
///
/// ⚠️ 这里曾是 `File::create`（每次 spawn 截断）。后果是：用户看到失败屏、
/// 读到崩溃日志，点「重试启动」→ 下一次 `spawn_backend` 先把那份日志清空 →
/// 重试往往又失败 → **屏幕上只剩空白，第一次的证据在用户眼前被抹掉**。
/// 崩溃日志必须活过重启，这是「让用户能排障」的前提。
fn open_backend_log() -> Option<std::fs::File> {
    let dir = log_dir();
    let _ = std::fs::create_dir_all(&dir);
    let path = dir.join("limo_backend_stderr.log");
    if std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0) > BACKEND_LOG_ROTATE_BYTES {
        // 只留一份历史：排障关心的是「最近一次」，而不是一整部编年史。
        let _ = std::fs::rename(&path, dir.join("limo_backend_stderr.1.log"));
    }
    let mut f = std::fs::OpenOptions::new().create(true).append(true).open(&path).ok()?;
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs();
    let _ = writeln!(f, "\n===== 后端启动 {} =====", fmt_ts(secs));
    Some(f)
}

/// 给多行文本逐行加来源前缀，让两段日志拼在一起后仍分得清谁说。
fn prefix_lines(tag: &str, text: &str) -> String {
    text.lines()
        .map(|l| format!("[{}] {}", tag, l))
        .collect::<Vec<_>>()
        .join("\n")
}

/// 读 `limo_startup.log` 里**本次启动以来**的尾部。
///
/// 那份日志跨会话累积，直接取尾部会把上周的启动混进来。每次冷启动写一行
/// `SESSION_MARKER`，这里只取最后一个标记之后的内容；找不到标记（老版本留下的
/// 日志 / 标记行恰好被截断）就退回整段尾部 —— 有总比没有好。
fn read_session_log(path: &Path, max_bytes: u64) -> String {
    let text = read_tail(path, max_bytes);
    match text.rfind(SESSION_MARKER) {
        Some(i) => text[i..].to_string(),
        None => text,
    }
}

/// 组装失败屏要显示的日志：启动器（在哪个目录、拉哪个 exe、为什么拉不起来）
/// 在前，后端自己的输出在后 —— 两段连起来就是一条完整的启动时间线。
fn build_log_tail() -> String {
    let dir = log_dir();
    let mut parts: Vec<String> = Vec::new();
    let launcher = read_session_log(&dir.join("limo_startup.log"), STDERR_TAIL_BYTES);
    if !launcher.trim().is_empty() {
        parts.push(prefix_lines("启动器", &launcher));
    }
    let backend = read_tail(&dir.join("limo_backend_stderr.log"), STDERR_TAIL_BYTES);
    if !backend.trim().is_empty() {
        parts.push(prefix_lines("后端", &backend));
    }
    parts.join("\n")
}

/// 在安装目录旁边写一个探针文件，判断它可不可写。
fn probe_dir_writable(dir: &Path) -> bool {
    let probe = dir.join(".limo_write_probe");
    match std::fs::write(&probe, b"x") {
        Ok(_) => { let _ = std::fs::remove_file(&probe); true }
        Err(_) => false,
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info).build(),
                )?;
            }

            let exe_path = env::current_exe().unwrap_or_default();
            let exe_dir = exe_path.parent().map(|p| p.to_path_buf()).unwrap_or_default();
            // 会话分隔行：失败屏只显示**本次**启动的启动器日志（见 read_session_log）。
            app_log(SESSION_MARKER);
            app_log(&format!("LiMo dir={}", exe_dir.display()));

            // 安装路径诊断
            let diag = analyze_install_path(&exe_path);
            if diag.path_has_space || diag.path_has_non_ascii || !diag.parent_writable {
                app_log(&format!("WARNING space={} nonascii={} writable={}",
                    diag.path_has_space, diag.path_has_non_ascii, diag.parent_writable));
                let app_handle = app.handle().clone();
                std::thread::spawn(move || {
                    std::thread::sleep(std::time::Duration::from_millis(1500));
                    if let Some(w) = app_handle.get_webview_window("main") {
                        let _ = w.emit("backend-warning", &diag);
                    }
                });
            }

            // ⚠️ SidecarHandle 与 BackendStartupState 都**必须无条件 manage**：
            //    `manage` 同一类型两次会 panic，所以不能一边 manage 一边不 manage。
            //    进程没创建出来时句柄为 None，探测线程据此立刻收工（不再干等 45s）。
            let (child_opt, startup_failure) = match spawn_backend(&exe_dir) {
                Ok(child) => {
                    app_log("spawn ok");
                    (Some(child), None)
                }
                Err(e) => {
                    app_log(&format!("spawn FAILED: {}", e));
                    eprintln!("后端启动失败: {}", e);
                    let failure = build_startup_failure(&exe_dir, "spawn", false, None, Some(e.as_str()));
                    (None, Some(failure))
                }
            };
            app.manage(SidecarHandle(Mutex::new(child_opt)));
            app.manage(BackendStartupState(Mutex::new(startup_failure.clone())));

            // spawn 阶段就失败时 WebView 还没加载完，emit 会被丢掉 —— 延时重发，
            // 前端另有 `get_backend_startup_failure` 主动拉取兜底（两条路缺一不可）。
            if let Some(failure) = startup_failure {
                let app_handle = app.handle().clone();
                std::thread::spawn(move || {
                    std::thread::sleep(std::time::Duration::from_millis(1500));
                    emit_startup_failure(&app_handle, &failure);
                });
            } else {
                spawn_backend_ready_probe(app.handle().clone());
            }

            // 打包版：把 Ctrl+W / Ctrl+± / Ctrl+0 等从 WebView2 加速键手里放行到网页，
            // 否则 `App.tsx::handleAppKeyDown` 在打包版根本不触发（详见 webview_keys 注释）
            if let Some(w) = app.get_webview_window("main") {
                if let Err(e) = w.with_webview(|pw| webview_keys::allow_app_shortcuts(&pw)) {
                    app_log(&format!("webview_keys 注册失败: {}", e));
                }
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            restart_backend_sidecar,
            detect_components,
            export_pdf,
            get_backend_startup_failure,
            open_log_dir,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            match event {
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit => {
                    kill_backend_sidecar(app_handle);
                }
                _ => {}
            }
        });
}

fn kill_backend_sidecar(app_handle: &tauri::AppHandle) {
    if let Some(state) = app_handle.try_state::<SidecarHandle>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(ref mut child) = *guard {
                eprintln!("[shutdown] killing backend");
                let _ = child.kill();
            }
        }
    }
}

// ── 启动后端 ──

/// 定位后端可执行文件 —— `spawn_backend` 与启动失败诊断**共用同一份**，
/// 否则诊断里报的路径可能不是实际尝试拉起的那一个。
fn resolve_backend_exe(exe_dir: &Path) -> PathBuf {
    // 优先 PyInstaller 打包的 exe（Tauri v2 externalBin 会自动加平台后缀）
    let plain_exe = exe_dir.join("LiMoBackend.exe");
    let target_exe = exe_dir.join(format!("LiMoBackend-{}.exe", std::env::consts::ARCH));
    // 实际上 Tauri 加的是完整 target triple，尝试匹配
    if target_exe.exists() {
        target_exe
    } else if plain_exe.exists() {
        plain_exe
    } else {
        // 模糊匹配：找 LiMoBackend*.exe
        std::fs::read_dir(exe_dir)
            .ok()
            .and_then(|mut d| d.find_map(|e| {
                e.ok().and_then(|f| {
                    let n = f.file_name().to_string_lossy().into_owned();
                    n.starts_with("LiMoBackend").then(|| f.path())
                })
            }))
            .unwrap_or(plain_exe)
    }
}

fn spawn_backend(exe_dir: &Path) -> Result<Child, String> {
    let backend_exe = resolve_backend_exe(exe_dir);
    if backend_exe.exists() {
        let log_path = log_dir().join("limo_backend_stderr.log");
        app_log(&format!("spawning {} -> {}", backend_exe.display(), log_path.display()));

        // 追加而非截断；失败则丢弃输出（但启动器日志仍记着这件事）
        let log_file = open_backend_log();
        if log_file.is_none() {
            app_log("WARNING 后端日志文件打不开，后端输出将被丢弃");
        }
        let mut cmd = Command::new(&backend_exe);
        cmd.current_dir(exe_dir);
        match log_file {
            Some(f) => match f.try_clone() {
                Ok(stdout_f) => { cmd.stdout(stdout_f); cmd.stderr(f); }
                Err(_) => { cmd.stdout(Stdio::null()); cmd.stderr(Stdio::null()); }
            },
            None => { cmd.stdout(Stdio::null()); cmd.stderr(Stdio::null()); }
        }

        let current_path = env::var("PATH").unwrap_or_default();
        cmd.env("PATH", &enhance_path(&current_path));

        let ffmpeg_dir = exe_dir.join("ffmpeg");
        if ffmpeg_dir.exists() {
            cmd.env("FFMPEG_BIN_PATH", ffmpeg_dir.to_string_lossy().to_string());
        }
        let searxng_dir = exe_dir.join("searxng");
        if searxng_dir.exists() {
            cmd.env("SEARXNG_BIN_PATH", searxng_dir.to_string_lossy().to_string());
        }

        let child = cmd.spawn().map_err(|e| format!("启动失败: {}", e))?;
        app_log(&format!("spawn ok pid={:?}", child.id()));
        return Ok(child);
    }

    // 回退：本地 Python（开发用）
    let python_exe = exe_dir.join("backend").join("python").join("python.exe");
    if python_exe.exists() {
        app_log(&format!("fallback python: {}", python_exe.display()));
        let mut cmd = Command::new(&python_exe);
        cmd.args(["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "7413"])
            .current_dir(exe_dir.join("backend"))
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let mut child = cmd.spawn().map_err(|e| format!("Python启动失败: {}", e))?;
        if let Some(stdout) = child.stdout.take() {
            std::thread::spawn(move || {
                for line in BufReader::new(stdout).lines() {
                    if let Ok(l) = line { println!("Backend: {}", l); }
                }
            });
        }
        if let Some(stderr) = child.stderr.take() {
            std::thread::spawn(move || {
                for line in BufReader::new(stderr).lines() {
                    if let Ok(l) = line { eprintln!("Backend: {}", l); }
                }
            });
        }
        return Ok(child);
    }

    Err(format!("找不到 LiMoBackend.exe 或 python"))
}

// ── 就绪探测 ──

fn spawn_backend_ready_probe(app: tauri::AppHandle) {
    let addr: SocketAddr = format!("127.0.0.1:{}", BACKEND_PORT).parse().unwrap();
    let timeout = Duration::from_secs(BACKEND_STARTUP_TIMEOUT_SECS);
    let exe_dir = env::current_exe()
        .unwrap_or_default()
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_default();

    std::thread::spawn(move || {
        let start = Instant::now();
        loop {
            if probe_health(&addr) {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.emit("backend-ready", BACKEND_PORT);
                }
                println!("Backend ready on {} after {:?}", BACKEND_PORT, start.elapsed());
                return;
            }

            // 子进程已退出 → **立刻**报失败，不再干等满 45s。
            // 这是「后端一启动就挂」最常见的样子：进程创建成功、几百毫秒后
            // 因 DLL 缺失 / 端口占用 / 依赖问题退出，而 traceback 只躺在 stderr 文件里。
            if let Some(code) = backend_child_exit_code(&app) {
                let failure = build_startup_failure(&exe_dir, "exited", false, Some(code), None);
                record_startup_failure(&app, failure);
                return;
            }

            if start.elapsed() >= timeout {
                // 到点时再确认一次：可能恰好在最后一轮退出。
                let exit_code = backend_child_exit_code(&app);
                let alive = exit_code.is_none();
                let stage = if alive { "timeout" } else { "exited" };
                let failure = build_startup_failure(&exe_dir, stage, alive, exit_code, None);
                record_startup_failure(&app, failure);
                return;
            }
            std::thread::sleep(Duration::from_millis(500));
        }
    });
}

/// 后端进程的退出码；`None` = 仍在运行（或主线程正持锁，下一轮再看）。
fn backend_child_exit_code(app: &tauri::AppHandle) -> Option<i32> {
    let state = app.try_state::<SidecarHandle>()?;
    let mut guard = state.0.try_lock().ok()?;
    let child = guard.as_mut()?;
    match child.try_wait() {
        Ok(Some(status)) => Some(status.code().unwrap_or(-1)),
        _ => None,
    }
}

/// 读日志尾部。截断点可能落在行中或多字节字符中间，所以丢掉不完整的首行。
fn read_tail(path: &Path, max_bytes: u64) -> String {
    let mut file = match std::fs::File::open(path) {
        Ok(f) => f,
        Err(_) => return String::new(),
    };
    let len = file.metadata().map(|m| m.len()).unwrap_or(0);
    let start = len.saturating_sub(max_bytes);
    if start > 0 && file.seek(SeekFrom::Start(start)).is_err() {
        return String::new();
    }
    let mut buf = Vec::new();
    if file.read_to_end(&mut buf).is_err() {
        return String::new();
    }
    let text = String::from_utf8_lossy(&buf).into_owned();
    if start > 0 {
        if let Some(i) = text.find('\n') {
            return text[i + 1..].to_string();
        }
    }
    text
}

/// 组装启动失败诊断 —— 路径解析与 `spawn_backend` 同源（`resolve_backend_exe`）。
fn build_startup_failure(
    exe_dir: &Path,
    stage: &str,
    alive: bool,
    exit_code: Option<i32>,
    // `spawn_backend` 返回的原始错误（仅 spawn 失败时有）。此前它**只进
    // `limo_startup.log`**，失败屏上一句「没能启动后端进程」把最关键的
    // 「为什么」（拒绝访问 / 文件被占用 / 杀软拦截）丢掉了。
    spawn_error: Option<&str>,
) -> StartupFailure {
    let ldir = log_dir();
    let log_path = ldir.join("limo_backend_stderr.log");
    let launcher_log_path = ldir.join("limo_startup.log");
    let log_tail = build_log_tail();
    // 「日志为空」必须能自证原因，而不是让用户以为界面坏了：
    //   - `log_dir_writable=false`        → 目录就写不进去，日志系统自己失败了
    //   - 可写但两份文件都不存在          → 后端一行输出都没产生（DLL 缺失等的典型形态）
    //   - 文件存在但 tail 为空            → 文件里确实没内容
    let log_dir_writable = dir_writable(&ldir);
    let launcher_log_exists = launcher_log_path.exists();
    let backend_log_exists = log_path.exists();
    let exe_path = resolve_backend_exe(exe_dir);
    let addr: SocketAddr = format!("127.0.0.1:{}", BACKEND_PORT).parse().unwrap();
    // 只在进程已退出时才有意义 —— 进程还活着时 7413 当然可能是它自己在监听。
    let port_occupied =
        !alive && TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok();

    let summary = if stage == "spawn" {
        // 「找不到 exe」与「exe 在但起不来」是两件处置完全不同的事：
        // 前者是安装不完整/被隔离（重装），后者是权限/占用/杀软（改环境）。
        // 此前两者共用一句「没能启动后端进程（路径）」，用户只能猜。
        if !exe_path.exists() {
            format!(
                "安装目录里找不到 {} —— 安装可能不完整，或被安全软件隔离了",
                exe_path.file_name().map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_else(|| exe_path.display().to_string())
            )
        } else if let Some(err) = spawn_error.filter(|e| !e.trim().is_empty()) {
            format!("后端程序存在，但启动被拒绝：{}", err)
        } else {
            format!("没能启动后端进程（{}）", exe_path.display())
        }
    } else if stage == "exited" {
        let base = match exit_code {
            Some(c) => format!("后端进程启动后立即退出（退出码 {}）", c),
            None => "后端进程启动后立即退出".to_string(),
        };
        if port_occupied {
            format!("{}，端口 {} 已被其他程序占用", base, BACKEND_PORT)
        } else {
            base
        }
    } else if port_occupied {
        format!("端口 {} 被其他程序占用，后端无法监听", BACKEND_PORT)
    } else {
        format!(
            "后端 {}s 内没有就绪（进程仍在运行）",
            BACKEND_STARTUP_TIMEOUT_SECS
        )
    };

    StartupFailure {
        stage: stage.to_string(),
        summary,
        alive,
        exit_code,
        log_tail,
        log_path: log_path.to_string_lossy().to_string(),
        log_dir: ldir.to_string_lossy().to_string(),
        log_dir_writable,
        launcher_log_exists,
        backend_log_exists,
        exe_path: exe_path.to_string_lossy().to_string(),
        port_occupied,
        install_dir_writable: probe_dir_writable(exe_dir),
    }
}

/// 写进 managed state（供前端主动拉取）+ emit 事件（供已挂载的前端即时收到）。
fn record_startup_failure(app: &tauri::AppHandle, failure: StartupFailure) {
    if let Some(state) = app.try_state::<BackendStartupState>() {
        if let Ok(mut guard) = state.0.lock() {
            *guard = Some(failure.clone());
        }
    }
    emit_startup_failure(app, &failure);
}

fn emit_startup_failure(app: &tauri::AppHandle, failure: &StartupFailure) {
    if let Some(w) = app.get_webview_window("main") {
        // timeout 事件名保持不变（前端已监听）；确定失败走新事件，语义上分得开：
        // timeout = 进程还活着但慢，可能再等等；failed = 已经救不回来了。
        let event = if failure.stage == "timeout" {
            "backend-startup-timeout"
        } else {
            "backend-startup-failed"
        };
        let _ = w.emit(event, failure);
    }
}

#[tauri::command]
fn get_backend_startup_failure(state: tauri::State<'_, BackendStartupState>) -> Option<StartupFailure> {
    state.0.lock().ok().and_then(|g| g.clone())
}

/// 打开日志目录 —— 用户在界面上直接够到 `limo_startup.log` / `limo_backend_stderr.log`。
#[tauri::command]
fn open_log_dir() -> Result<(), String> {
    let dir = log_dir();
    let _ = std::fs::create_dir_all(&dir);
    Command::new("explorer")
        .arg(&dir)
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("打开日志目录失败: {}", e))
}

fn probe_health(addr: &SocketAddr) -> bool {
    let mut stream = match TcpStream::connect_timeout(addr, Duration::from_millis(800)) {
        Ok(s) => s,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(1500)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(1500)));
    let req = format!("GET /health HTTP/1.0\r\nHost: 127.0.0.1:{}\r\nConnection: close\r\n\r\n", addr.port());
    if stream.write_all(req.as_bytes()).is_err() { return false; }
    let mut buf = [0u8; 64];
    let n = match stream.read(&mut buf) { Ok(n) => n, Err(_) => return false };
    let head = std::str::from_utf8(&buf[..n]).unwrap_or("");
    head.starts_with("HTTP/1.1 200") || head.starts_with("HTTP/1.0 200")
}

// ── 重启 ──

#[tauri::command]
fn restart_backend_sidecar(
    state: tauri::State<'_, SidecarHandle>,
    app: tauri::AppHandle,
) -> Result<(), String> {
    // ⚠️ 这里曾 emit `backend-restarting` / `backend-restarted`，前端**零监听**。
    // 它们是纯粹的冗余：本命令是**同步**的（spawn + 落位完成后才 Ok 返回），
    // 前端 `await invoke('restart_backend_sidecar')` 的 resolve 就等价于「restarted」，
    // 而「restarting」在 await 开始前即是事实。事件只在「命令之外的异步通知」上
    // 才有价值，这里是同一件事的第二个出口 —— 两份表达必然漂移，删掉。
    {
        let mut guard = state.0.lock().map_err(|e| format!("{}", e))?;
        if let Some(ref mut child) = *guard { let _ = child.kill(); }
    }
    // 清掉上一次的失败记录 —— 否则前端「重试」后立刻又拉到旧结论。
    if let Some(st) = app.try_state::<BackendStartupState>() {
        if let Ok(mut g) = st.0.lock() { *g = None; }
    }
    let exe_dir = env::current_exe().unwrap().parent().map(|p| p.to_path_buf()).unwrap();
    let new_child = match spawn_backend(&exe_dir) {
        Ok(c) => c,
        Err(e) => {
            let failure = build_startup_failure(&exe_dir, "spawn", false, None, Some(e.as_str()));
            record_startup_failure(&app, failure);
            return Err(e);
        }
    };
    {
        let mut guard = state.0.lock().map_err(|e| format!("{}", e))?;
        *guard = Some(new_child);
    }
    spawn_backend_ready_probe(app);
    Ok(())
}

// ── 路径诊断 ──

#[derive(serde::Serialize, Clone)]
struct InstallPathDiagnostics {
    exe_path: String,
    path_has_non_ascii: bool,
    path_has_space: bool,
    parent_writable: bool,
    platform: String,
}

fn analyze_install_path(exe_path: &Path) -> InstallPathDiagnostics {
    let path_str = exe_path.to_string_lossy().to_string();
    InstallPathDiagnostics {
        path_has_non_ascii: path_str.chars().any(|c| !c.is_ascii()),
        path_has_space: path_str.contains(' '),
        parent_writable: exe_path.parent().map_or(false, probe_dir_writable),
        exe_path: path_str,
        platform: std::env::consts::OS.to_string(),
    }
}

// ⚠️ 这里曾有 `#[tauri::command] get_install_path_diagnostics`，前端**零 invoke**。
// 同一份诊断数据已由 `backend-warning` 事件出门（`run()` 的 setup 里），且那条
// 出口现在真的有人听（`App.tsx` 的安装路径告警横幅）。留着它就是同一事实的第二
// 个出口 —— 删掉命令、保留 `analyze_install_path`（事件在用它）。

// ── 组件检测 ──

#[derive(serde::Serialize, Clone)]
struct ComponentStatus {
    /// "ffmpeg" | "searxng"
    name: String,
    /// "found" | "missing" | "bundled"
    status: String,
    /// 可执行文件路径（找到时）
    path: Option<String>,
}

/// 在指定目录和系统 PATH 中查找可执行文件
fn find_in_dir_or_path(name: &str, search_dir: &Path) -> Option<String> {
    let exe_name = if cfg!(windows) { format!("{}.exe", name) } else { name.to_string() };
    // 1. 安装目录优先
    let bundled = search_dir.join(&exe_name);
    if bundled.exists() {
        return Some(bundled.to_string_lossy().to_string());
    }
    // 2. 系统 PATH
    if let Ok(path_var) = env::var("PATH") {
        for dir in path_var.split(if cfg!(windows) { ';' } else { ':' }) {
            let candidate = Path::new(dir).join(&exe_name);
            if candidate.exists() {
                return Some(candidate.to_string_lossy().to_string());
            }
        }
    }
    None
}

#[tauri::command]
fn detect_components() -> Vec<ComponentStatus> {
    let exe_dir = env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_default();

    let mut results = Vec::new();

    // ffmpeg 检测
    let ffmpeg_path = find_in_dir_or_path("ffmpeg", &exe_dir.join("ffmpeg"));
    results.push(ComponentStatus {
        name: "ffmpeg".into(),
        status: if ffmpeg_path.is_some() { "found".into() } else { "missing".into() },
        path: ffmpeg_path,
    });

    // SearXNG 检测
    let searxng_dir = exe_dir.join("searxng");
    let searxng_exe = if cfg!(windows) {
        searxng_dir.join("searxng").join("SearXNG for Windows.exe")
    } else {
        searxng_dir.join("searxng")
    };
    let searxng_found = if searxng_exe.exists() {
        Some(searxng_exe.to_string_lossy().to_string())
    } else {
        // 尝试系统 PATH
        find_in_dir_or_path("searxng", &Path::new("").join(""))
            .or_else(|| find_in_dir_or_path("python", &Path::new("").join(""))
                .map(|_| "searxng (python module)".into()))
    };
    results.push(ComponentStatus {
        name: "searxng".into(),
        status: if searxng_found.is_some() { "found".into() } else { "missing".into() },
        path: searxng_found,
    });

    results
}

// ── PDF 导出（WebView2 PrintToPdf）──

/// 把后端产出的**自包含 HTML** 渲染成 PDF 直接落盘，不弹打印对话框。
///
/// ## 为什么不用主窗口打印
/// `ICoreWebView2::PrintToPdf` 只能打印**该 WebView2 实例的主文档**，而主窗口的文档
/// 是应用 UI 本身。故新建屏幕外窗口加载 `/export/{id}?format=html&mode=…`，
/// 打完即销毁 —— 喂给它的正是 HTML 导出那份自包含产物（自带 `_PRINT_CSS`），
/// 于是「预览 / HTML 导出 / PDF 导出」三者同源，不会各自漂移。
///
/// ## 为什么屏幕外而非 `visible(false)`
/// WebView2 对不可见窗口会推迟渲染，PrintToPdf 有概率拿到空白页。
/// 窗口放到 (-32000, -32000) 并关掉装饰与任务栏按钮 —— 用户看不见，渲染照常。
///
/// ## 依赖脆弱点
/// 本模块直接用 `webview2-com` 的 COM 类型，其版本必须与 tauri 内部的**同源**
/// （见 Cargo.toml 注释）：COM 接口不跨 crate 实例，`.cast()` 会因「两个不相干的
/// 类型」直接编译失败。升级 tauri 后此处报错先核对 Cargo.lock。
#[cfg(windows)]
mod pdf_export {
    use super::*;
    use std::sync::mpsc;
    use tauri::{WebviewUrl, WebviewWindow, WebviewWindowBuilder};
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        ICoreWebView2Environment6, ICoreWebView2_7,
    };
    use webview2_com::PrintToPdfCompletedHandler;
    use windows::core::{Interface, HSTRING};

    /// 导出窗口的 label —— 固定值（Tauri 要求窗口 label 唯一，故建窗前先清残窗）
    const WINDOW_LABEL: &str = "pdf-export";

    /// A4 = 210×297mm（英寸）。CSS 的 `@page { size: A4 }` 说的是同一张纸，
    /// 但 PrintSettings 的默认值其实是 **Letter** —— 不显式设就会印出美式尺寸
    const A4_WIDTH_IN: f64 = 8.2677;
    const A4_HEIGHT_IN: f64 = 11.6929;

    /// 与 `_PRINT_CSS` 的 `@page { margin: 18mm 16mm }` 取同值：
    /// 两边一致则无论 CSS 与 PrintSettings 谁胜出，纸上都是这个边距
    const MARGIN_V_IN: f64 = 18.0 / 25.4;
    const MARGIN_H_IN: f64 = 16.0 / 25.4;

    /// 打印超时 —— 正常文档几秒内完成，留足余量
    const PRINT_TIMEOUT_SECS: u64 = 90;

    pub async fn export(
        app: &tauri::AppHandle,
        url: String,
        file_name: String,
    ) -> Result<String, String> {
        let out_path = resolve_output_path(app, &file_name)?;
        let parsed = tauri::Url::parse(&url).map_err(|e| format!("导出地址无效: {}", e))?;
        // 结果从 WebView2 的完成回调跨回本协程
        let (tx, rx) = mpsc::channel::<Result<(), String>>();

        let app_for_main = app.clone();
        let out_for_main = out_path.clone();
        app.run_on_main_thread(move || create_job(app_for_main, parsed, out_for_main, tx))
            .map_err(|e| format!("调度导出任务失败: {}", e))?;

        // recv 会阻塞线程，故放 blocking 池（async command 里直接 recv 会占死运行时）
        let received = tauri::async_runtime::spawn_blocking(move || {
            rx.recv_timeout(Duration::from_secs(PRINT_TIMEOUT_SECS))
        })
        .await
        .map_err(|e| format!("等待导出结果失败: {}", e))?;

        match received {
            Ok(Ok(())) => Ok(out_path.to_string_lossy().to_string()),
            Ok(Err(e)) => Err(e),
            Err(_) => Err(format!("导出超时（{}s）", PRINT_TIMEOUT_SECS)),
        }
    }

    /// 建窗口并挂载「页面加载完成 → 打印」的钩子（必须在主线程调用）
    fn create_job(
        app: tauri::AppHandle,
        url: tauri::Url,
        out_path: PathBuf,
        tx: mpsc::Sender<Result<(), String>>,
    ) {
        // 上一轮可能残留（超时/崩溃）——label 必须唯一，先清掉
        if let Some(stale) = app.get_webview_window(WINDOW_LABEL) {
            let _ = stale.close();
        }

        let out_for_load = out_path.clone();
        // 闭包是 `Fn` 可能多次调用，且会 move 捕获 —— 为它单独留一份，
        // 免得把外层的 `tx` 一并卷走（下方 build 失败时还要用它报错）
        let tx_for_load = tx.clone();
        let builder = WebviewWindowBuilder::new(&app, WINDOW_LABEL, WebviewUrl::External(url))
            .title("LiMo PDF 导出")
            .inner_size(1024.0, 1400.0)
            .position(-32000.0, -32000.0)
            .decorations(false)
            .shadow(false)
            .skip_taskbar(true)
            .on_page_load(move |window, payload| {
                if payload.event() != tauri::webview::PageLoadEvent::Finished {
                    return;
                }
                let win = window.clone();
                let out = out_for_load.clone();
                let tx = tx_for_load.clone();
                // 换一个线程再抓 controller：本回调跑在 UI 线程，就地重入事件循环
                // 有死锁风险；顺带留出图片解码/字体就绪的时间。
                //
                // ⚠️ 这个数字是实测值，不是推导值：Finished 只代表主文档加载完成，
                // 而导出的 HTML 自包含（~1.8MB base64 字体 + 内联图片），字体解码
                // 与 KaTeX 客户端渲染都还在其后。没有「更短也够」的成功样本，别调小。
                // 代价：每次导出多等约 3s（离屏窗口，用户只感知为「慢一点」）。
                std::thread::spawn(move || {
                    std::thread::sleep(Duration::from_millis(3000));
                    start_print(win, out, tx);
                });
            });

        if let Err(e) = builder.build() {
            let _ = tx.send(Err(format!("创建导出窗口失败: {}", e)));
        }
    }

    fn start_print(
        window: WebviewWindow,
        out_path: PathBuf,
        tx: mpsc::Sender<Result<(), String>>,
    ) {
        // 兜底：无论成败，超时后都关窗，免得用户看不见却占着资源
        let guard = window.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_secs(PRINT_TIMEOUT_SECS + 5));
            let _ = guard.close();
        });

        let win_for_print = window.clone();
        let out_for_print = out_path.clone();
        let err_win = window.clone();
        let tx_outer = tx.clone();

        let dispatched = window.with_webview(move |pw| {
            if let Err(e) = print_now(&pw, &out_for_print, tx_outer.clone(), win_for_print) {
                app_log(&format!("pdf export failed: {}", e));
                let _ = tx_outer.send(Err(e));
                let _ = err_win.close();
            }
        });

        if let Err(e) = dispatched {
            let _ = tx.send(Err(format!("获取 WebView2 句柄失败: {}", e)));
            let _ = window.close();
        }
    }

    /// 真正发起打印。**必须在 UI 线程执行**（`with_webview` 的约定）——
    /// WebView2 是单线程 COM 组件，跨线程调用会失败或静默无输出。
    fn print_now(
        pw: &tauri::webview::PlatformWebview,
        out_path: &Path,
        tx: mpsc::Sender<Result<(), String>>,
        window: WebviewWindow,
    ) -> Result<(), String> {
        let controller = pw.controller();
        let core = unsafe { controller.CoreWebView2() }
            .map_err(|e| format!("获取 WebView2 核心对象失败: {}", e))?;

        // PrintToPdf 属于 ICoreWebView2_7（SDK 1.0.1108+），Win10/11 自带的运行时都满足
        let core7: ICoreWebView2_7 = core
            .cast()
            .map_err(|e| format!("WebView2 运行时版本过旧，不支持 PDF 导出: {}", e))?;
        let env6: ICoreWebView2Environment6 = pw
            .environment()
            .cast()
            .map_err(|e| format!("WebView2 环境不支持打印设置: {}", e))?;
        let settings = unsafe { env6.CreatePrintSettings() }
            .map_err(|e| format!("创建打印设置失败: {}", e))?;

        unsafe {
            settings.SetPageWidth(A4_WIDTH_IN).map_err(err_msg)?;
            settings.SetPageHeight(A4_HEIGHT_IN).map_err(err_msg)?;
            settings.SetMarginTop(MARGIN_V_IN).map_err(err_msg)?;
            settings.SetMarginBottom(MARGIN_V_IN).map_err(err_msg)?;
            settings.SetMarginLeft(MARGIN_H_IN).map_err(err_msg)?;
            settings.SetMarginRight(MARGIN_H_IN).map_err(err_msg)?;
            // 保留代码块/引用的底色 —— 一键导出没有对话框让用户勾「背景图形」，
            // 少了它代码块只剩一圈边框
            settings.SetShouldPrintBackgrounds(true.into()).map_err(err_msg)?;
            // 不要 URL/日期/页码（与浏览器「另存为 PDF」的默认一致）
            settings
                .SetShouldPrintHeaderAndFooter(false.into())
                .map_err(err_msg)?;
        }

        let out_wide = HSTRING::from(out_path.to_string_lossy().as_ref());
        let handler = PrintToPdfCompletedHandler::create(Box::new(move |result, succeeded| {
            let outcome = match (result, succeeded) {
                (Ok(()), true) => Ok(()),
                (Ok(()), false) => Err("WebView2 报告打印未成功".to_string()),
                (Err(e), _) => Err(format!("打印失败: {}", e)),
            };
            let _ = window.close();
            let _ = tx.send(outcome);
            Ok(())
        }));

        unsafe { core7.PrintToPdf(&out_wide, &settings, &handler) }
            .map_err(|e| format!("发起打印失败: {}", e))?;
        Ok(())
    }

    fn err_msg(e: windows::core::Error) -> String {
        format!("打印设置失败: {}", e)
    }

    /// 落盘位置 = 系统「下载」目录（与其余导出行为一致），同名自动加序号
    fn resolve_output_path(app: &tauri::AppHandle, file_name: &str) -> Result<PathBuf, String> {
        let dir = app
            .path()
            .download_dir()
            .map_err(|e| format!("找不到系统下载目录: {}", e))?;
        let _ = std::fs::create_dir_all(&dir);

        let stem = sanitize_stem(file_name);
        let mut candidate = dir.join(format!("{}.pdf", stem));
        let mut n = 1u32;
        while candidate.exists() {
            candidate = dir.join(format!("{} ({}).pdf", stem, n));
            n += 1;
            if n > 999 {
                return Err("同名文件过多，请先清理下载目录".to_string());
            }
        }
        Ok(candidate)
    }

    /// 文件名清洗。前端已过滤一轮，这里是**落盘前的最后一道** ——
    /// 命令可被任意前端调用，Rust 侧不能假设调用方守规矩（Windows 文件名
    /// 不允许 `\/:*?"<>|`，路径分隔符混进来就是路径穿越）
    fn sanitize_stem(file_name: &str) -> String {
        let stripped = file_name.strip_suffix(".pdf").unwrap_or(file_name);
        let cleaned: String = stripped
            .chars()
            .map(|c| {
                if matches!(c, '\\' | '/' | ':' | '*' | '?' | '"' | '<' | '>' | '|') || c.is_control()
                {
                    '_'
                } else {
                    c
                }
            })
            .collect();
        // Windows 不允许文件名以点/空格结尾
        let trimmed = cleaned.trim().trim_end_matches('.').trim();
        let capped: String = trimmed.chars().take(80).collect();
        let capped = capped.trim_end();
        if capped.is_empty() {
            "export".to_string()
        } else {
            capped.to_string()
        }
    }
}

#[cfg(not(windows))]
mod pdf_export {
    pub async fn export(
        _app: &tauri::AppHandle,
        _url: String,
        _file_name: String,
    ) -> Result<String, String> {
        Err("当前平台不支持 PDF 直接导出（仅 Windows WebView2 可用）".to_string())
    }
}

#[tauri::command]
async fn export_pdf(
    app: tauri::AppHandle,
    url: String,
    file_name: String,
) -> Result<String, String> {
    pdf_export::export(&app, url, file_name).await
}

// ── 打包版：把应用的 Ctrl 快捷键从 WebView2 手里放行到网页 ──
//
// ⚠️ **这里是「网页版（npm run dev）与打包版行为不同」的唯一落点**，也是本项目
// 唯一需要 Rust 侧介入的键盘问题。
//
// WebView2 的浏览器加速键（`AreBrowserAcceleratorKeysEnabled`）**默认为开启**，
// 而 `Ctrl+Plus` / `Ctrl+Minus` 正落在它的管辖范围内（同列表还有 Ctrl+P 打印、
// Ctrl+F 查找、F5 刷新、DevTools）。按键的传播顺序是：
//
//   ① 宿主 AcceleratorKeyPressed 事件（本模块）
//   ② **WebView2 按浏览器语义消费加速键**      ← Ctrl+± 死在这一步
//   ③ 网页内容 —— 只有 ①② 都没拦下的组合才会到这里
//
// 于是 `App.tsx::handleAppKeyDown` 里那些 `preventDefault()` 在打包版
// **根本不触发**（浏览器开发模式下一切正常），两端行为从根上分叉。
//
// 做法：对**我们自己在用的那几个键**单独设 `IsBrowserAcceleratorKeyEnabled = FALSE`
// —— 浏览器跳过自己的处理，事件照常往下传播，余下交给既有前端逻辑。
//
// ⚠️ **不要设 `Handled = TRUE`**：那是「吞掉、连网页也收不到」，与目的正好相反。
// ⚠️ **不要动全局的 `AreBrowserAcceleratorKeysEnabled`**：会连带废掉
//    Ctrl+P / F5 / Ctrl+F / DevTools，排障时很麻烦 —— 精准放行只多这几行。

#[cfg(windows)]
mod webview_keys {
    use webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2AcceleratorKeyPressedEventArgs2;
    use webview2_com::AcceleratorKeyPressedEventHandler;
    use windows::core::Interface;

    // Win32 虚拟键码。⚠️ **这份清单与 `frontend/src/App.tsx::handleAppKeyDown`
    // 的 case 一一对应** —— 那边加/删快捷键时要同步改这里，否则新键在打包版会被
    // WebView2 抢走（症状：开发模式好用、打包版没反应，且没有任何报错）。
    //
    // 逐键说明：
    //   · `=` / `-`（VK_OEM_PLUS / VK_OEM_MINUS）—— **必须**放行：页面缩放正落在
    //     WebView2 加速键的管辖范围内，不放行则打包版完全收不到这两个键
    //   · 其余（W / G / I / K / \）—— 当前**不在** WebView2 的加速键列表里，
    //     放行属于防御性的（对不被消费的键设 FALSE 零副作用）。之所以一并列出，
    //     是让清单与 App.tsx 等长：新增快捷键时不必再逐个判断「这个键要不要放行」
    const VK_0: u32 = 0x30; // '0' —— 重置字号
    const VK_G: u32 = 0x47; // 'G' —— 生成面板
    const VK_I: u32 = 0x49; // 'I' —— AI 浮窗
    const VK_K: u32 = 0x4B; // 'K' —— 文档库搜索
    const VK_W: u32 = 0x57; // 'W' —— 阅读宽度
    const VK_OEM_PLUS: u32 = 0xBB; // '='（Shift 下是 '+'，VK 与 Shift 无关）
    const VK_OEM_MINUS: u32 = 0xBD; // '-'（Shift 下是 '_'）
    const VK_OEM_5: u32 = 0xDC; // '\' —— 沉浸阅读

    fn is_app_shortcut(vk: u32) -> bool {
        matches!(
            vk,
            VK_0 | VK_G | VK_I | VK_K | VK_W | VK_OEM_PLUS | VK_OEM_MINUS | VK_OEM_5
        )
    }

    /// 在主窗口上注册加速键回调。**必须在 UI 线程调用**（WebView2 是单线程 COM）
    pub fn allow_app_shortcuts(pw: &tauri::webview::PlatformWebview) {
        let handler = AcceleratorKeyPressedEventHandler::create(Box::new(|_sender, args| {
            if let Some(args) = args {
                let mut vk: u32 = 0;
                // 读不到键码就当不认识，保持默认行为
                if unsafe { args.VirtualKey(&mut vk) }.is_ok() && is_app_shortcut(vk) {
                    // Args2 需要运行时 1.0.1108+（Win10/11 自带的都满足）。
                    // 老运行时不支持则 cast 失败 —— 退回现状（该键被浏览器加速键接管），
                    // 不影响其它任何功能
                    if let Ok(args2) = args.cast::<ICoreWebView2AcceleratorKeyPressedEventArgs2>() {
                        let _ = unsafe { args2.SetIsBrowserAcceleratorKeyEnabled(false.into()) };
                    }
                }
            }
            Ok(())
        }));

        // handler 无需显式注销：COM 事件注册会 AddRef，随进程退出一并释放
        let controller = pw.controller();
        if let Err(e) = unsafe {
            controller.add_AcceleratorKeyPressed(&handler, std::ptr::null_mut())
        } {
            eprintln!("注册加速键回调失败（应用快捷键仍由浏览器加速键接管）: {}", e);
        }
    }
}

#[cfg(not(windows))]
mod webview_keys {
    /// 非 Windows 平台没有 WebView2 加速键这回事：Linux/macOS 的 webview 直接把
    /// 按键交给网页。空实现让 `run()` 里的调用点不必包 `cfg`
    pub fn allow_app_shortcuts(_pw: &tauri::webview::PlatformWebview) {}
}

// ── PATH 增强 ──

fn enhance_path(current_path: &str) -> String {
    let mut extra = Vec::new();

    // 安装目录下的组件优先
    let exe_dir = env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()));
    if let Some(ref dir) = exe_dir {
        let ffmpeg_dir = dir.join("ffmpeg");
        if ffmpeg_dir.exists() {
            extra.push(ffmpeg_dir.to_string_lossy().to_string());
        }
        let searxng_dir = dir.join("searxng");
        if searxng_dir.exists() {
            extra.push(searxng_dir.to_string_lossy().to_string());
        }
    }

    // 常见系统路径
    let system_paths: Vec<&str> = if cfg!(windows) {
        vec!["C:\\ffmpeg\\bin", "C:\\Program Files\\ffmpeg\\bin",
             "C:\\tools\\ffmpeg\\bin", "C:\\ProgramData\\chocolatey\\bin"]
    } else if cfg!(target_os = "macos") {
        vec!["/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin"]
    } else {
        vec!["/usr/local/bin", "/usr/bin", "/bin"]
    };

    let sep = if cfg!(windows) { ";" } else { ":" };
    let mut parts: Vec<String> = extra;
    parts.extend(system_paths.iter().map(|s| s.to_string()));
    if !current_path.is_empty() { parts.push(current_path.to_string()); }
    parts.join(sep)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 唯一的临时路径（不引 `tempfile`：这里只要「测试之间不撞名」）。
    fn tmp_path(tag: &str) -> PathBuf {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        env::temp_dir().join(format!("limo-test-{}-{}-{}", tag, std::process::id(), nanos))
    }

    /// 造一个**一定写不进去**的目录路径：把普通文件当父级。
    ///
    /// ⚠️ 不用 chmod / 只读属性造「不可写」—— CI（或以管理员身份）跑时那条路会
    /// 静默变成可写，测试照样绿而没有测到任何东西。「父级是个文件」这条判据与
    /// 权限无关，到哪都一样。
    fn blocked_dir(tag: &str) -> PathBuf {
        let file = tmp_path(tag);
        std::fs::write(&file, b"x").unwrap();
        file.join("sub")
    }

    #[test]
    fn dir_writable_能写时返回_true_且不留探针() {
        let d = tmp_path("writable");
        assert!(dir_writable(&d));
        assert!(d.exists());
        assert!(!d.join(".limo_log_probe").exists(), "探针文件必须被清掉");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn dir_writable_建不出目录时返回_false() {
        assert!(!dir_writable(&blocked_dir("blocked")));
    }

    // ⚠️ 本轮的核心不变量：首选目录写不进去时，日志必须落到**下一个**候选上，
    // 而不是「一条都不留」。开发机上造不出「%LOCALAPPDATA% 不可写」这个环境，
    // 所以这条只能靠传假的候选列表来钉 —— 它是「别人机器上没有日志」的唯一防线。
    #[test]
    fn pick_log_dir_跳过写不进去的候选() {
        let blocked = blocked_dir("skip");
        let ok = tmp_path("ok");
        assert_eq!(pick_log_dir(&[blocked.clone(), ok.clone()]), Some(ok.clone()));
        assert!(!blocked.exists(), "写不进去的候选不该被留下半个目录");
        let _ = std::fs::remove_dir_all(&ok);
    }

    #[test]
    fn pick_log_dir_全部不可写时返回_none() {
        assert_eq!(pick_log_dir(&[blocked_dir("none-a"), blocked_dir("none-b")]), None);
    }

    // 回退链本身要有第二、第三站，否则 `pick_log_dir` 再正确也没得换。
    #[test]
    fn log_dir_candidates_首选应用数据目录_其次临时目录() {
        let c = log_dir_candidates();
        assert!(c.len() >= 2, "至少两个候选，回退链才成立");
        assert_eq!(c[0], user_data_dir());
        assert_eq!(c[1], env::temp_dir().join("LiMo").join("logs"));
    }
}
