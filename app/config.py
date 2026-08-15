"""全局配置"""

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    # LLM
    deepseek_api_key: str = field(
        default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", "")
    )
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_chat_model: str = "deepseek-v4-flash"
    deepseek_reasoner_model: str = "deepseek-v4-pro"

    # Kimi (Moonshot) —— 智能对话使用
    kimi_api_key: str = field(
        default_factory=lambda: os.getenv("KIMI_API_KEY", "")
    )
    kimi_base_url: str = "https://api.moonshot.cn/v1"
    kimi_chat_model: str = field(
        default_factory=lambda: os.getenv("KIMI_CHAT_MODEL", "kimi-k2.6")
    )

    # Server
    host: str = "127.0.0.1"
    port: int = 9092

    # 记忆
    memory_max_tokens: int = 4000
    memory_recent_count: int = 5
    redis_ttl_seconds: int = 3600
    # 长期记忆后端：mysql 或 sqlite（MySQL 不可用时自动降级回 sqlite）
    long_term_backend: str = field(
        default_factory=lambda: os.getenv("LONG_TERM_BACKEND", "mysql")
    )
    mysql_dsn: str = field(
        default_factory=lambda: os.getenv(
            "MYSQL_DSN", "mysql://root:123456@localhost:3306/aiopsAgent"
        )
    )
    # 会话归档保留天数：>7时把超过该天数的历史会话移入 chat_kv_archive，0=不归档
    chat_archive_days: int = field(
        default_factory=lambda: int(os.getenv("CHAT_ARCHIVE_DAYS", "7"))
    )

    # 工具队列
    tool_queue_rate: int = 10  # 每秒
    tool_queue_burst: int = 20
    tool_max_retries: int = 3

    # MCP 工具
    # 报告/Excel 导出落盘目录
    reports_dir: str = str(BASE_DIR / "data" / "reports")
    # 预警发送 Webhook：非空时真实 POST 到该地址，否则仅存库
    notification_webhook_url: str = field(
        default_factory=lambda: os.getenv("NOTIFICATION_WEBHOOK_URL", "")
    )


settings = Settings()
