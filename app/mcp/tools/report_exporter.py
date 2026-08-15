"""报告导出工具：将处置报告真实落盘为 JSON / Markdown"""

import json
import logging
from datetime import datetime
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def _reports_dir() -> Path:
    path = Path(settings.reports_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


async def export_report(params: dict) -> dict:
    """导出处置报告并写入 data/reports/ 目录"""
    fmt = params.get("format", "json")
    report_data = params.get("data", {})
    trace_id = report_data.get("trace_id", "unknown")

    if fmt == "markdown":
        lines = [
            f"# 处置报告",
            f"",
            f"**Trace ID**: {trace_id}",
            f"**告警**: {report_data.get('alert_title', '')}",
            f"**时间**: {datetime.now().isoformat()}",
            f"",
            f"## 分析结论",
            f"{report_data.get('summary', '')}",
            f"",
            f"## 处置计划",
            f"{report_data.get('disposition_plan', '')}",
            f"",
            f"---",
            f"*由 AIOps 自动生成*",
        ]
        result = "\n".join(lines)
        filename = f"report_{trace_id}.md"
    else:
        result = json.dumps(report_data, ensure_ascii=False, indent=2)
        filename = f"report_{trace_id}.json"

    path = _reports_dir() / filename
    path.write_text(result, encoding="utf-8")
    logger.info("[EXPORT] saved %s (%d bytes)", path, len(result.encode("utf-8")))

    return {
        "format": "json" if fmt != "markdown" else "markdown",
        "filename": filename,
        "path": str(path),
        "saved": True,
    }
