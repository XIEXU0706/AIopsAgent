"""Agent 基类"""


import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

from app.runtime.agent_runtime import AgentResult
from app.runtime.context import ExecutionContext

if TYPE_CHECKING:
    from app.blackboard.blackboard import CollaborationBlackboard

logger = logging.getLogger(__name__)


class BaseMindAgent(ABC):
    """所有 Agent 的基类

    子类只需实现:
      - name: Agent 唯一标识
      - description: 能力描述
      - claim_types: 能认领的黑板任务类型列表
      - run(): 核心业务逻辑

    可选实现:
      - on_claim(): 在黑板认领任务后的回调
    """

    name: str = ""
    description: str = ""
    claim_types: list[str] = []

    def __init__(
        self,
        context: ExecutionContext,
        blackboard: Optional["CollaborationBlackboard"] = None,
    ):
        self.context = context
        self.blackboard = blackboard

    @abstractmethod
    async def run(self, input_data: dict[str, Any]) -> AgentResult:
        """执行 Agent 核心逻辑"""
        ...

    async def on_claim(self, task_id: str) -> None:
        """认领黑板任务后的钩子（子类可选重写）"""
        pass
