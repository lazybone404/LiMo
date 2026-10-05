"""
LiMo Eagle Eye Test — 启动冒烟 + 核心端点验证。

用法：
    python tests/test_e2e_smoke.py                  # 默认端口 7413
    LIMO_E2E_PORT=7420 python tests/test_e2e_smoke.py
不依赖外部 LLM API。

⚠️ 本文件**自己启动后端**（不是「需后端已在跑」），且把 config / 密钥库 / 数据目录
   全部隔离进临时目录，跑完删光，全程不触碰 `~/.limo`。
   此前它直连真实配置：`POST /api/engine-bindings {"doc": 99999}` 把用户的 doc 引擎
   绑定永久覆盖成无效 id（2026-10-06 实测确认，原绑定已不可恢复）。
   三个隔离量缺一不可 —— 只设 `LIMO_CONFIG` 时后端仍按 config 里的 `data_dir`
   （默认 `~/.limo`）落 Store，**隔离了配置却污染了数据，比完全不隔离更隐蔽**。

⚠️ 执行体全在 `main()` 里、由 `__main__` 守卫触发。本文件名匹配 pytest 的
   `test_*.py`，顶层代码会在 **pytest 收集阶段被 import 执行** —— 即每跑一次
   `pytest tests/` 就静默起一个后端、打几十个请求、写一次真实配置。
   （`tests/__pycache__/test_e2e_smoke.cpython-3xx-pytest-9.1.1.pyc` 就是「它确实
   被 pytest 收集过」的证据；「这个文件需要外部后端所以不会被 pytest 跑」是个错觉。）
"""
import socket, subprocess, sys, time, json, os, shutil, tempfile, urllib.request, urllib.parse, urllib.error
from pathlib import Path

PORT = int(os.environ.get("LIMO_E2E_PORT", "7413"))
BASE = f"http://127.0.0.1:{PORT}"
PASSED = 0
FAILED = 0

def _get(path):
    url = f"{BASE}{path}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, json.loads(r.read()), ""
    except urllib.error.HTTPError as e:
        body = {}
        try: body = json.loads(e.read())
        except: pass
        return e.code, body, ""

def _json_req(method, path, body):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = {}
        try: body = json.loads(e.read())
        except: pass
        return e.code, body

def _post_form(path, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ct = r.headers.get("content-type", "")
            if "text/event-stream" in ct:
                # SSE：逐行读直到 done/error 事件，然后 break 关闭连接。
                # ⚠️ 不能用 r.read()——uvicorn 的 StreamingResponse 结束后不发送
                # 终止 chunk 且保持 keep-alive，read() 会一直等 EOF 直到 socket 超时。
                ev = None
                while True:
                    line = r.readline()
                    if not line:
                        break
                    if line.startswith(b"data: "):
                        try:
                            ev = json.loads(line[6:])
                            if ev.get("type") in ("done", "error"):
                                break
                        except: pass
                return r.status, ev or {}
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body = {}
        try: body = json.loads(e.read())
        except: pass
        return e.code, body

def _get_raw(path):
    req = urllib.request.Request(f"{BASE}{path}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""

def check(label, condition, detail=""):
    """记一笔断言结果。

    ⚠️ 这里**刻意不 `assert`**：冒烟测试的价值是「一次跑完看到全部失败项」，第一个失败
    就中断会让人反复重跑。退出码由 `main()` 收口 —— 此前它只 print 统计、永远 exit 0，
    在 `check-all` 里**必然绿**（模式 #38 的第三种形态：断言跑了、结果没人看）。
    """
    global PASSED, FAILED
    if condition:
        PASSED += 1; print(f"  PASS: {label}")
    else:
        FAILED += 1; print(f"  FAIL: {label}  --  {detail}")

def _port_busy(port):
    """端口上是否已经有东西在监听。"""
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0

def _make_isolated_env(tmp_root):
    """建临时 config（含 `data_dir`）+ 临时密钥库路径，返回传给子进程的 env。

    ⚠️ `data_dir` **必须**写进临时 config：只设 `LIMO_CONFIG` 时后端仍按 config 里的
    `data_dir`（默认 `~/.limo`）定位 Store，配置隔离了、数据没有。
    ⚠️ 返回值**自证**（模式 #58）：不是「我设了两个变量」，而是「传出去的那份 env 里
    确实有、且值就是临时路径」——不成立就直接炸，不要带着没生效的隔离继续跑。
    """
    data_dir = tmp_root / "data"
    cfg_path = tmp_root / "config.yaml"
    secrets_path = tmp_root / "secrets.bin"
    # `json.dumps` 而非手写引号：Windows 路径里的 `\` 在 YAML 双引号里是转义字符，
    # 手写要额外小心；JSON 字符串字面量正好是合法的 YAML 双引号标量。
    cfg_path.write_text(f"data_dir: {json.dumps(str(data_dir))}\n", encoding="utf-8")

    env = dict(os.environ)
    env["LIMO_CONFIG"] = str(cfg_path)
    env["LIMO_SECRETS"] = str(secrets_path)
    for key, want in (("LIMO_CONFIG", str(cfg_path)), ("LIMO_SECRETS", str(secrets_path))):
        if env.get(key) != want:
            raise RuntimeError(f"隔离失败：{key} 没进子进程 env（拿到 {env.get(key)!r}）")
    return env, cfg_path

def start_server(env):
    # ⚠️ Windows 匿名管道缓冲仅 ~4KB：uvicorn 的 stdout/stderr 若用 PIPE 且无人读取，
    # 日志量累计填满缓冲后写阻塞 → 阻塞 uvicorn 事件循环 → 后续请求全部超时（间歇性 flaky）。
    # 改为写入临时文件：不阻塞 + 保留后端日志供 e2e 失败时诊断。
    logf = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    print(f"backend log -> {logf.name}")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        stdout=logf, stderr=logf,
        env=env,
    )
    for _ in range(30):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=2) as r:
                if r.status == 200:
                    return proc
        except: pass
    proc.kill()
    raise RuntimeError(f"Backend did not start within 15s（日志见 {logf.name}）")

def main():
    # ⚠️ 起后端**之前**先看端口空不空。占用的端口会让 uvicorn 启动失败，而下面是
    # 「连上 /health 就算就绪」——于是测试会连上**用户正在跑的那个真实后端**，
    # 全程对着真实数据目录操作。这个失败模式看起来完全正常（断言照样全过），
    # 比「启动失败」危险得多，所以这里直接拒绝跑而不是「尽力而为」。
    if _port_busy(PORT):
        print(f"端口 {PORT} 已被占用 —— 拒绝启动。\n"
              f"本测试会自己起一个后端并写入临时数据目录，绝不能连到真实实例上。\n"
              f"换端口：LIMO_E2E_PORT=7420 python tests/test_e2e_smoke.py")
        return 2

    tmp_root = Path(tempfile.mkdtemp(prefix="limo_e2e_"))
    env, cfg_path = _make_isolated_env(tmp_root)

    # 收尾自证用的快照：真实配置必须毫发无损（模式 #58 第 ③ 条）
    real_cfg = Path.home() / ".limo" / "config.yaml"
    real_before = real_cfg.stat().st_mtime_ns if real_cfg.exists() else None

    print("=" * 50)
    print("LiMo Eagle Eye Test")
    print("=" * 50)
    print(f"隔离：config={cfg_path}")
    print(f"      data={tmp_root / 'data'}")
    print("\nStarting backend...")
    server = start_server(env)
    print(f"Backend ready (PID={server.pid})\n")

    try:
        # ---- 1. Health Check ----
        print("-- 1. Health Check --")
        st, body, _ = _get("/health")
        check("GET /health -> 200", st == 200, f"status={st}")
        check("status=ok", body.get("status") == "ok")
        check("version present", bool(body.get("version")))
        check("started_at is server start time",
              body.get("started_at", "").startswith("202"),
              body.get("started_at", ""))

        # ---- 2. Capabilities ----
        print("\n-- 2. Capabilities --")
        st, body, _ = _get("/api/capabilities")
        check("GET /api/capabilities -> 200", st == 200)
        check("adapters >= 5", len(body.get("adapters", [])) >= 5)
        check("engines has asr/doc/ocr",
              all(k in body.get("engines", {}) for k in ["asr", "doc", "ocr"]))
        check("engineBindings exists", isinstance(body.get("engineBindings"), dict))
        check("engineOptions not empty", len(body.get("engineOptions", {})) > 0)
        check("styles not empty", len(body.get("styles", [])) > 0)

        # ---- 3. Text Import + Title ----
        print("\n-- 3. Text Import + Title --")
        st, result = _post_form("/api/ingest/stream",
                                {"text": "# Hello World\n\nTest content paragraph."})
        check("POST /api/ingest/stream (text) -> 200", st == 200, f"status={st}")
        tx_id = result.get("id", "")
        check("got transcript ID", len(tx_id) > 0)

        if tx_id:
            st, tx, _ = _get(f"/api/transcripts/{tx_id}")
            check("GET transcript -> 200", st == 200)
            title = tx.get("title", "")
            check(f"title non-empty: '{title}'", len(title) >= 2, f"title='{title}'")
            check("sourceType=text", tx.get("sourceType") == "text")

        # ---- 4. URL Import + Duplicate ----
        print("\n-- 4. URL Import + Dedup --")
        st, result2 = _post_form("/api/ingest/stream", {"url": "https://httpbin.org/html"})
        check("POST URL import -> 200", st == 200, f"status={st}")
        tx_id2 = result2.get("id", "")
        if tx_id2:
            st, tx2, _ = _get(f"/api/transcripts/{tx_id2}")
            check("sourceType=webpage", tx2.get("sourceType") == "webpage",
                  f"got {tx2.get('sourceType')}")
            check("title not raw URL", not tx2.get("title", "").startswith("http"),
                  f"title='{tx2.get('title')}'")
        # Duplicate
        st, dup = _post_form("/api/ingest/stream", {"url": "https://httpbin.org/html"})
        check("Duplicate URL blocked",
              "duplicate" in str(dup).lower(), str(dup)[:200])

        # ---- 5. Document List ----
        print("\n-- 5. Document List --")
        st, docs, _ = _get("/api/transcripts")
        check("GET /api/transcripts -> 200", st == 200)
        check("at least 2 docs", len(docs) >= 2, f"count={len(docs)}")

        # ---- 6. CRUD ----
        print("\n-- 6. CRUD (Update/Delete) --")
        if tx_id:
            st, patch = _json_req("PATCH", f"/api/transcripts/{tx_id}",
                                  {"title": "SmokeTest Title"})
            check("PATCH title -> 200", st == 200, f"status={st}")
            check("title updated", patch.get("title") == "SmokeTest Title", str(patch)[:100])
            st, _ = _json_req("DELETE", f"/api/transcripts/{tx_id}", None)
            check("DELETE -> 200", st == 200)
            st, _, _ = _get(f"/api/transcripts/{tx_id}")
            check("deleted doc -> 404", st == 404)

        # ---- 7. Engine Bindings Merge ----
        print("\n-- 7. Engine Bindings Merge --")
        st, binds, _ = _get("/api/engine-bindings")
        check("GET bindings -> 200", st == 200)
        old_asr = binds.get("asr")
        old_doc = binds.get("doc")
        # ⚠️ 这里**刻意不再**顺手塞一个无效引擎 id（旧版是 `{"asr": None, "doc": 99999}`）：
        # 那会把绑定写坏，而在被污染的真实配置上表现为「用户的 doc 引擎永久失效、且
        # 完全看不出原因」。要测的语义（null = 不修改）并不需要它。
        st, _ = _json_req("POST", "/api/engine-bindings", {"bindings": {"asr": None}})
        check("POST null merge -> 200", st == 200, f"status={st}")
        st, binds2, _ = _get("/api/engine-bindings")
        check("null does not overwrite asr",
              binds2.get("asr") == old_asr or binds2.get("asr") is not None,
              f"before={old_asr} after={binds2.get('asr')}")
        check("merge leaves doc untouched", binds2.get("doc") == old_doc,
              f"before={old_doc} after={binds2.get('doc')}")

        # ---- 8. Logs ----
        print("\n-- 8. Logs --")
        st, logs, _ = _get("/api/logs")
        check("GET /api/logs -> 200", st == 200)
        check("log entries > 0", logs.get("total", 0) > 0, f"total={logs.get('total')}")

        # ---- 9. Export ----
        print("\n-- 9. Export --")
        if tx_id2:
            for fmt in ("md", "html"):
                st, content = _get_raw(
                    f"/api/export/{tx_id2}?format={fmt}&mode=transcript")
                check(f"{fmt.upper()} export -> 200", st == 200, f"status={st}")
            st, html = _get_raw(f"/api/export/{tx_id2}?format=html&mode=transcript")
            check("HTML contains <html>", b"<html" in html)

        # ---- 10. Config + Masking ----
        print("\n-- 10. Config Masking --")
        st, cfg, _ = _get("/api/config")
        check("GET /api/config -> 200", st == 200)
        providers = cfg.get("providers", [])
        has_keys = any(p.get("api_key") for p in providers)
        is_masked = any("***" in str(p.get("api_key", "")) for p in providers)
        check("sensitive keys masked", is_masked or not has_keys,
              "OK if no keys configured")

        # ---- 11. Conversations ----
        print("\n-- 11. Conversations --")
        st, convs, _ = _get("/api/conversations")
        check("GET /api/conversations -> 200", st == 200)

    finally:
        print("\n-- Cleanup --")
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        print("Backend stopped")

        # ⚠️ **隔离自证**（必须在 rmtree 之前）。前面那些断言全绿**不能**说明隔离生效了 ——
        # 它们测的是后端的行为，不是「后端在哪个目录里干活」。这两条才是。
        # 而且它们是**有区分力**的：把 `env=env` 去掉（或漏掉临时 config 里的 `data_dir`），
        # 后端就会去读真实配置、把 Store 落到 `~/.limo` —— 于是临时 config 始终只有我们
        # 开头写的那一行、临时 data 目录根本不存在，**两条都红**（模式 #38）。
        cfg_used = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
        check("后端写回了临时 config（隔离生效）",
              "engine_bindings" in cfg_used,
              f"临时 config 只有 {len(cfg_used.splitlines())} 行 —— 后端没用它")
        check("Store 落在临时 data_dir（隔离生效）",
              (tmp_root / "data" / "index.json").exists(),
              f"{tmp_root / 'data'} 下没有 index.json")
        shutil.rmtree(tmp_root, ignore_errors=True)

    # ---- 隔离自证：真实配置必须毫发无损（模式 #58：跑完断言「未污染真实位置」）----
    print("\n-- Isolation --")
    real_after = real_cfg.stat().st_mtime_ns if real_cfg.exists() else None
    check(f"真实配置未被改动 ({real_cfg})", real_before == real_after,
          f"mtime {real_before} -> {real_after}"
          "（若你恰好在跑测试期间改过设置，此项可忽略）")

    print(f"\n{'=' * 50}")
    print(f"Results: {PASSED} passed / {FAILED} failed / {PASSED + FAILED} total")
    if FAILED == 0:
        print("ALL PASSED")
    else:
        print(f"{FAILED} FAILURES")
    print(f"{'=' * 50}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
