#!/bin/bash
# restart-backend.sh — 彻底杀掉旧进程后重启
PORT=${1:-20020}
ROOT="$(dirname "$0")/.."

# 温和关闭端口上所有 LISTENING 进程
echo ">>> 温和关闭端口 $PORT 上的进程…"
PIDS=$(netstat -ano 2>/dev/null | grep ":$PORT" | grep LISTENING | awk '{print $5}' | sort -u)
for pid in $PIDS; do
  echo "    温和关闭 PID $pid"
  cmd //c "taskkill /PID $pid" 2>/dev/null
  sleep 2
done

# 确认端口释放（幽灵 socket 杀不掉时警告并继续）
STILL=$(netstat -ano 2>/dev/null | grep ":$PORT" | grep LISTENING)
if [ -n "$STILL" ]; then
  echo ">>> 警告: 端口 $PORT 仍被占用，可能是幽灵 socket"
  echo "    请关闭终端窗口后重新打开，不要用 taskkill /F"
  echo "    尝试继续启动…"
fi

# 启动
echo ">>> Starting backend on port $PORT ..."
cd "$ROOT/backend"
python -m uvicorn main:app --host 127.0.0.1 --port $PORT --reload 2>&1
