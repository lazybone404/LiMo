# 短期待办

**这里放什么**：已经决定要做、做法清楚、近期就会动手的事。做完直接从本文件删掉。

**这里不放什么**：

- 还没定要不要做的方向、以及**等触发条件**的重构 → [roadmap.md](roadmap.md)。
  roadmap 只留大方向与「继续这么长下去会变贵」的结构债，不排期。
- 已经做完的记录 → roadmap 各域的 ✅ 行与 [CHANGELOG.md](CHANGELOG.md)。

⚠️ **同一条只待在一个地方。** 本文件与 roadmap 各写一份，等于两处各自记着「这件还没做」，
做完时漏删一处，下个接手的人就会重复做一遍。

## 排队中

### 1. 自动更新（安装版）｜优先级：高

客户端读 GitHub Release 的 `latest.json`，与 `CARGO_PKG_VERSION` 比版本号，有新的就
下载 → minisign 验签 → 安装 → 重启。**只做安装版**：LiMo 只发 NSIS 安装包
（`bundle.targets` 写的是 `all`，实际产出只有 `LiMo_x.x.x_x64-setup.exe`），
Tauri updater 插件承担全部下载 / 验签 / 替换 / 重启。

三处落点必须同批做：

1. `frontend/src-tauri/tauri.conf.json` 加 `bundle.createUpdaterArtifacts: true` 与
   `plugins.updater.pubkey` —— 两者当前都不存在，`plugins` 是空对象；
2. 发版流程加「签名 → 生成 `latest.json` → 随安装包一起上传」。现在 `scripts/` 下没有
   任何 release 脚本，`.github/workflows/` 只有 `ci.yml` 与 `deploy-demo.yml`，
   发版靠手工 `gh release create`；
3. 发版门禁加一条「`latest.json` 存在且 `signature` 非空」，与 `sync-version.py --check`
   同性质。

⚠️ **漏签是静默失效**：客户端只认为「没有新版本」，继续用旧版，全程不报错。
⚠️ **私钥丢失不可逆**：存量客户端只认已内置的公钥，换密钥后它们再也收不到自动更新。

与「打包 ≠ 发布」不冲突：自动更新是客户端行为，没发布过新版本就什么都不会发生。

