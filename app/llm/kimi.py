"""Kimi (Moonshot) LLM 客户端 —— 复用 OpenAI 兼容协议

Moonshot 的 /v1/chat/completions 与 OpenAI 协议一致，
因此直接继承 DeepSeekClient，仅覆盖 base_url / model / api_key。

kimi-k2 系列为推理模型：
  - temperature 固定为 1
  - 流式响应先输出 reasoning_content（内部推理），再输出 content（最终回答）
"""


import json
import logging
from typing import Optional

from app.config import settings
from app.llm.deepseek import DeepSeekClient, LLMConfig

logger = logging.getLogger(__name__)


class KimiClient(DeepSeekClient):
    """Kimi (Moonshot) API 客户端（流式 + 非流式）"""

    def __init__(self, config: Optional[LLMConfig] = None):
        if config is None:
            config = LLMConfig(
                api_key=settings.kimi_api_key,
                base_url=settings.kimi_base_url,
                model=settings.kimi_chat_model,
                temperature=1.0,   # kimi-k2 推理模型仅允许 temperature=1
                max_tokens=4096,   # 推理会消耗 token，调高避免推理未完被截断
            )
        super().__init__(config)

    async def chat_stream(self, messages: list[dict], **override):
        """流式对话：仅输出正式回答 content，隐藏推理过程（reasoning_content）

        推理阶段前端无数据，由 ChatPanel 显示「正在思考中…」。
        """
        payload = self._build_payload(messages, stream=True, **override)
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    chunk = line[6:]
                    if chunk == "[DONE]":
                        break
                    try:
                        data = json.loads(chunk)
                        delta = data["choices"][0]["delta"]
                        content = delta.get("content") or ""
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error("Kimi stream failed: %s", e)
            raise
