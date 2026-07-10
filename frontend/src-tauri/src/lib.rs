use std::env;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager};

const BACKEND_STARTUP_TIMEOUT_SECS: u64 = 45;
const BACKEND_PORT: u16 = 7413;

struct SidecarHandle(Mutex<Option<Child>>);

fn app_log(dir: &Path, msg: &str) {
    let log_path = dir.join("limo_startup.log");
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
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_install_path_diagnostics,
            restart_backend_sidecar,
            detect_components,
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
        let log_path = exe_dir.join("limo_backend_stderr.log");
        app_log(exe_dir, &format!("spawning {} -> {}", backend_exe.display(), log_path.display()));

        let stderr_file = std::fs::File::create(&log_path)
            .map_err(|e| format!("日志创建失败: {}", e))?;

        let mut cmd = Command::new(&backend_exe);
        cmd.current_dir(exe_dir)
            .stdout(stderr_file.try_clone().map_err(|e| format!("{}", e))?)
            .stderr(stderr_file);

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
