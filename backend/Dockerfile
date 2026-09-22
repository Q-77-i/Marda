# 后端镜像：uv 装依赖（锁文件）→ uvicorn 运行
# 版本对齐本地开发环境（Python 3.13），避免 uv.lock 走 <3.12 分支的解析结果
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# uv 本身走清华源（依赖走 pyproject 里已配置的 [[tool.uv.index]]）
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple uv

WORKDIR /app

# 依赖声明单独一层，改代码不触发重装
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY scripts ./scripts

EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
