# 使用官方 Python 3.12 镜像（国内镜像源加速）
FROM docker.1ms.run/library/python:3.12-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_HTTP_TIMEOUT=300 \
    UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# 安装 uv（使用国内镜像）
RUN pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制依赖文件
COPY pyproject.toml uv.lock ./

# 安装依赖（不安装开发依赖）；构建时一次装齐，含项目本身
RUN uv sync --frozen --no-dev --no-install-project

# 复制应用代码
COPY . .

# 把项目本身装进 venv（代码已就位），使 venv 与 uv.lock 完全一致
RUN uv sync --frozen --no-dev

# 启动命令（直接用 venv 内的 python，避免 uv run 每次启动做环境同步/联网校验）
CMD ["/app/.venv/bin/python", "app.py"]
