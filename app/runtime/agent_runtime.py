"""Agent Runtime —— Agent 注册与执行容器"""

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from app.runtime.context import ExecutionContext

if TYPE_CHECKING:
    from app.agents.base_agent import BaseMindAgent
    from app.blackboard.blackboard import CollaborationBlackboard

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Agent 执行结果"""
    output: dict = field(default_factory=dict)
    intercepted: bool = False
    error: Optional[str] = None


class AgentRuntime:
    """Agent 运行时

    职责：
      - 注册 Agent 类型
      - 按名称创建并执行 Agent
      - 管理 Agent 与黑板的连接
    """

    def __init__(self, blackboard: "CollaborationBlackboard"):
        self.blackboard = blackboard
        self.registry: dict[str, type["BaseMindAgent"]] = {}

    def register_agent(self, agent_class: type["BaseMindAgent"]) -> None:
        """注册一个 Agent 类型"""
        name = agent_class.name
        self.registry[name] = agent_class
        logger.info("Agent registered: %s (%s)", name, agent_class.__name__)

    def get_registered_agents(self) -> list[str]:
        """
        获取已注册的代理列表
        返回:
            list[str]: 包含所有已注册代理标识符的列表
        """
        return list(self.registry.keys())

    async def run(
        self,
        agent_name: str,
        ctx: ExecutionContext,
        input_data: dict[str, Any],
    ) -> AgentResult:
        """运行指定的 Agent

        Args:
            agent_name: 注册时的名称
            ctx: 执行上下文
            input_data: 输入数据

        Returns:
            AgentResult
        """
        agent_cls = self.registry.get(agent_name)
        if agent_cls is None:
            raise KeyError(
                f"Agent '{agent_name}' not registered. "
                f"Registered: {list(self.registry.keys())}"
            )

        agent: "BaseMindAgent" = agent_cls(
            context=ctx,
            blackboard=self.blackboard,
        )

        start = time.time()
        try:
            result = await agent.run(input_data)
            duration = int((time.time() - start) * 1000)
            logger.info(
                "Agent %s completed in %dms (intercepted=%s)",
                agent_name,
                duration,
                result.intercepted,
            )
            return result
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            logger.exception("Agent %s failed after %dms", agent_name, duration)
            return AgentResult(output={}, error=str(e))
