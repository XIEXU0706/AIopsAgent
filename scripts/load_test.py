"""Locust 压测脚本 —— 告警接入接口吞吐与延迟基准

压测场景：
  - ingest（默认权重 3）：POST /api/v1/alerts，触发完整多 Agent 处置链路
  - list   （默认权重 1）：GET /api/v1/alerts，告警列表查询

用法：
    # 安装（开发工具，不进生产依赖）
    pip install locust

    # 启动被测服务（建议无 LLM Key 模式：全链路规则降级，测的是纯工程吞吐）
    uvicorn app.main:app --port 9092

    # 无界面压测：50 并发、每秒起 5 个、跑 60 秒，输出 CSV（含 P99）
    locust -f scripts/load_test.py --headless \\
        -u 50 -r 5 -t 60s --host http://127.0.0.1:9092 \\
        --csv results/loadtest

    # 轻量场景（只压查询接口，不产生告警处置副作用）
    locust -f scripts/load_test.py --headless --exclude-tags ingest \\
        -u 50 -r 5 -t 60s --host http://127.0.0.1:9092 --csv results/loadtest

结果解读（results/loadtest_stats.csv）：
  - Requests/s  = QPS（吞吐）
  - 99% 列      = P99 延迟（ms）
  - Failures    = 错误数（应为 0）

注意事项：
  - ingest 会真实触发后台处置（报告导出/预警落库），压测产生大量数据，
    跑完可清理 data/reports/ 与 data/alerts.db
  - 告警接入是 202 异步受理（BackgroundTasks 后台处置），
    接口吞吐不受 LLM/处置耗时影响 —— 这正是解耦设计的验证点
"""

import json
import random
import uuid

from locust import HttpUser, between, task, tag

# ── 告警 payload 模板（覆盖不同 error_type，验证处置链路稳定性） ──

ALERT_TEMPLATES = [
    {
        "source": "prometheus",
        "severity": "critical",
        "title": "MySQL 连接数暴涨",
        "message": "Host: db-01, Connections: 850/1000, too many connections",
        "error_type": "mysql_connection",
        "raw_data": {"metric": "mysql_connections", "instance": "db-01"},
    },
    {
        "source": "prometheus",
        "severity": "critical",
        "title": "Redis 内存打满",
        "message": "OOM command not allowed when used memory > maxmemory",
        "error_type": "redis_oom",
        "raw_data": {"metric": "redis_memory", "instance": "cache-01"},
    },
    {
        "source": "prometheus",
        "severity": "warning",
        "title": "磁盘使用率过高",
        "message": "disk usage at 94%, no space left on device",
        "error_type": "disk_full",
        "raw_data": {"metric": "disk_usage", "instance": "node-03"},
    },
    {
        "source": "prometheus",
        "severity": "warning",
        "title": "CPU 使用率过高",
        "message": "CPU usage at 96% (5m avg), load1=32",
        "error_type": "high_cpu",
        "raw_data": {"metric": "cpu_usage", "instance": "node-01"},
    },
    {
        "source": "prometheus",
        "severity": "critical",
        "title": "接口 5xx 激增",
        "message": "error_rate=18.3% (5m), p99_latency=4.2s",
        "error_type": "http_error",
        "raw_data": {"metric": "http_error_rate", "service": "order-api"},
    },
]

# Alertmanager webhook 原始格式（压测 webhook 接入路径）
ALERTMANAGER_TEMPLATE = {
    "version": "4",
    "status": "firing",
    "receiver": "loadtest",
    "groupKey": "loadtest:batch",
    "commonLabels": {},
    "commonAnnotations": {},
    "externalURL": "http://loadtest:9093",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "MySQLTooManyConnections",
                "severity": "critical",
                "job": "mysql",
                "instance": "db-master-01:3306",
            },
            "annotations": {
                "summary": "MySQL 连接数达到上限",
                "description": "max_connections=1000, threads_connected=998",
            },
            "startsAt": "2026-08-22T00:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "http://prometheus:9090",
        }
    ],
}


class AlertIngestUser(HttpUser):
    """告警平台压测用户：混合读写（模拟监控告警流 + 控制台查询）"""

    wait_time = between(0.05, 0.2)

    @task(3)
    @tag("ingest")
    def ingest_alert(self):
        """提交告警（202 异步受理，后台走多 Agent 处置）"""
        payload = dict(random.choice(ALERT_TEMPLATES))
        payload["id"] = f"loadtest-{uuid.uuid4().hex[:8]}"
        with self.client.post(
            "/api/v1/alerts",
            json=payload,
            name="POST /api/v1/alerts [ingest]",
            catch_response=True,
        ) as resp:
            if resp.status_code != 202:
                resp.failure(f"unexpected status {resp.status_code}")

    @task(1)
    @tag("ingest")
    def ingest_alertmanager_webhook(self):
        """Alertmanager webhook 接入（模拟真实告警源）"""
        payload = json.loads(json.dumps(ALERTMANAGER_TEMPLATE))  # deep copy
        payload["alerts"][0]["fingerprint"] = uuid.uuid4().hex[:16]
        with self.client.post(
            "/api/v1/webhook/alertmanager",
            json=payload,
            name="POST /api/v1/webhook/alertmanager [ingest]",
            catch_response=True,
        ) as resp:
            if resp.status_code != 202:
                resp.failure(f"unexpected status {resp.status_code}")

    @task(2)
    @tag("query")
    def list_alerts(self):
        """告警列表查询"""
        self.client.get("/api/v1/alerts", name="GET /api/v1/alerts [query]")

    @task(1)
    @tag("query")
    def health(self):
        """健康检查（基线：框架空转开销）"""
        self.client.get("/health", name="GET /health [query]")
