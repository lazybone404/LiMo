//! 应用内自动更新（安装版）。
//!
//! 链路的每一步都有「不报错、只是升级装不上」的失效形态，所以细节全写在注释里 ——
//! 这些坑只有真发版才暴露，靠读代码发现不了。
//!
//! 关键判据（改动前先读）：
//! - **检查可以静默，安装必须由人点**。这里只提供两个命令，什么时候调是前端的事。
//! - **Windows 上 `install()` 会 `std::process::exit(0)`**（插件源码 `install_inner`），
//!   不经过 `RunEvent::Exit`。所以后端 sidecar 必须在 `on_before_exit` 里先杀掉并等它退出，
//!   否则安装器覆盖 `LiMoBackend.exe` 时文件还被锁着，NSIS 中途失败 → 半更新的安装目录。
//! - **`on_before_exit` 是覆盖而非追加**：插件 `UpdaterBuilder::new()` 已经挂了一个
//!   `cleanup_before_exit`，我们再挂自己的就必须在里面手动调一次，否则 Tauri 的默认清理丢失。
//! - **`Update.timeout` 默认是 `None`**（插件 `update_from_release` 里写死），
//!   builder 的 `timeout` 只作用于检查请求。下载必须自己设一个上限，否则卡住的连接永不返回。

use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::ipc::Channel;
use tauri::{AppHandle, Manager};
use tauri_plugin_updater::{Update, UpdaterExt};
use url::Url;

/// GitHub Releases 的固定清单路径。仓库地址取自 `Cargo.toml` 的 `repository` 字段
/// （编译期 `CARGO_PKG_REPOSITORY`）—— 与 `scripts/release.py --check` 的第 2 条核对同源，
/// 改仓库地址时两处一起看。
const MANIFEST_SUFFIX: &str = "/releases/latest/download/latest.json";

/// 检查更新的单次请求上限。插件会逐个 endpoint 试，三条线路最坏 36 秒。
const CHECK_TIMEOUT: Duration = Duration::from_secs(12);

/// 下载安装包的总时长上限。**必须显式给**：不设是「永不超时」。
/// 30 分钟对 ~150 MB 在 100 KB/s 下仍有四倍余量。
const DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(30 * 60);

/// 下载线路：第 0 条是直连，其余是「中转前缀 + 直连地址」。
/// 中转只代理资源下载，**不能**拿去当网页链接（见 `docs/BUILD.md`）。
const PROXY_PREFIXES: &[&str] = &[
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
    "https://gh-proxy.org/",
];

/// 上次成功的线路记在这里。判据照抄 P-ai：失败前进一格并写回，成功**保持不动**。
/// 不记住的代价是：中文网络下每次启动都要先在直连上失败一轮。
const ROUTE_FILE: &str = "update-route.json";

fn route_count() -> usize {
    PROXY_PREFIXES.len() + 1
}

fn manifest_url() -> Result<String, String> {
    let repo = env!("CARGO_PKG_REPOSITORY").trim_end_matches('/');
    if repo.is_empty() {
        return Err("未配置仓库地址（Cargo.toml 的 repository 为空），无法检查更新".to_string());
    }
    Ok(format!("{repo}{MANIFEST_SUFFIX}"))
}

fn route_manifest_url(route: usize) -> Result<String, String> {
    let base = manifest_url()?;
    Ok(match route {
        0 => base,
        i => format!("{}{}", PROXY_PREFIXES[i - 1], base),
    })
}

fn route_download_url(route: usize, direct: &str) -> String {
    match route {
        0 => direct.to_string(),
        i => format!("{}{}", PROXY_PREFIXES[i - 1], direct),
    }
}

#[derive(Serialize, Deserialize)]
struct RouteState {
    index: usize,
}

fn route_file(app: &AppHandle) -> Option<std::path::PathBuf> {
    app.path().app_local_data_dir().ok().map(|d| d.join(ROUTE_FILE))
}

/// 读失败一律回落 0（直连）——线路偏好是纯优化，不能因为它坏掉就检查不了更新。
fn read_route(app: &AppHandle) -> usize {
    let Some(path) = route_file(app) else { return 0 };
    let Ok(raw) = std::fs::read_to_string(&path) else { return 0 };
    serde_json::from_str::<RouteState>(&raw)
        .map(|s| s.index % route_count())
        .unwrap_or(0)
}

fn write_route(app: &AppHandle, index: usize) {
    let Some(path) = route_file(app) else { return };
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let state = RouteState { index: index % route_count() };
    if let Ok(raw) = serde_json::to_vec(&state) {
        let _ = std::fs::write(&path, raw);
    }
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct UpdateInfo {
    version: String,
    /// 发布日期的 `Display` 形式（RFC3339）。插件给的是 `OffsetDateTime`，这里只当字符串用。
    date: Option<String>,
    notes: Option<String>,
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct UpdateStatus {
    current_version: String,
    update: Option<UpdateInfo>,
}

#[derive(Serialize, Clone)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum UpdateProgress {
    Progress {
        downloaded: u64,
        total: Option<u64>,
        // ⚠️ `rename_all` 挂在 enum 上只改**变体名**，不改变体里的字段名。
        // 少这一行时前端收到的是 `via_proxy`，按 `viaProxy` 读会恒为 `undefined`
        // （不报错，只是那句话永远不显示）。
        #[serde(rename = "viaProxy")]
        via_proxy: bool,
    },
    Installing {
        version: String,
    },
}

/// 组装检查器。每次调用都从持久化的线路开始，把四条线路全部排进去 ——
/// 插件 `check()` 自己就是「逐个 endpoint 试到成功为止」，我们不再写一层回退。
fn build_updater(app: &AppHandle) -> Result<tauri_plugin_updater::Updater, String> {
    let start = read_route(app);
    let mut endpoints = Vec::with_capacity(route_count());
    for i in 0..route_count() {
        let route = (start + i) % route_count();
        let url = Url::parse(&route_manifest_url(route)?)
            .map_err(|e| format!("更新清单地址无效：{e}"))?;
        endpoints.push(url);
    }

    let mut builder = app
        .updater_builder()
        .endpoints(endpoints)
        .map_err(|e| format!("配置更新端点失败：{e}"))?
        .timeout(CHECK_TIMEOUT);

    #[cfg(target_os = "windows")]
    {
        // 覆盖插件默认挂上的 cleanup_before_exit —— 自己这一份必须把它补回去。
        let handle = app.clone();
        builder = builder.on_before_exit(move || {
            crate::stop_backend_sidecar_blocking(&handle);
            handle.cleanup_before_exit();
        });

        // NSIS 默认会回落到「上一次写进注册表的目录」，实测过自定义安装目录被重置
        // （P-ai v0.9.10）。`/D=` 必须在参数最后，插件保证 installer_arg 追加在内部参数之后。
        if let Ok(exe) = std::env::current_exe() {
            if let Some(dir) = exe.parent() {
                builder = builder.installer_arg(format!("/D={}", dir.display()));
            }
        }
    }

    builder.build().map_err(|e| format!("构建更新检查器失败：{e}"))
}

async fn check_once(app: &AppHandle) -> Result<Option<Update>, String> {
    build_updater(app)?
        .check()
        .await
        .map_err(|e| format!("检查更新失败：{e}"))
}

/// 查有没有新版本。**不吃任何节流**：节流只属于启动时的静默检查（前端 `update-prefs`），
/// 手动点进来的人必须拿到刚才那一刻的真实结果 —— 返回缓存结论会让人以为新版本还没发。
#[tauri::command]
pub async fn check_update(app: AppHandle) -> Result<UpdateStatus, String> {
    let update = check_once(&app).await?;
    Ok(UpdateStatus {
        current_version: app.package_info().version.to_string(),
        update: update.map(|u| UpdateInfo {
            version: u.version,
            date: u.date.map(|d| d.to_string()),
            notes: u.body,
        }),
    })
}

/// 下载并安装。Windows 上成功时进程会在这里被安装器接管（`exit(0)`），
/// 所以调用方**不要**等这个 Promise resolve。
#[tauri::command]
pub async fn install_update(
    app: AppHandle,
    on_event: Channel<UpdateProgress>,
) -> Result<(), String> {
    // 重新检查而不是复用 `check_update` 的结果：两个命令之间隔着用户点击，
    // 期间可能又发了新版；多一次 1 KB 请求，换掉「装到一半版本对不上」。
    let Some(mut update) = check_once(&app).await? else {
        return Err("没有可安装的更新".to_string());
    };
    update.timeout = Some(DOWNLOAD_TIMEOUT);

    let start = read_route(&app);
    let mut last_error = String::from("未知错误");

    for i in 0..route_count() {
        let route = (start + i) % route_count();
        let via_proxy = route != 0;

        let mut attempt = update.clone();
        if via_proxy {
            let direct = update.download_url.to_string();
            attempt.download_url = Url::parse(&route_download_url(route, &direct))
                .map_err(|e| format!("更新下载地址无效：{e}"))?;
        }

        let downloaded = AtomicU64::new(0);
        let last_emitted = AtomicU64::new(0);
        let channel = on_event.clone();

        let result = attempt
            .download(
                move |chunk_length, total| {
                    let done =
                        downloaded.fetch_add(chunk_length as u64, Ordering::Relaxed)
                            + chunk_length as u64;
                    // 每 512 KiB 才发一次进度：分块回调是 KB 级的，逐块发会让前端
                    // 每秒重渲染几十次，而进度条只需要 1% 的粒度。
                    let previous = last_emitted.load(Ordering::Relaxed);
                    if done - previous < 512 * 1024 && total != Some(done) {
                        return;
                    }
                    last_emitted.store(done, Ordering::Relaxed);
                    let _ = channel.send(UpdateProgress::Progress {
                        downloaded: done,
                        total,
                        via_proxy,
                    });
                },
                || {},
            )
            .await;

        match result {
            Ok(bytes) => {
                // 成功不推进线路 —— 下次还走这条（含中转）。
                write_route(&app, route);
                let _ = on_event.send(UpdateProgress::Installing {
                    version: update.version.clone(),
                });
                return install(&app, &update, bytes);
            }
            Err(e) => {
                // 失败前进一格并写回，下次先试下一条。
                write_route(&app, (route + 1) % route_count());
                last_error = if via_proxy {
                    format!("第 {route} 条中转线路下载失败：{e}")
                } else {
                    format!("直连下载失败：{e}")
                };
            }
        }
    }

    Err(format!(
        "所有下载线路都失败了（{last_error}）。可在浏览器中打开 Releases 手动下载安装包"
    ))
}

#[cfg_attr(target_os = "windows", allow(unused_variables))]
fn install(app: &AppHandle, update: &Update, bytes: Vec<u8>) -> Result<(), String> {
    // `install()` 内部会再验一次签（源码第一行就是 `self.verify(bytes)?`），
    // 所以手动下载这条路没有削弱签名校验。
    update
        .install(bytes)
        .map_err(|e| format!("安装更新失败：{e}"))?;

    #[cfg(not(target_os = "windows"))]
    {
        app.restart();
    }

    #[cfg(target_os = "windows")]
    {
        // 正常情况下走不到这里：`install()` 成功时已经 `std::process::exit(0)`。
        Ok(())
    }
}
