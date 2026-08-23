from app.api.alert_routes import router as alert_router
from app.api.chat_routes import router as chat_router
from app.api.knowledge_routes import router as knowledge_router
from app.api.notification_routes import router as notification_router
from app.api.webhook_routes import router as webhook_router

__all__ = [
    "alert_router",
    "chat_router",
    "knowledge_router",
    "notification_router",
    "webhook_router",
]
