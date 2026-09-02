# FlowHub 后端镜像：Python 3.13 + 内置 opencode CLI
# 构建：docker build -t flowhub-backend -f backend/Dockerfile backend
FROM python:3.13-slim

# 构建时可通过 --build-arg HTTP_PROXY/HTTPS_PROXY 使用临时代理；默认不配置。
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ENV HTTP_PROXY=${HTTP_PROXY} HTTPS_PROXY=${HTTPS_PROXY} NO_PROXY=${NO_PROXY}

# opencode 官方安装脚本依赖 curl / unzip / ca-certificates；git 供仓库镜像 bare clone 使用
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl unzip ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# 内置安装 opencode（官方脚本 → /root/.opencode/bin/opencode）；网络不稳时重试 3 次，
# 仍失败则跳过（opencode 为可选运行时，缺失只影响 opencode 执行器，不影响 FlowHub 主体）
RUN for i in 1 2 3; do curl -fsSL --connect-timeout 20 https://opencode.ai/install | bash && break || sleep 5; done; \
    test -x /root/.opencode/bin/opencode || echo "WARN: opencode install skipped (network unavailable)"
ENV PATH="/root/.opencode/bin:${PATH}"

WORKDIR /app

# 依赖层（利用缓存）
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# 运行配置
ENV PYTHONUNBUFFERED=1
EXPOSE 8001

# 默认启动（外部依赖 PostgreSQL/Redis/MinIO 通过 env 配置）
CMD ["uvicorn", "flowhub_api.main:app", "--host", "0.0.0.0", "--port", "8001"]
