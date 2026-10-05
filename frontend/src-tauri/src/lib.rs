use std::env;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager};

const BACKEND_STARTUP_TIMEOUT_SECS: u64 = 45;
const BACKEND_PORT: u16 = 7413;

struct SidecarHandle(Mutex<Option<Child>>);

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

fn app_log(_dir: &Path, msg: &str) {
    let log_dir = user_data_dir();
    let _ = std::fs::create_dir_all(&log_dir);
    let log_path = log_dir.join("limo_startup.log");
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs();
    let line = format!("[{}] {}\n", ts, msg);
    let _ = std::fs::OpenOptions::new().append(true).create(true)
        .open(&log_path)
        .map(|mut f| { let _ = f.write_all(line.as_bytes()); });
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
            app_log(&exe_dir, &format!("LiMo dir={}", exe_dir.display()));

            // 安装路径诊断
            let diag = analyze_install_path(&exe_path);
            if diag.path_has_space || diag.path_has_non_ascii || !diag.parent_writable {
                app_log(&exe_dir, &format!("WARNING space={} nonascii={} writable={}",
                    diag.path_has_space, diag.path_has_non_ascii, diag.parent_writable));
                let app_handle = app.handle().clone();
                std::thread::spawn(move || {
                    std::thread::sleep(std::time::Duration::from_millis(1500));
                    if let Some(w) = app_handle.get_webview_window("main") {
                        let _ = w.emit("backend-warning", &diag);
                    }
                });
            }

            match spawn_backend(&exe_dir) {
                Ok(child) => {
                    app.manage(SidecarHandle(Mutex::new(Some(child))));
                    app_log(&exe_dir, "spawn ok");
                }
                Err(e) => {
                    app_log(&exe_dir, &format!("spawn FAILED: {}", e));
                    eprintln!("后端启动失败: {}", e);
                }
            }

            spawn_backend_ready_probe(app.handle().clone());

            // 打包版：把 Ctrl+W / Ctrl+± / Ctrl+0 等从 WebView2 加速键手里放行到网页，
            // 否则 `App.tsx::handleAppKeyDown` 在打包版根本不触发（详见 webview_keys 注释）
            if let Some(w) = app.get_webview_window("main") {
                if let Err(e) = w.with_webview(|pw| webview_keys::allow_app_shortcuts(&pw)) {
                    app_log(&exe_dir, &format!("webview_keys 注册失败: {}", e));
                }
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_install_path_diagnostics,
            restart_backend_sidecar,
            detect_components,
            export_pdf,
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

fn spawn_backend(exe_dir: &Path) -> Result<Child, String> {
    // 优先 PyInstaller 打包的 exe（Tauri v2 externalBin 会自动加平台后缀）
    let plain_exe = exe_dir.join("LiMoBackend.exe");
    let target_exe = exe_dir.join(format!("LiMoBackend-{}.exe", std::env::consts::ARCH));
    // 实际上 Tauri 加的是完整 target triple，尝试匹配
    let backend_exe = if target_exe.exists() {
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
    };
    if backend_exe.exists() {
        let log_dir = user_data_dir();
        let _ = std::fs::create_dir_all(&log_dir);
        let log_path = log_dir.join("limo_backend_stderr.log");
        app_log(exe_dir, &format!("spawning {} -> {}", backend_exe.display(), log_path.display()));

        // 尝试创建日志文件并重定向 stdout/stderr；失败则丢弃输出
        let log_file = std::fs::File::create(&log_path).ok();
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
        app_log(exe_dir, &format!("spawn ok pid={:?}", child.id()));
        return Ok(child);
    }

    // 回退：本地 Python（开发用）
    let python_exe = exe_dir.join("backend").join("python").join("python.exe");
    if python_exe.exists() {
        app_log(exe_dir, &format!("fallback python: {}", python_exe.display()));
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
            if start.elapsed() >= timeout {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.emit("backend-startup-timeout",
                        &format!("后端 {}s 内未就绪", timeout.as_secs()));
                }
                return;
            }
            std::thread::sleep(Duration::from_millis(500));
        }
    });
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
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.emit("backend-restarting", ());
    }
    {
        let mut guard = state.0.lock().map_err(|e| format!("{}", e))?;
        if let Some(ref mut child) = *guard { let _ = child.kill(); }
    }
    let exe_dir = env::current_exe().unwrap().parent().map(|p| p.to_path_buf()).unwrap();
    let new_child = spawn_backend(&exe_dir)?;
    {
        let mut guard = state.0.lock().map_err(|e| format!("{}", e))?;
        *guard = Some(new_child);
    }
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.emit("backend-restarted", ());
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
        parent_writable: exe_path.parent().map_or(false, |p| {
            let probe = p.join(".dd_probe");
            match std::fs::write(&probe, b"x") {
                Ok(_) => { let _ = std::fs::remove_file(&probe); true }
                Err(_) => false,
            }
        }),
        exe_path: path_str,
        platform: std::env::consts::OS.to_string(),
    }
}

#[tauri::command]
fn get_install_path_diagnostics() -> InstallPathDiagnostics {
    analyze_install_path(&env::current_exe().unwrap_or_default())
}

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
                app_log(
                    &env::current_exe().unwrap_or_default(),
                    &format!("pdf export failed: {}", e),
                );
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
