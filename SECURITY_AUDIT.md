# LiMo 安全漏洞检查报告

> 检查日期：2026-07-08 | 范围：全栈（后端 FastAPI + 前端 React/TypeScript）
> ⚠️ **时点审计报告**：以下问题多数已修复（标注 ✅），保留用于追溯。当前安全状态见 [CLAUDE.md](CLAUDE.md)「安全」章节。
> 2026-08-02 追加修复（全面漏洞排查）：**CORS Origin 白名单**（阻断任意网页跨站读本地后端/Cookie 泄漏）、serve_asset 路径遍历收紧到 `doc_dir/assets`、导出 `temp_id` 穿越校验、content_preparer `assets/../../` 任意文件读取校验。
> 2026-10-04 追加修复：**凭据移出 `config.yaml`** —— 所有 API Key / Cookie 改存 Windows DPAPI 密钥库（`%LOCALAPPDATA%\LiMo\secrets.bin`，`ctypes` 直调 `crypt32.dll`，零新依赖），YAML 只留 `secrets_ref: dpapi:limo`；配套 `scripts/check-secrets.py`（自检）与 `scripts/migrate-secrets.py`（迁移 / 回滚）。**下方 L3 因此已失效**。

---

## 🔴 HIGH — 敏感信息泄露

### H1. Tavily API Key 未经脱敏返回前端 ✅ 已修复

**文件**: [config.py:41](backend/app/core/config.py#L41)

```python
# GET /api/config 返回：
"agent_tools": cfg.get("agent_tools", {}),
# → {"search": {"provider": "searxng", "tavily_api_key": "tvly-明文Key"}}
```

Tavily API Key 在 `GET /api/config` 响应中原样返回，任何能打开浏览器 DevTools 的人都能看到。对比 LLM API Key 和 Cookie 均做了脱敏处理（`mask_sensitive()`），Tavily Key 是唯一的漏网之鱼。

**影响**：API Key 泄露 → 第三方可盗用 Tavily 额度、发起恶意搜索请求。

**修复方案**：在 config GET 路由中对 `tavily_api_key` 做脱敏，同时在 ConfigUpdate POST 中增加 `merge_sensitive` 保护，防止前端发回 `***` 占位值时覆盖真实 Key。

```python
# config.py GET 路由 — 脱敏
at = cfg.get("agent_tools", {})
if at.get("search", {}).get("tavily_api_key"):
    at = copy.deepcopy(at)
    at["search"]["tavily_api_key"] = mask_sensitive(at["search"]["tavily_api_key"])
```

```python
# config.py POST 路由 — 防覆盖
if data.agent_tools:
    new_search = data.agent_tools.get("search", {})
    old_search = cfg.get("agent_tools", {}).get("search", {})
    if new_search.get("tavily_api_key"):
        new_search["tavily_api_key"] = merge_sensitive(
            new_search["tavily_api_key"], old_search.get("tavily_api_key", ""))
```

---

## 🟡 MEDIUM

### M1. Markdown 渲染中 `javascript:` 协议未过滤 ✅ 已修复

**文件**: [markdown-components.tsx:123](frontend/src/lib/markdown-components.tsx#L123)

```tsx
<a href={href} target={href?.startsWith('http') ? '_blank' : undefined} ...>
```

LLM 生成的 Markdown 中的 `[点击](javascript:alert(1))` 会在用户点击时执行。虽然内容来自用户自己的 LLM，但若导入外部来源的 Markdown 或对话中包含恶意链接，存在风险。

**影响**：诱导点击 → XSS（需要用户交互）。

**修复方案**：在 `<a>` 组件中校验 `href`，拒绝 `javascript:` / `data:` / `vbscript:` 协议。

```tsx
const safe = href && /^(https?:|#|\/)/.test(href) ? href : undefined
if (!safe) return <span {...props}>{children}</span>
```

### M2. 无任何请求认证/授权

**文件**: [main.py](backend/main.py)

整个 API 无任何认证机制。对于本地桌面应用这是可接受的，但需注意：
- 同局域网内其他设备可通过 `http://<本机IP>:7413` 访问所有 API
- 恶意本地进程可任意读写知识库数据

**影响**：局域网内未授权访问、本地恶意软件可操作知识库。

**修复方案**：短期可绑定 `127.0.0.1`（当前已是）；长期可加本地 token 认证。

### M3. 封面重定向存在开放重定向风险

**文件**: [files.py:76-78](backend/app/routers/files.py#L76-L78)

```python
if domain in _COVER_DOMAINS or domain.endswith(".hdslb.com"):
    return RedirectResponse(url=cover_url)
```

域名白名单包含 `i.ytimg.com` 等 CDN 域，但如果 CDN URL 本身被篡改（例如旧数据中的 cover_url 被手动修改），仍可能重定向到恶意 URL。此外 `.hdslb.com` 后缀匹配可能被 `evil.hdslb.com.attacker.com` 绕过（虽然 DNS 解析会阻止，但不够严谨）。

**修复方案**：对 `domain.endswith(".hdslb.com")` 增加前缀检查，确保是精确的子域名匹配。

---

## 🟢 LOW

### L1. HTML 导出引用外部 CDN 资源 ✅ 已修复（统一自包含内联）

**文件**: [export.py:432-437](backend/app/routers/export.py#L432-L437)

```python
HTML_HEAD = """...<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">"""
HTML_FOOT = """<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>..."""
```

`_to_html()` 函数使用 CDN 加载 KaTeX。`_build_selfcontained_html()` 则正确地内联了资源。但 `_to_html()` 在某些导出路径中仍被使用，导致外部网络请求，且 CDN 资源可能被篡改（供应链风险）。

**修复方案**：所有 HTML 导出统一使用 `_build_selfcontained_html()` 内联 KaTeX。

### L2. 临时文件目录未自动清理

**文件**: [ingest.py:544](backend/app/routers/ingest.py#L544)

```python
media_dir = tempfile.mkdtemp(prefix="mbw_img_")
```

非流式导入路径（`/ingest`）创建的临时目录在 `_persist_media_images` 中有清理逻辑（`shutil.rmtree`），但如果中间抛出异常跳过了清理，临时目录会残留。流式路径同理。

**修复方案**：用 `try/finally` 包裹确保临时目录总是被清理。

### L3. `config.yaml` 中 API Key 明文存储 ✅ 已修复（2026-10-04）

~~所有 API Key（LLM、DashScope、Tavily）以明文存储在 `config.yaml`。对于本地桌面应用是常见做法，但用户应注意文件权限。~~

**修复**：值改送 DPAPI 密钥库，`config.yaml` 只留 `secrets_ref: dpapi:limo`。密钥库**刻意放 `%LOCALAPPDATA%\LiMo\`**——`~/.limo/` 会被同步 / 备份 / 打包分享，不是放密钥的地方。

⚠️ **定位是「卫生」不是「安全边界」**：挡的是「配置文件被复制走」的被动泄漏，挡不住同账户下的主动恶意进程。解法失败时**显式抛错**，不静默退回明文（退回等于白做，且 YAML 上看不出异常）。

### L4. `data_dir` 配置值未做路径遍历校验

**文件**: [config.py:45](backend/app/routers/config.py#L45)

`data_dir` 接受任意字符串，可能被指向系统关键目录。但需要先能修改 `config.yaml`，因此实际风险很低。

---

## ✅ 已验证安全的项目

| 项目 | 状态 | 关键实现 |
|------|------|----------|
| SSRF 防护 | ✅ 完备 | 14 个 IP 段黑名单 + DNS 解析检查 + 协议白名单 |
| 路径遍历 | ✅ 完备 | `_safe_filename()` + `resolve().relative_to()` 双重防护 |
| 文件上传 | ✅ 完备 | 2GB 上限 + 100MB 流式阈值 + 扩展名校验 |
| SQL 注入 | ✅ 不适用 | 无 SQL 数据库，纯文件存储 |
| 命令注入 | ✅ 安全 | `subprocess.run()` 使用列表参数，无 shell 调用 |
| 配置写入 | ✅ 安全 | 原子写入（.tmp → os.replace）+ config_lock |
| Cookie 脱敏 | ✅ 安全 | `mask_sensitive()` + `merge_sensitive()` |
| XSS (Markdown) | ✅ 基本安全 | ReactMarkdown 默认不渲染 HTML |
| CSRF | ✅ 不适用 | 无 Cookie/Session 机制 |
| API Key 格式检测 | ✅ 安全 | `_warn_key_mismatch()` 防 DashScope Key 配错 |
| 输入校验 (tx_id) | ✅ 安全 | 正则 `^[a-zA-Z0-9_\-]+$` + 长度限制 |

---

## 修复优先级

| # | 问题 | 严重度 | 修复难度 |
|---|------|--------|----------|
| H1 | Tavily API Key 前端泄露 | HIGH | 低（3 行代码） |
| M1 | Markdown `javascript:` 链接 | MEDIUM | 低（5 行代码） |
| M2 | 无认证机制 | MEDIUM | 中（需设计） |
| M3 | 封面重定向域名匹配 | MEDIUM | 低 |
| L1 | HTML 导出 CDN 依赖 | LOW | 低 |
| L2 | 临时文件残留 | LOW | 低 |
