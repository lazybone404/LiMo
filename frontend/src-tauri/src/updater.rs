//! 应用内自动更新（安装版）。
//!
//! 链路的每一步都有「不报错、只是升级装不上」的失效形态，所以细节全写在注释里 ——
//! 这些坑只有真发版才暴露，靠读代码发现不了。
//!
//! 关键判据（改动前先读）：
//! - **检查可以静默，安装必须由人点**。这里只提供四个命令（检查 / 下载 / 安装 / 查已备好），
//!   什么时候调是前端的事。
//! - **下载与安装是两个命令**（2026-11-08）：下载安全（不动任何进程），安装会退出应用。
//!   拆开后用户能「先下好，忙完再装」，安装失败也不必重下 150 MB。
//! - **Windows 上 `install()` 会 `std::process::exit(0)`**（插件源码 `install_inner`），
//!   不经过 `RunEvent::Exit`。所以后端 sidecar 必须在 `on_before_exit` 里先杀掉并等它退出，
//!   否则安装器覆盖 `LiMoBackend.exe` 时文件还被锁着，NSIS 中途失败 → 半更新的安装目录。
//! - **`on_before_exit` 是覆盖而非追加**：插件 `UpdaterBuilder::new()` 已经挂了一个
//!   `cleanup_before_exit`，我们再挂自己的就必须在里面手动调一次，否则 Tauri 的默认清理丢失。
//! - **`Update.timeout` 默认是 `None`**（插件 `update_from_release` 里写死），
//!   builder 的 `timeout` 只作用于检查请求。下载必须自己设一个上限，否则卡住的连接永不返回。

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
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

/// 检查更新的单次请求上限。一次只查一条线路，四条全不通最坏 48 秒
/// （`route_count() = 1 + PROXY_PREFIXES.len()`，改线路表时这条注释要跟着算）。
const CHECK_TIMEOUT: Duration = Duration::from_secs(12);

/// 下载安装包的总时长上限。**必须显式给**：不设是「永不超时」。
/// 30 分钟对 ~150 MB 在 100 KB/s 下仍有四倍余量。
const DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(30 * 60);

/// 已下载、已验签、等人点「重启安装」的那一份。
///
/// **为什么下载与安装要拆成两个命令**（2026-11-08，照 P-ai 的做法）：
/// 安装会让应用退出，而下载要几分钟。合成一步时，用户没法「先下好，等我手头的事做完再装」，
/// 中途装失败还要重下 150 MB。拆开之后下载是安全的（不碰任何进程），只有最后那一下重启
/// 才是不可撤销的。
///
/// ⚠️ **只活在进程内存里**：关掉应用就没了，下次要重新下载。落盘要自己管临时文件的
/// 清理、磁盘配额与版本对不上时的失效判断，收益只是「关掉再打开还能接着装」——
/// 不值得。P-ai 同样只放内存。
struct PreparedUpdate {
    update: Update,
    /// 来自 `update.download()` 的返回值，**已经过签名校验**。
    /// ⚠️ `install()` **不验签**（插件 2.12.0 源码：只有 `download()` 里调 `verify_signature`），
    /// 所以这份字节绝不能来自别处 —— 自己攒、从磁盘读、跨进程传，都等于绕过验签且全程不报错。
    bytes: Vec<u8>,
}

static PREPARED: Mutex<Option<PreparedUpdate>> = Mutex::new(None);

fn take_prepared() -> Option<PreparedUpdate> {
    PREPARED.lock().ok().and_then(|mut slot| slot.take())
}

/// 下载线路：第 0 条是直连，其余是「中转前缀 + 直连地址」。
/// 中转只代理资源下载，**不能**拿去当网页链接（见 `docs/BUILD.md`）。
const PROXY_PREFIXES: &[&str] = &[
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
    "https://gh-proxy.org/",
];

/// 上次成功的线路记在这里。判据照抄 P-ai：失败前进一格并写回，成功**保持不动**。
/// 检查与下载两个阶段共用这一格 —— 检查命中的线路立即写回（`check_from`），
/// 下载失败再推进。不记住的代价是：中文网络下每次启动都要先在直连上失败一轮。
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

/// 按给定线路顺序组装检查器。**一次只该放一条线路**，理由见 `check_from`。
fn build_updater(
    app: &AppHandle,
    routes: &[usize],
) -> Result<tauri_plugin_updater::Updater, String> {
    let mut endpoints = Vec::with_capacity(routes.len());
    for &route in routes {
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

/// 校验清单里给出的下载地址。
///
/// **清单本身不签名**（插件的 `pubkey` 只覆盖安装包字节），所以它是不可信输入。危害有两条：
///
/// ① 中转被控时把 `url` 写成 `http://127.0.0.1:7413/...` 或内网地址 —— 用户一点安装，
///    客户端就从**自己机器上**发一次 GET（盲 SSRF，响应在验签失败后被丢弃）。
/// ② 把**旧版本**的 `url` + `signature`（两者都合法、都有签名）配上一个抬高的 `version`，
///    诱导降级到真实签名但过时的构建。
///
/// 两条都靠「地址必须钉在**本仓库 + 本次 announced 版本**上」堵掉：旧产物的地址里带的是
/// 旧版本号，与抬高的 `version` 对不上；而攻击者若把 `version` 改回旧值，插件的
/// 「必须比当前新」这道闸又会拦下。
///
/// 这也是没有开 `requireSignedVersion` 的补偿：那条要求签名 trusted comment 里带 `version:`，
/// 而本机 CLI 2.11.4 签出来的是 `timestamp:…\tfile:…`（2026-11-08 实测），开了等于每个
/// 更新都报 `MissingSignedVersion`。等确认打包时的 CLI 会写 `version:` 再开。
fn validate_release(update: &Update) -> Result<(), String> {
    validate_download_url(&update.download_url, &update.version)
}

/// `validate_release` 的纯函数部分 —— 纯粹为了能直接测（`Update` 没有构造器）。
///
/// 判据是「地址钉在本仓库 + 本次版本上」，`slug` 从 `CARGO_PKG_REPOSITORY` 现算，
/// 与 `scripts/release.py::repo_slug_from_url` 同判据（那边校验 `.sig` 与安装包）。
fn validate_download_url(url: &Url, version: &str) -> Result<(), String> {
    let repo = env!("CARGO_PKG_REPOSITORY").trim_end_matches('/');
    // `https://github.com/owner/repo` → `owner/repo`
    let slug = repo
        .rsplit('/')
        .take(2)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect::<Vec<_>>()
        .join("/");

    if url.scheme() != "https" || url.host_str() != Some("github.com") {
        return Err(format!("更新清单给出的下载地址不在 GitHub 上，已拒绝：{url}"));
    }
    if !url.path().starts_with(&format!("/{slug}/releases/download/v{version}/")) {
        return Err(format!(
            "更新清单给出的下载地址与版本 v{version} 不符，已拒绝：{url}"
        ));
    }
    Ok(())
}

/// 从 `start` 起逐条线路检查，返回 `(检查结果, 命中的线路)`。
///
/// 为什么不把四条线路一次性塞给插件的 `check()`（它自己也会逐个 endpoint 试）：
/// 那样只知道「有没有新版」，不知道**哪条线路通的**；而后面下载正是从游标那条起试，
/// 于是会出现「刚证明直连不通，下载又先去撞直连」—— 多等一个 12 秒超时。
/// 自己做这层循环还多一个好处：线路在那一次成功时就写回，不必等下载阶段。
///
/// 命中即返回，失败继续下一条；全部失败才返回错误。
async fn check_from(app: &AppHandle, start: usize) -> Result<(Option<Update>, usize), String> {
    let mut last_error = String::from("未知错误");
    for i in 0..route_count() {
        let route = (start + i) % route_count();
        match build_updater(app, &[route])?.check().await {
            Ok(found) => {
                // 先验清单的成色再认这个结果：地址不对就不当作「有更新」，
                // 更不会把它交到下载那一步。
                if let Some(u) = &found {
                    validate_release(u)?;
                }
                write_route(app, route);
                return Ok((found, route));
            }
            Err(e) => {
                last_error = if route == 0 {
                    format!("直连：{e}")
                } else {
                    format!("第 {route} 条中转：{e}")
                };
            }
        }
    }
    Err(format!("检查更新失败（最后一条线路报：{last_error}）"))
}

/// 查有没有新版本。**不吃任何节流**：节流只属于启动时的静默检查（前端 `update-prefs`），
/// 手动点进来的人必须拿到刚才那一刻的真实结果 —— 返回缓存结论会让人以为新版本还没发。
#[tauri::command]
pub async fn check_update(app: AppHandle) -> Result<UpdateStatus, String> {
    let (update, _route) = check_from(&app, read_route(&app)).await?;
    Ok(UpdateStatus {
        current_version: app.package_info().version.to_string(),
        update: update.map(|u| UpdateInfo {
            version: u.version,
            date: u.date.map(|d| d.to_string()),
            notes: u.body,
        }),
    })
}

/// 下载安装包（含验签），存进 `PREPARED` 等用户点「重启安装」。
/// **不启动安装器、不退出进程** —— 这一步是安全的，用户可以在下载时继续干活。
#[tauri::command]
pub async fn download_update(
    app: AppHandle,
    on_event: Channel<UpdateProgress>,
) -> Result<(), String> {
    // 先丢掉上一次的半成品：否则「已下载 v0.5.24」的旧包会一直留着，
    // 而界面上显示的是新的版本号 —— 装下去的却是旧包。
    let _ = take_prepared();

    // 重新检查而不是复用 `check_update` 的结果：两个命令之间隔着用户点击，
    // 期间可能又发了新版；多一次 1 KB 请求，换掉「装到一半版本对不上」。
    // 命中的那条线路就是下载的起点 —— 刚证通过的线路不该再被超时重试一次。
    let (found, start) = check_from(&app, read_route(&app)).await?;
    let Some(mut update) = found else {
        return Err("没有可安装的更新".to_string());
    };
    update.timeout = Some(DOWNLOAD_TIMEOUT);

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
                if let Ok(mut slot) = PREPARED.lock() {
                    *slot = Some(PreparedUpdate { update, bytes });
                }
                return Ok(());
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

/// 安装已下载的更新。Windows 上成功时进程会在这里被安装器接管（`exit(0)`），
/// 所以调用方**不要**等这个 Promise resolve。
#[tauri::command]
pub async fn apply_update(
    app: AppHandle,
    on_event: Channel<UpdateProgress>,
) -> Result<(), String> {
    let Some(prepared) = take_prepared() else {
        return Err("没有已下载的更新，请先下载".to_string());
    };
    let _ = on_event.send(UpdateProgress::Installing {
        version: prepared.update.version.clone(),
    });
    install(&app, &prepared.update, prepared.bytes)
}

/// 有没有已下载待安装的更新（返回版本号）。
///
/// 前端在启动时调一次，把「已下载，等重启」这个状态捡回来 —— 单个 WebView 里页面
/// 重新加载不会动这个进程，那份字节还在，而前端的内存状态已经没了。
#[tauri::command]
pub fn prepared_update() -> Option<String> {
    PREPARED
        .lock()
        .ok()
        .and_then(|slot| slot.as_ref().map(|p| p.update.version.clone()))
}

#[cfg_attr(target_os = "windows", allow(unused_variables))]
fn install(app: &AppHandle, update: &Update, bytes: Vec<u8>) -> Result<(), String> {
    // ⚠️ 验签发生在 `update.download()` 里（插件 `updater.rs` 的 download 内部调
    // `verify_signature`），`install()` **不验签** —— 它只负责把字节交给安装器。
    // 所以传进来的 `bytes` 必须来自 `download()` 的返回值，不能自己攒一份
    // （自己攒就是绕过签名校验，而且全程不报错）。
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

#[cfg(test)]
mod tests {
    use super::*;

    fn slug() -> String {
        env!("CARGO_PKG_REPOSITORY")
            .trim_end_matches('/')
            .rsplit('/')
            .take(2)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect::<Vec<_>>()
            .join("/")
    }

    fn release_url(version: &str) -> String {
        format!(
            "https://github.com/{}/releases/download/v{version}/LiMo_{version}_x64-setup.exe",
            slug()
        )
    }

    fn check(url: &str, version: &str) -> Result<(), String> {
        validate_download_url(&Url::parse(url).unwrap(), version)
    }

    #[test]
    fn 正常清单地址通过() {
        assert!(check(&release_url("0.5.23"), "0.5.23").is_ok());
    }

    #[test]
    fn 非_https_被拒() {
        let url = release_url("0.5.23").replace("https://", "http://");
        assert!(check(&url, "0.5.23").is_err());
    }

    /// 中转被控时把 url 指向本机端口 —— 盲 SSRF 的入口，必须挡住。
    #[test]
    fn 非_github_主机被拒() {
        assert!(check("http://127.0.0.1:7413/api/health", "0.5.23").is_err());
        assert!(check("https://example.com/a/b/c.exe", "0.5.23").is_err());
    }

    /// 降级攻击：旧产物的 url 与 signature 都合法，清单却报一个新版本号。
    #[test]
    fn 地址里的版本与清单不符被拒() {
        assert!(check(&release_url("0.5.1"), "0.5.23").is_err());
    }

    #[test]
    fn 别的仓库被拒() {
        let url = release_url("0.5.23").replace(&format!("github.com/{}", slug()), "github.com/attacker/LiMo");
        assert!(check(&url, "0.5.23").is_err());
    }
}
