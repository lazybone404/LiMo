#!/bin/bash
# LiMo 一键启动
# 用法: bash start.sh

ROOT="$(cd "$(dirname "$0")" && pwd)"
PORT=7413

echo "========================================"
echo "  LiMo"
echo "========================================"

# ── 温和关闭旧进程 ──
_PIDS=$(netstat -ano 2>/dev/null | grep ":$PORT" | grep LISTENING | awk '{print $5}' | grep -v "^0$" | sort -u)
for _pid in $_PIDS; do
  echo ">>> 温和关闭旧进程 PID=$_pid (端口 $PORT)"
  cmd //c "taskkill /PID $_pid" 2>/dev/null
  sleep 2
done
# 温和关闭失败的，说明是僵尸/幽灵 socket，不要用 /F 硬杀，警告用户手动处理
_STILL=$(netstat -ano 2>/dev/null | grep ":$PORT" | grep LISTENING | awk '{print $5}' | grep -v "^0$" | sort -u)
if [ -n "$_STILL" ]; then
  echo ">>> 警告: 端口 $PORT 仍被占用（PID: $_STILL），可能是幽灵 socket"
  echo "    请关闭终端窗口后重新打开，或手动在任务管理器结束这些 PID"
  echo "    尝试继续启动…"
fi

# ── 清理函数 ──
cleanup() {
  echo ""
  echo ">>> 正在停止..."
  # 先温和关闭后端进程
  if [ -n "$BACKEND_PID" ]; then
    cmd //c "taskkill /PID $BACKEND_PID /T" 2>/dev/null
    sleep 2
    # 确认是否还活着——还活着才用 /F（自己的子进程，不会有幽灵）
    if cmd //c "tasklist /FI \"PID eq $BACKEND_PID\" 2>nul" | grep -q "$BACKEND_PID" 2>/dev/null; then
      echo ">>> 后端进程未响应，强制终止…"
      cmd //c "taskkill /F /PID $BACKEND_PID /T" 2>/dev/null
    fi
  fi
  echo ">>> 已停止"
}

trap cleanup EXIT INT TERM

# ── 启动后端（后台）──
echo ">>> 后端: 127.0.0.1:$PORT"
cd "$ROOT/backend"
python -m uvicorn main:app --host 127.0.0.1 --port $PORT --reload &
BACKEND_PID=$!

# 等后端就绪
while ! curl -s "http://127.0.0.1:$PORT/health" > /dev/null 2>&1; do
  sleep 0.5
done
echo ">>> 后端就绪 ✓"

# ── 启动前端（前台，Ctrl+C 一起停）──
echo ">>> 前端: http://localhost:5173"
cd "$ROOT/frontend"
npx vite --host 127.0.0.1 --port 5173
