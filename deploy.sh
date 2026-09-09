#!/usr/bin/env bash
# FlowHub 一键部署脚本：本机构建 → 上传 → 目标机备份/替换 → 重建容器 → 端到端验证
#
# 用法：
#   ./deploy.sh              # 默认部署到 root@192.168.21.195:/opt/flowhub
#   ./deploy.sh --skip-build # 跳过本地构建（复用上次 dist）
#   ./deploy.sh --restart    # 只重启目标机 backend/frontend 容器，不传代码
#
# 说明：
# - 远端 compose 项目名固定为 flowhub（容器 flowhub-*，数据卷 flowhub_pgdata 等）
#   如用其他项目名，用 REMOTE_PROJECT 环境变量覆盖：REMOTE_PROJECT=myproj ./deploy.sh
# - deploy/.env 中 BOOTSTRAP_ADMIN_PASSWORD 为远端 admin 密码（脚本自动用它做验证）
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-192.168.21.195}"
REMOTE_USER="${REMOTE_USER:-root}"
REMOTE_DIR="${REMOTE_DIR:-/opt/flowhub}"
REMOTE_PROJECT="${REMOTE_PROJECT:-flowhub}"
FRONTEND_PORT="${FRONTEND_PORT:-8088}"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8)
TARGET="${REMOTE_USER}@${REMOTE_HOST}"
ROOT="$(cd "$(dirname "$0")" && pwd)"

SKIP_BUILD=0
RESTART_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    --restart) RESTART_ONLY=1 ;;
    *) echo "未知参数：$arg"; exit 1 ;;
  esac
done

log() { printf '\n\033[1;34m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
ssh_t() { ssh "${SSH_OPTS[@]}" "$TARGET" "$@" 2> >(grep -vE "WARNING|store now|openssh" >&2); }

# 远端 sed 的密码值含特殊字符，这里不做值校验，仅读取用于登录验证
remote_admin_pw() {
  ssh_t "grep '^BOOTSTRAP_ADMIN_PASSWORD=' ${REMOTE_DIR}/backend/deploy/.env | cut -d= -f2-"
}

if [[ $RESTART_ONLY -eq 1 ]]; then
  log "重启模式：只重建目标机 backend 并重启 frontend"
  ssh_t "cd ${REMOTE_DIR}/backend && docker-compose -p ${REMOTE_PROJECT} -f deploy/docker-compose.yml up -d --build backend 2>&1 | tail -3 && docker-compose -p ${REMOTE_PROJECT} -f deploy/docker-compose.yml restart frontend 2>&1 | tail -1"
  exit 0
fi

# ---------- 1. 本地构建 ----------
log "本地构建前端（产物 frontend/dist）"
if [[ $SKIP_BUILD -eq 0 ]]; then
  (cd "$ROOT/frontend" && npm run build 2>&1 | grep -E "error|✓ built" | head -3)
else
  log "跳过构建（--skip-build）"
fi
[[ -f "$ROOT/frontend/dist/index.html" ]] || { echo "dist 不存在，请先构建"; exit 1; }

# ---------- 2. 打包上传 ----------
log "打包并上传（backend 全量 + frontend 源码/配置 + dist）"
STAMP=$(date +%Y%m%d-%H%M%S)
COPYFILE_DISABLE=1 tar czf /tmp/flowhub-code-${STAMP}.tgz \
  --exclude='.git' --exclude='node_modules' --exclude='.venv' --exclude='__pycache__' \
  --exclude='.pytest_cache' --exclude='.repo-mirror' --exclude='uv.lock' \
  -C "$ROOT" backend docs frontend/src frontend/package.json frontend/package-lock.json \
  frontend/vite.config.ts frontend/index.html frontend/tsconfig*.json frontend/components.json 2>/dev/null || true
COPYFILE_DISABLE=1 tar czf /tmp/flowhub-dist-${STAMP}.tgz -C "$ROOT/frontend" dist
COPYFILE_DISABLE=1 tar czf /tmp/flowhub-nginx-${STAMP}.tgz -C "$ROOT/backend/deploy" nginx.conf docker-compose.yml
scp "${SSH_OPTS[@]}" /tmp/flowhub-code-${STAMP}.tgz /tmp/flowhub-dist-${STAMP}.tgz /tmp/flowhub-nginx-${STAMP}.tgz "$TARGET:/tmp/" 2> >(grep -vE "WARNING|store now|openssh" >&2)

# ---------- 3. 远端：备份 + 替换 ----------
log "远端备份当前版本并替换代码（.env / 数据卷不受影响）"
ssh_t "set -euo pipefail
cd ${REMOTE_DIR}
tar czf /opt/flowhub-backup-${STAMP}.tgz --exclude='backend/.venv' --exclude='__pycache__' backend/frontend_backup_marker backend frontend 2>/dev/null || tar czf /opt/flowhub-backup-${STAMP}.tgz backend frontend
echo \"备份: /opt/flowhub-backup-${STAMP}.tgz\"
tar xzf /tmp/flowhub-code-${STAMP}.tgz -C ${REMOTE_DIR} 2>/dev/null
cd frontend && rm -rf dist && tar xzf /tmp/flowhub-dist-${STAMP}.tgz 2>/dev/null && cd ..
# 清理 macOS AppleDouble 文件 + 放宽权限（nginx 容器内 nginx 用户需可读）
find frontend/dist backend -name '._*' -delete 2>/dev/null || true
find frontend/dist backend -name '.DS_Store' -delete 2>/dev/null || true
chmod -R a+rX frontend/dist
echo REPLACE_OK"

# ---------- 4. 远端：重建容器 ----------
log "重建 backend 镜像并重启 frontend（compose 项目 ${REMOTE_PROJECT}）"
ssh_t "set -euo pipefail
cd ${REMOTE_DIR}/backend
docker-compose -p ${REMOTE_PROJECT} -f deploy/docker-compose.yml up -d --build backend 2>&1 | tail -3
docker-compose -p ${REMOTE_PROJECT} -f deploy/docker-compose.yml up -d --force-recreate frontend 2>&1 | tail -1"

# ---------- 5. 验证 ----------
log "等待启动并端到端验证"
sleep 6
VERIFY=$(ssh_t "PW=\$(grep '^BOOTSTRAP_ADMIN_PASSWORD=' ${REMOTE_DIR}/backend/deploy/.env | cut -d= -f2-)
echo -n 'index='; curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:${FRONTEND_PORT}/
echo -n ' login='; curl -s -m 5 -X POST http://127.0.0.1:${FRONTEND_PORT}/api/v1/auth/login -H 'Content-Type: application/json' --data-raw \"{\\\"account\\\":\\\"admin\\\",\\\"password\\\":\\\"\$PW\\\"}\" | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"code\"])'
echo -n ' tags='; TOKEN=\$(curl -s -m 5 -X POST http://127.0.0.1:${FRONTEND_PORT}/api/v1/auth/login -H 'Content-Type: application/json' --data-raw \"{\\\"account\\\":\\\"admin\\\",\\\"password\\\":\\\"\$PW\\\"}\" | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"data\"][\"token\"])')
curl -s -m 5 http://127.0.0.1:${FRONTEND_PORT}/api/v1/tags -H \"Authorization: Bearer \$TOKEN\" | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"code\"])'
echo -n ' containers='; docker ps --format '{{.Names}} {{.Status}}' | grep flowhub | wc -l")
echo "$VERIFY"
echo "$VERIFY" | grep -q "index=200" && echo "$VERIFY" | grep -q "login=0" && echo "$VERIFY" | grep -q "tags=0" \
  && log "✅ 部署成功" || { log "❌ 验证未通过，请检查远端 docker logs flowhub-backend-1"; exit 1; }
log "完成。访问 http://${REMOTE_HOST}:${FRONTEND_PORT}"
