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

# 安装依赖（不安装开发依赖）
RUN uv sync --frozen --no-dev --no-install-project

# 复制应用代码
COPY . .

# 启动命令（端口从 config.yaml 读取）
CMD ["uv", "run", "python", "app.py"]
