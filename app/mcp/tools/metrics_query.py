"""指标查询工具：向 Prometheus HTTP API 查询时序指标（CPU/内存/QPS 等）

排障第一步通常是「看指标」。告警由 Alertmanager 推送，但指标（曲线）需主动
向 Prometheus 查询——Prometheus 已定时从各实例拉取并存储，本工具只发一个
HTTP 请求要数据，不去服务器上取。

Prometheus 查询 API 文档（/api/v1/query 为瞬时，/api/v1/query_range 为区间）：
  GET {prometheus_url}/api/v1/query?query=<promql>
  GET {prometheus_url}/api/v1/query_range?query=<promql>&start=&end=&step=

失败隔离：查询异常不抛出，返回 status=failed，由上层 AsyncToolQueue 决定重试。
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def query_metrics(params: dict) -> dict:
    """查询 Prometheus 指标。

    params:
      promql   (必填): PromQL 表达式，如 'rate(http_requests_total[5m])'
      start    (可选): 区间查询起始 ISO/RFC3339 时间
      end      (可选): 区间查询结束时间
      step     (可选): 区间查询步长，如 '60s'
      instance (可选): 仅用于回显，便于上层定位
    """
    promql = params.get("promql", "").strip()
    if not promql:
        return {"status": "failed", "error": "missing_promql", "result": None}

    base = settings.prometheus_url.rstrip("/")
    if not base:
        return {"status": "failed", "error": "prometheus_not_configured", "result": None}

    start = params.get("start")
    end = params.get("end")
    step = params.get("step", "60s")

    try:
        async with httpx.AsyncClient(timeout=settings.prometheus_timeout) as client:
            if start and end:
                url = f"{base}/api/v1/query_range"
                resp = await client.get(url, params={
                    "query": promql, "start": start, "end": end, "step": step,
                })
            else:
                url = f"{base}/api/v1/query"
                resp = await client.get(url, params={"query": promql})

        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != "success":
            return {
                "status": "failed",
                "error": f"prometheus_error: {payload.get('error', 'unknown')}",
                "result": None,
            }

        results = payload.get("data", {}).get("result", [])
        logger.info("[METRICS] promql=%s hits=%d", promql, len(results))
        return {
            "status": "success",
            "promql": promql,
            "instance": params.get("instance", ""),
            "result_type": payload.get("data", {}).get("resultType", ""),
            "series_count": len(results),
            "result": results,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("[METRICS] query failed: %s", e)
        return {"status": "failed", "error": str(e), "result": None}
