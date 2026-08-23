"""Alertmanager 告警源模拟器 —— 以 Prometheus Alertmanager v4 webhook 格式推送告警

用途：
  - 联调 / 演示：向 MindBridge 推送接近真实的告警流，验证
    webhook 接入 → 多 Agent 处置 → 安全护栏 → 报告生成全链路
  - 压测预热：--loop 模式持续发送，模拟生产告警流

用法：
    # 单发一条随机告警
    python scripts/mock_alertmanager.py

    # 指定类型发送（mysql / redis / disk / cpu / http）
    python scripts/mock_alertmanager.py --alert mysql

    # 发 20 条，间隔 0.5s
    python scripts/mock_alertmanager.py --count 20 --interval 0.5

    # 持续发送（Ctrl+C 停止）
    python scripts/mock_alertmanager.py --loop --interval 2
"""

import argparse
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

# 允许以脚本方式直接运行
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── 内置告警模板（字段对齐 Prometheus 常用规则） ──────────────

ALERT_TEMPLATES = {
    "mysql": {
        "alertname": "MySQLTooManyConnections",
        "labels": {
            "severity": "critical",
            "job": "mysql",
            "instance": "db-master-01:3306",
            "alertname": "MySQLTooManyConnections",
        },
        "annotations": {
            "summary": "MySQL 连接数达到 max_connections 上限",
            "description": (
                "max_connections=1000, threads_connected=998, "
                "新连接被拒绝 (ERROR 1040: Too many connections)"
            ),
        },
    },
    "redis": {
        "alertname": "RedisOOM",
        "labels": {
            "severity": "critical",
            "job": "redis",
            "instance": "cache-01:6379",
            "alertname": "RedisOOM",
        },
        "annotations": {
            "summary": "Redis 内存达到 maxmemory，写入被拒绝",
            "description": "used_memory=7.9GB maxmemory=8GB evicted_keys=0",
        },
    },
    "disk": {
        "alertname": "HostDiskAlmostFull",
        "labels": {
            "severity": "warning",
            "job": "node",
            "instance": "app-node-03:9100",
            "device": "/dev/sda1",
            "mountpoint": "/var/lib/docker",
            "alertname": "HostDiskAlmostFull",
        },
        "annotations": {
            "summary": "主机磁盘使用率超过 90%",
            "description": "disk usage 94%, 预计 6 小时内写满",
        },
    },
    "cpu": {
        "alertname": "HostHighCpuLoad",
        "labels": {
            "severity": "warning",
            "job": "node",
            "instance": "app-node-01:9100",
            "alertname": "HostHighCpuLoad",
        },
        "annotations": {
            "summary": "主机 CPU 使用率持续高于 90%",
            "description": "CPU usage 96% (5m avg), load1=32 (8 cores)",
        },
    },
    "http": {
        "alertname": "HTTPErrorRateHigh",
        "labels": {
            "severity": "critical",
            "job": "gateway",
            "service": "order-api",
            "alertname": "HTTPErrorRateHigh",
        },
        "annotations": {
            "summary": "订单服务 HTTP 5xx 比例超过 10%",
            "description": "error_rate=18.3% (5m), p99_latency=4.2s",
        },
    },
}


def build_payload(alert_type: str) -> dict:
    """构造一条 Alertmanager v4 webhook payload（fingerprint 随机，模拟新告警实例）"""
    tpl = ALERT_TEMPLATES[alert_type]
    now = datetime.now(timezone.utc)
    labels = dict(tpl["labels"])
    labels["alertname"] = tpl["alertname"]
    return {
        "version": "4",
        "groupKey": f"{tpl['labels']['job']}:{tpl['alertname']}",
        "status": "firing",
        "receiver": "mindbridge",
        "groupLabels": {"alertname": tpl["alertname"], "job": tpl["labels"]["job"]},
        "commonLabels": labels,
        "commonAnnotations": tpl["annotations"],
        "externalURL": "http://alertmanager:9093",
        "alerts": [
            {
                "status": "firing",
                "labels": labels,
                "annotations": tpl["annotations"],
                "startsAt": (now - timedelta(minutes=2)).isoformat(),
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090/graph",
                "fingerprint": uuid.uuid4().hex[:16],
            }
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Alertmanager 告警源模拟器")
    parser.add_argument("--url", default="http://127.0.0.1:9092/api/v1/webhook/alertmanager",
                        help="MindBridge webhook 地址")
    parser.add_argument("--alert", choices=list(ALERT_TEMPLATES), default=None,
                        help="指定告警类型（默认随机）")
    parser.add_argument("--count", type=int, default=1, help="发送条数（--loop 时忽略）")
    parser.add_argument("--interval", type=float, default=1.0, help="发送间隔秒数")
    parser.add_argument("--loop", action="store_true", help="持续发送直到 Ctrl+C")
    args = parser.parse_args()

    client = httpx.Client(timeout=10)
    types = list(ALERT_TEMPLATES)
    sent = 0

    print(f"目标: {args.url}")
    try:
        while True:
            alert_type = args.alert or random.choice(types)
            payload = build_payload(alert_type)
            try:
                resp = client.post(args.url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                sent += 1
                print(f"[{sent:>4}] {alert_type:<6} {payload['alerts'][0]['labels']['alertname']}"
                      f" -> accepted={data.get('accepted')}")
            except httpx.HTTPError as e:
                print(f"[FAIL] {alert_type}: {e}")
            except Exception as e:
                print(f"[FAIL] {alert_type}: {e}")

            if args.loop:
                time.sleep(args.interval)
                continue
            if sent >= args.count:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n停止，共发送 {sent} 条告警")


if __name__ == "__main__":
    main()
