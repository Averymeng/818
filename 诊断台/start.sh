#!/usr/bin/env bash
# 诊断台后端启动脚本（含崩溃自重启 + 0.0.0.0 监听 + 环境变量检查）
# 用法：
#   export DEEPSEEK_API_KEY=sk-xxxxx
#   ./start.sh                # 默认监听 0.0.0.0:8000
#   PORT=9000 ./start.sh      # 自定义端口
set -e
cd "$(dirname "$0")"

if [ -z "$DEEPSEEK_API_KEY" ]; then
  echo "错误：请先设置环境变量 DEEPSEEK_API_KEY" >&2
  echo "  export DEEPSEEK_API_KEY=sk-..." >&2
  exit 1
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
echo "启动诊断台：http://$HOST:$PORT （Ctrl+C 退出循环）"

# 崩溃自重启循环：进程异常退出后 5 秒自动拉起
while true; do
  python3 api_server.py --host "$HOST" --port "$PORT"
  echo "[$(date)] 进程退出，5 秒后重启" >&2
  sleep 5
done
