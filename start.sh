#!/bin/bash
set -e

cd "$(dirname "$0")"

# 使用 uv run 启动服务器，自动安装依赖
# Start server using project dependencies
uv run uvicorn app.web.server:app --reload --host 0.0.0.0 --port 8000
