"""回调接收测试服务器。

启动一个轻量 HTTP 服务器，监听 POST /callback，
接收并打印管线各阶段的回调通知。

用法：
    python tests/test_callback_server.py              # 默认监听 0.0.0.0:8000
    python tests/test_callback_server.py --port 9000  # 指定端口

配合主服务使用示例：
    # 1. 先启动本回调服务器
    python tests/test_callback_server.py --port 8000

    # 2. 提交文件解析，指定 callback_url
    curl -X POST "http://localhost:5019/file/parse?mode=async&callback_url=http://localhost:8000/callback" \\
         -F "file=@test.pdf"

    # 回调服务器控制台将依次打印：
    # [2026-02-09 12:00:01] POST /callback
    # {"file_id": "abc123", "status": "parsing"}
    # [2026-02-09 12:00:05] POST /callback
    # {"file_id": "abc123", "status": "chunking"}
    # ...
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI(title="Callback Test Server")

# 阶段对应的中文名
STAGE_LABELS = {
    "parsing": "解析中",
    "chunking": "分块中",
    "embedding": "向量化中",
    "extracting": "字段提取中",
    "analyzing": "逻辑分析中",
    "complete": "全部完成",
}


@app.post("/callback")
async def receive_callback(request: Request):
    """接收并打印回调通知。"""
    body = await request.json()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    file_id = body.get("file_id", "?")
    status = body.get("status", "?")
    label = STAGE_LABELS.get(status, status)

    print(f"\n[{now}] POST /callback")
    print(f"  file_id : {file_id}")
    print(f"  status  : {status} ({label})")
    print(f"  raw     : {json.dumps(body, ensure_ascii=False)}")

    return {"received": True}


@app.get("/")
async def index():
    """健康检查。"""
    return {"message": "Callback test server is running. POST /callback to receive notifications."}


def main():
    parser = argparse.ArgumentParser(description="回调接收测试服务器")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="监听端口 (默认: 8000)")
    args = parser.parse_args()

    print(f"回调测试服务器启动: http://{args.host}:{args.port}")
    print(f"回调地址: http://localhost:{args.port}/callback")
    print("等待接收回调通知...\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
