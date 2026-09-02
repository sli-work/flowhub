# FlowHub 后端镜像：Python 3.13
# 构建：docker build -t flowhub-backend -f backend/Dockerfile backend
FROM python:3.13-slim

# 依赖统一走内网 PyPI 仓库（直连），不再通过 HTTP 代理构建
# git 供仓库镜像 bare clone 使用
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖层（利用缓存）；默认走内网 PyPI 仓库（可 --build-arg PIP_INDEX_URL 覆盖）
ARG PIP_INDEX_URL=http://192.168.239.230:8887/repository/mc-group-pypi/simple/
ARG PIP_TRUSTED_HOST=192.168.239.230
COPY pyproject.toml README.md ./
COPY src ./src
# uv 安装依赖（并行下载/硬链接缓存，比 pip 快很多）；uv 本身也取自内网仓库
RUN pip install --no-cache-dir --index-url=${PIP_INDEX_URL} --trusted-host ${PIP_TRUSTED_HOST} uv \
    && uv pip install --system --no-cache --index-url=${PIP_INDEX_URL} --allow-insecure-host=${PIP_TRUSTED_HOST} .

# 运行配置
ENV PYTHONUNBUFFERED=1
EXPOSE 8001

# 默认启动（外部依赖 PostgreSQL/Redis/MinIO 通过 env 配置）
CMD ["uvicorn", "flowhub_api.main:app", "--host", "0.0.0.0", "--port", "8001"]
