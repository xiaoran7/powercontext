#!/usr/bin/env bash
# 启动本地 seekdb 实例（赛题要求的「随应用提供本地 server 的启动脚本与连接配置」）。
#
# 用法：
#   bash scripts/start_seekdb.sh            # 前台启动（Ctrl-C 停止）
#   bash scripts/start_seekdb.sh --daemon   # 后台启动，日志写入 seekdb_data/log/
#   bash scripts/start_seekdb.sh --stop     # 停止由本脚本后台启动的实例
#
# 查找 seekdb 可执行文件的顺序：$SEEKDB_BIN → PATH 中的 seekdb → 仓库内编译产物。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

BASE_DIR="${SEEKDB_BASE_DIR:-$APP_DIR/seekdb_data}"
DATA_DIR="${SEEKDB_DATA_DIR:-$BASE_DIR/store}"
LOG_DIR="${SEEKDB_LOG_DIR:-$BASE_DIR/log}"
PORT="${SEEKDB_PORT:-2881}"
PID_FILE="$BASE_DIR/seekdb.pid"

find_seekdb() {
  if [[ -n "${SEEKDB_BIN:-}" ]]; then
    echo "$SEEKDB_BIN"
    return
  fi
  if command -v seekdb >/dev/null 2>&1; then
    command -v seekdb
    return
  fi
  local candidate
  for candidate in \
    "$APP_DIR/../seekdb/build_release/src/observer/seekdb" \
    "$APP_DIR/../seekdb/build_debug/src/observer/seekdb"; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return
    fi
  done
  return 1
}

if ! SEEKDB_BIN_PATH="$(find_seekdb)"; then
  cat >&2 <<'EOF'
未找到 seekdb 可执行文件。任选一种方式获取后重试：

  1) 官方发行包（推荐，Ubuntu/Debian）：
     https://github.com/oceanbase/seekdb/releases  →  seekdb_1.4.0-*.deb
     sudo dpkg -i seekdb_*.deb

  2) 从源码编译：
     git clone https://github.com/oceanbase/seekdb && cd seekdb && bash build.sh

  3) 已在别处安装：export SEEKDB_BIN=/path/to/seekdb

  4) 使用已有的远程实例：export SEEKDB_HOST=<host>，无需本地启动。
EOF
  exit 1
fi

mkdir -p "$BASE_DIR" "$DATA_DIR" "$LOG_DIR"

COMMON_ARGS=(
  --base-dir "$BASE_DIR"
  --data-dir "$DATA_DIR"
  --port "$PORT"
  --parameter "datafile_size=${SEEKDB_DATAFILE_SIZE:-512M}"
  --parameter "log_disk_size=${SEEKDB_LOG_DISK_SIZE:-2G}"
  --parameter "memory_limit=${SEEKDB_MEMORY_LIMIT:-2G}"
)

stop_instance() {
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    kill "$(cat "$PID_FILE")"
    echo "已停止 seekdb (pid=$(cat "$PID_FILE"))"
    rm -f "$PID_FILE"
  else
    echo "没有由本脚本启动的 seekdb 实例（$PID_FILE 不存在或进程已退出）"
  fi
}

case "${1:-}" in
  --stop)
    stop_instance
    ;;
  --daemon)
    if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
      echo "端口 $PORT 已被占用，seekdb 可能已在运行。"
      exit 0
    fi
    nohup "$SEEKDB_BIN_PATH" --nodaemon "${COMMON_ARGS[@]}" \
      >>"$LOG_DIR/server.stdout.log" 2>&1 &
    echo $! >"$PID_FILE"
    echo "seekdb 已在后台启动 (pid=$(cat "$PID_FILE"), port=$PORT, base_dir=$BASE_DIR)"
    echo "日志：$LOG_DIR/server.stdout.log"
    ;;
  *)
    echo "启动 seekdb（前台）：$SEEKDB_BIN_PATH"
    echo "  base_dir=$BASE_DIR  data_dir=$DATA_DIR  port=$PORT"
    exec "$SEEKDB_BIN_PATH" --nodaemon "${COMMON_ARGS[@]}"
    ;;
esac
