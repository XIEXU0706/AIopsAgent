"""AIOps 智能运维告警处理平台入口"""

import logging
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

# 加载 .env 文件（优先加载项目根目录下的 .env）
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    alert_router,
    chat_router,
    knowledge_router,
    notification_router,
    webhook_router,
)
from app.api.alert_routes import init_harness
from app.config import settings
from app.harness import AIOpsAgentHarness

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="AIOps",
        description="多Agent:运维告警处理平台",
        version="1.0.0",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 初始化 Harness（核心引擎）
    harness = AIOpsAgentHarness()
    init_harness(harness)

    # 注册路由
    app.include_router(alert_router)
    app.include_router(chat_router)
    app.include_router(knowledge_router)
    app.include_router(notification_router)
    app.include_router(webhook_router)

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "agents": harness.runtime.get_registered_agents(),
        }

    @app.get("/")
    async def root():
        return {
            "service": "AIOps",
            "version": "1.0.0",
            "docs": "/docs",
            "endpoints": {
                "ingest_alert": "POST /api/v1/alerts",
                "event_stream": "GET /api/v1/alerts/{trace_id}/events",
                "get_report": "GET /api/v1/alerts/{trace_id}/report",
                "chat": "POST /api/v1/chat/ask",
                "sessions": "GET /api/v1/sessions",
            },
        }

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
