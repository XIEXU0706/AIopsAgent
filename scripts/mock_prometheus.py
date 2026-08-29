"""Prometheus 查询 API 模拟器 —— 提供 /api/v1/query 与 /api/v1/query_range

用途：
  - 联调 / 演示：让 MindBridge 的 query_metrics MCP 工具有真实的 Prometheus
    可查，无需部署真 Prometheus。返回构造的 CPU/内存/QPS 曲线。
  - 对标 scripts/mock_alertmanager.py，构成「告警推送 + 指标查询」双模拟。

用法：
    # 启动模拟 Prometheus（默认 :9090）
    python scripts/mock_prometheus.py

    # 自定义端口
    python scripts/mock_prometheus.py --port 9091
"""

import argparse
import math
import random
import sys
import time
from datetime import datetime, timezone

from pathlib import Path

import httpx  # 复用同一依赖，便于直接运行

# 允许以脚本方式直接运行
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    import uvicorn
except ImportError:  # pragma: no cover
    print("需要 fastapi/uvicorn 才能运行 mock_prometheus")
    raise


app = FastAPI(title="Mock Prometheus")


def _now_ns() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _gen_series(promql: str):
    """根据 promql 关键字返回一段伪曲线（value 列表）。"""
    seed = sum(ord(c) for c in promql)
    random.seed(seed)

    # 粗略按关键字挑曲线形态
    if "node_cpu" in promql or "cpu" in promql:
        base, amp = 85.0, 12.0
    elif "memory" in promql or "mem" in promql:
        base, amp = 70.0, 8.0
    elif "rate(" in promql or "qps" in promql or "http_requests" in promql:
        base, amp = 120.0, 60.0
    else:
        base, amp = 50.0, 20.0

    return [
        round(max(0.0, min(100.0, base + amp * math.sin(i / 3.0) + random.uniform(-5, 5))), 2)
        for i in range(30)
    ]


def _instant_result(promql: str) -> dict:
    values = _gen_series(promql)
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"__name__": "mock_metric", "instance": "app-node-01:9100"},
                    "value": [_now_ns() / 1000, str(values[-1])],
                }
            ],
        },
    }


def _range_result(promql: str, start: float, end: float, step: int) -> dict:
    values = _gen_series(promql)
    step_f = max(1, int(step))
    samples = []
    ts = int(start)
    while ts <= int(end):
        idx = min(len(values) - 1, (ts // step_f) % len(values))
        samples.append([ts, str(values[idx])])
        ts += step_f
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"__name__": "mock_metric", "instance": "app-node-01:9100"},
                    "values": samples,
                }
            ],
        },
    }


@app.get("/api/v1/query")
async def query(request: Request):
    promql = request.query_params.get("query", "")
    return JSONResponse(_instant_result(promql))


@app.get("/api/v1/query_range")
async def query_range(request: Request):
    promql = request.query_params.get("query", "")
    now = time.time()
    start = float(request.query_params.get("start", now - 1800))
    end = float(request.query_params.get("end", now))
    step = int(request.query_params.get("step", 60).rstrip("s") or 60)
    return JSONResponse(_range_result(promql, start, end, step))


def main():
    parser = argparse.ArgumentParser(description="Prometheus 查询 API 模拟器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9090)
    args = parser.parse_args()
    print(f"Mock Prometheus 启动: http://{args.host}:{args.port}")
    print("  支持 /api/v1/query 与 /api/v1/query_range")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
