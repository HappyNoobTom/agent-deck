#!/usr/bin/env bash
# Agent Deck Docker 与宿主机 StreamDock 桥接的统一管理入口。
# 运行方式：在本文件所在项目目录调用；所有状态、日志和配置都保存在外置盘项目目录。
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
cd "${PROJECT_ROOT}"

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-agent-deck}"
BRIDGE_SESSION="${COMPOSE_PROJECT_NAME}-streamdock-bridge"
BRIDGE_LOG="${PROJECT_ROOT}/container-data/streamdock-bridge.log"
HOST_BRIDGE_URL="${AGENT_DECK_STREAMDOCK_BRIDGE_URL:-http://127.0.0.1:8767}"
CONTAINER_BRIDGE_URL="${AGENT_DECK_CONTAINER_BRIDGE_URL:-http://host.docker.internal:8767}"

usage() {
  cat <<EOF_USAGE
Usage: scripts/docker-agent-deck.sh <build|up|hardware-up|down|hardware-down|restart|status|logs|shell|hardware|bridge-start|bridge-stop|bridge-status>

Commands:
  build          构建 Docker 镜像
  up             启动 Docker 内的 fake 硬件模式
  hardware-up    启动 macOS SDK 桥接，并启动真实 N4 Pro 模式
  down           停止 Docker 服务
  hardware-down  停止 Docker 和 macOS 桥接
  restart        重建并重启当前 Docker 服务
  status         查看 Docker 服务
  hardware       查看桥接和 Docker 健康状态
  logs           查看 Agent Deck 日志
  shell          进入容器
  bridge-start   以 tmux 常驻启动宿主机桥接
  bridge-stop    停止宿主机桥接
  bridge-status  查看宿主机桥接健康状态
EOF_USAGE
}

bridge_start() {
  mkdir -p "${PROJECT_ROOT}/container-data"
  if tmux has-session -t "${BRIDGE_SESSION}" 2>/dev/null; then
    echo "StreamDock bridge already running (tmux ${BRIDGE_SESSION})"
    return 0
  fi
  tmux new-session -d -s "${BRIDGE_SESSION}" \
    "cd $(printf '%q' "${PROJECT_ROOT}") && exec $(printf '%q' "${PROJECT_ROOT}/.venv/bin/python") $(printf '%q' "${PROJECT_ROOT}/scripts/streamdock-bridge.py") >>$(printf '%q' "${BRIDGE_LOG}") 2>&1"
  sleep 1
  if ! tmux has-session -t "${BRIDGE_SESSION}" 2>/dev/null; then
    echo "StreamDock bridge failed to start; see ${BRIDGE_LOG}" >&2
    return 1
  fi
  echo "StreamDock bridge started (tmux ${BRIDGE_SESSION}); log: ${BRIDGE_LOG}"
}

bridge_stop() {
  if tmux has-session -t "${BRIDGE_SESSION}" 2>/dev/null; then
    tmux kill-session -t "${BRIDGE_SESSION}"
    echo "StreamDock bridge stopped"
  else
    echo "StreamDock bridge is not running"
  fi
}

case "${1:-}" in
  build)
    docker compose build
    ;;
  up)
    docker compose up -d
    echo "Agent Deck: http://127.0.0.1:8766/"
    ;;
  hardware-up)
    bridge_start
    AGENT_DECK_HARDWARE_MODE=streamdock AGENT_DECK_STREAMDOCK_BRIDGE_URL="${CONTAINER_BRIDGE_URL}" docker compose up -d --build --force-recreate
    echo "Agent Deck: http://127.0.0.1:8766/"
    echo "StreamDock bridge: ${HOST_BRIDGE_URL}/health"
    ;;
  down)
    docker compose down
    ;;
  hardware-down)
    docker compose down
    bridge_stop
    ;;
  restart)
    if tmux has-session -t "${BRIDGE_SESSION}" 2>/dev/null; then
      AGENT_DECK_HARDWARE_MODE=streamdock AGENT_DECK_STREAMDOCK_BRIDGE_URL="${CONTAINER_BRIDGE_URL}" docker compose up -d --build --force-recreate
    else
      docker compose up -d --build --force-recreate
    fi
    echo "Agent Deck: http://127.0.0.1:8766/"
    ;;
  status)
    docker compose ps
    ;;
  logs)
    docker compose logs -f --tail=100 agent-deck
    ;;
  shell)
    docker compose exec agent-deck sh
    ;;
  hardware)
    echo "--- macOS StreamDock bridge ---"
    curl -fsS "${HOST_BRIDGE_URL}/health" || true
    echo
    echo "--- Docker Agent Deck ---"
    docker compose ps
    ;;
  bridge-start)
    bridge_start
    ;;
  bridge-stop)
    bridge_stop
    ;;
  bridge-status)
    curl -fsS "${HOST_BRIDGE_URL}/health" || true
    echo
    ;;
  -h|--help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
