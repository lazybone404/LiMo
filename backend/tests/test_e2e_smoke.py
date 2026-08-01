"""
LiMo Eagle Eye Test - startup smoke + core endpoint verification.
Usage: uv run python tests/test_e2e_smoke.py
No external LLM API dependencies.
"""
import subprocess, sys, time, json, os, urllib.request, urllib.parse, urllib.error

BASE = "http://127.0.0.1:7413"
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
    global PASSED, FAILED
    if condition:
        PASSED += 1; print(f"  PASS: {label}")
    else:
        FAILED += 1; print(f"  FAIL: {label}  --  {detail}")

def start_server():
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "7413"],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    for _ in range(30):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=2) as r:
                if r.status == 200:
                    return proc
        except: pass
    proc.kill()
    raise RuntimeError("Backend did not start within 15s")

# ===== START =====
print("=" * 50)
print("LiMo Eagle Eye Test")
print("=" * 50)
print("\nStarting backend...")
server = start_server()
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
    st, _ = _json_req("POST", "/api/engine-bindings",
                      {"bindings": {"asr": None, "doc": 99999}})
    check("POST null merge -> 200", st == 200, f"status={st}")
    st, binds2, _ = _get("/api/engine-bindings")
    check("null does not overwrite asr",
          binds2.get("asr") == old_asr or binds2.get("asr") is not None,
          f"before={old_asr} after={binds2.get('asr')}")

    # ---- 8. Auto-Fallback Toggle ----
    print("\n-- 8. Auto-Fallback Toggle --")
    st, fb, _ = _get("/api/engine-auto-fallback")
    current = fb.get("enabled", True)
    st, _ = _json_req("POST", "/api/engine-auto-fallback",
                      {"enabled": not current})
    check("toggle -> 200", st == 200)
    st, fb2, _ = _get("/api/engine-auto-fallback")
    check(f"value changed to {not current}",
          fb2.get("enabled") == (not current))
    _json_req("POST", "/api/engine-auto-fallback", {"enabled": current})

    # ---- 9. Logs ----
    print("\n-- 9. Logs --")
    st, logs, _ = _get("/api/logs")
    check("GET /api/logs -> 200", st == 200)
    check("log entries > 0", logs.get("total", 0) > 0, f"total={logs.get('total')}")

    # ---- 10. Export ----
    print("\n-- 10. Export --")
    if tx_id2:
        for fmt in ("md", "html"):
            st, content = _get_raw(
                f"/api/export/{tx_id2}?format={fmt}&mode=transcript")
            check(f"{fmt.upper()} export -> 200", st == 200, f"status={st}")
        st, html = _get_raw(f"/api/export/{tx_id2}?format=html&mode=transcript")
        check("HTML contains <html>", b"<html" in html)

    # ---- 11. Config + Masking ----
    print("\n-- 11. Config Masking --")
    st, cfg, _ = _get("/api/config")
    check("GET /api/config -> 200", st == 200)
    providers = cfg.get("providers", [])
    has_keys = any(p.get("api_key") for p in providers)
    is_masked = any("***" in str(p.get("api_key", "")) for p in providers)
    check("sensitive keys masked", is_masked or not has_keys,
          "OK if no keys configured")

    # ---- 12. Conversations ----
    print("\n-- 12. Conversations --")
    st, convs, _ = _get("/api/conversations")
    check("GET /api/conversations -> 200", st == 200)

finally:
    print("\n-- Cleanup --")
    server.terminate()
    server.wait(timeout=5)
    print("Backend stopped")

print(f"\n{'='*50}")
print(f"Results: {PASSED} passed / {FAILED} failed / {PASSED+FAILED} total")
if FAILED == 0:
    print("ALL PASSED")
else:
    print(f"{FAILED} FAILURES")
print(f"{'='*50}")
