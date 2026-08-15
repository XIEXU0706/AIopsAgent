"""DeepSeek LLM 客户端（OpenAI 兼容接口）"""


import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    api_key: str = settings.deepseek_api_key
    base_url: str = settings.deepseek_base_url
    model: str = settings.deepseek_chat_model
    timeout: int = 60
    max_tokens: int = 2048
    temperature: float = 0.1


class DeepSeekClient:
    """DeepSeek API 客户端（流式 + 非流式）"""

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or LLMConfig()
        self._client = httpx.AsyncClient(
            base_url=self.config.base_url,
            timeout=self.config.timeout,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def chat(
        self,
        messages: list[dict],
        **override,
    ) -> str:
        """非流式对话，返回完整文本"""
        payload = self._build_payload(messages, **override)
        try:
            resp = await self._client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error("DeepSeek chat failed: %s", e)
            raise

    async def chat_stream(
        self,
        messages: list[dict],
        **override,
    ):
        """流式对话，逐 chunk 产出文本

        使用 aiter_text 手动按行切分，避免 httpx aiter_lines 的行缓冲造成
        “攒一批才吐出” 的延迟，确保逐 token 实时推送。
        """
        payload = self._build_payload(messages, stream=True, **override)
        buffer = ""
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as resp:
                resp.raise_for_status()
                async for text in resp.aiter_text():
                    buffer += text
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line.startswith("data: "):
                            continue
                        chunk = line[6:]
                        if chunk == "[DONE]":
                            return
                        try:
                            data = json.loads(chunk)
                            delta = data["choices"][0]["delta"].get("content", "")
                            if delta:
                                yield delta
                        except json.JSONDecodeError:
                            continue
                # 处理结尾残留（极少数无换行结尾的情况）
                tail = buffer.strip()
                if tail.startswith("data: ") and tail[6:] != "[DONE]":
                    try:
                        data = json.loads(tail[6:])
                        delta = data["choices"][0]["delta"].get("content", "")
                        if delta:
                            yield delta
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            logger.error("DeepSeek stream failed: %s", e)
            raise

    def _build_payload(self, messages: list[dict], **override) -> dict:
        payload = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
        }
        payload.update(override)
        return payload

    async def aclose(self):
        await self._client.aclose()


PROMPTS = {
    "log_analysis": 
        """
            你是一个运维日志分析专家。分析以下告警消息，返回 JSON 格式的分析结果：
            {{
                "error_type": "错误类型",
                "root_cause": "根因分析",
                "analysis": "详细分析说明",
                "severity_level": "high/medium/low"
            }}

            告警消息：{message}
        """,

    "disposition_suggestion": 
        """
        基于以下告警信息和日志分析结果，给出处置建议（JSON 格式）：
            {{
            "disposition_plan": "处置步骤，分点列出",
            "priority": "P0/P1/P2",
            "estimated_impact": "影响范围评估",
            "needs_manual_review": true/false
            }}

            告警信息：
            {alert_info}

            日志分析结果：
            {log_analysis}
        """,
}
