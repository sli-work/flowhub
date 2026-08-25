# FlowHub 后端镜像：Python 3.13 + 内置 opencode CLI
# 构建：docker build -t flowhub-backend -f backend/Dockerfile backend
FROM python:3.13-slim

# 构建时可通过 --build-arg HTTP_PROXY/HTTPS_PROXY 使用临时代理；默认不配置。
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ENV HTTP_PROXY=${HTTP_PROXY} HTTPS_PROXY=${HTTPS_PROXY} NO_PROXY=${NO_PROXY}

# opencode 官方安装脚本依赖 curl / unzip / ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 内置安装 opencode（官方脚本 → /root/.opencode/bin/opencode）
# 本地/生产需通过 opencode login 或配置 api key 后方可调用模型
RUN curl -fsSL https://opencode.ai/install | bash
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
