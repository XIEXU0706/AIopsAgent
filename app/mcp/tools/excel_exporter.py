"""Excel 报告导出工具：使用 openpyxl 生成真正的 .xlsx 文件"""

import logging
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from app.config import settings

logger = logging.getLogger(__name__)


def _reports_dir() -> Path:
    path = Path(settings.reports_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


async def excel_export(params: dict) -> dict:
    """导出处置报告为 .xlsx 文件（真实落盘）"""
    data = params.get("data", {})
    trace_id = data.get("trace_id", "unknown")

    wb = Workbook()
    ws = wb.active
    ws.title = "处置报告"
    ws.append(["TraceID", "AlertID", "状态", "耗时(ms)", "是否拦截", "时间"])
    ws.append([
        trace_id,
        data.get("alert_id", ""),
        data.get("status", ""),
        data.get("duration_ms", ""),
        "是" if data.get("has_safety_intercept") else "否",
        datetime.now().isoformat(),
    ])

    filename = f"report_{trace_id}.xlsx"
    path = _reports_dir() / filename
    wb.save(path)
    logger.info("[EXCEL] saved %s", path)

    return {
        "format": "xlsx",
        "filename": filename,
        "path": str(path),
        "saved": True,
    }
