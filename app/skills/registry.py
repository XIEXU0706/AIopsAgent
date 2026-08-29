"""AIOps Skill 注册表 —— 从 SKILL.md 动态加载技能

Skill 定义格式 (SKILL.md)：
  ```yaml
  name: mysql_fault_diagnosis
  description: MySQL 故障排查
  risk_level: medium
  triggers:
    - error_type: mysql_connection
    - error_type: mysql_slow_query
  steps:
    - type: check_connections
      params: {limit: 20}
    - type: analyze_slow_queries
    - type: suggest_remediation
      params: {detail: "优化慢查询索引"}
  ```
steps 的 type 会被 SkillExecutor 映射为真实诊断动作（只读安全，危险动作受安全门控制）。
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型提示，避免运行时依赖已移除的 executor 模块
    from app.skills.executor import SkillExecutor, StepResult

logger = logging.getLogger(__name__)


@dataclass
class SkillStep:
    """一个声明式处置步骤，可被 SkillExecutor 映射为真实动作。"""
    type: str = ""
    risk_level: str = "low"
    params: dict = field(default_factory=dict)


@dataclass
class AIOpsSkill:
    """一个可执行 Skill"""
    name: str = ""
    description: str = ""
    risk_level: str = "low"  # low | medium | high | critical
    triggers: list[str] = field(default_factory=list)  # 匹配的 error_type
    steps: list[SkillStep] = field(default_factory=list)
    source_file: str = ""

    def handover_summary(self, context: dict) -> str:
        """生成个案交接摘要：供高风险处置后人工接管时快速了解上下文

        包含：技能名称、风险等级、触发场景、待执行步骤、安全检查项。
        高风险场景额外标注「需人工复核」标记，提升可审计性与可测试性。
        """
        risk = self.risk_level
        ctx_desc = context.get("alert_context") or context.get("error_type") or "未提供上下文"
        step_lines = "\n".join(
            f"  {i+1}. {s.type}" for i, s in enumerate(self.steps)
        ) or "  (无预定义步骤)"
        audit_tag = "【需人工复核】" if risk in ("high", "critical") else ""
        return (
            f"处置技能: {self.name}\n"
            f"风险等级: {risk} {audit_tag}\n"
            f"触发场景: {ctx_desc}\n"
            f"处置步骤:\n{step_lines}\n"
            f"来源文件: {self.source_file or '运行时注册'}"
        )

    async def execute(
        self,
        context: dict,
        executor: Optional["SkillExecutor"] = None,
        safety_review: Optional[Callable[[str, str], Any]] = None,
    ) -> dict:
        """执行 Skill：返回结构化处置计划与交接摘要。

        当前 Skill 为声明式处置剧本，危险动作默认仅生成处置计划不自动执行；
        若上层传入 executor（真实动作执行层）则调用其执行只读诊断。
        """
        if executor is not None:
            results = await executor.execute(self, context)
            executed = [r for r in results if r.status == "executed"]
            blocked = [r for r in results if r.status == "blocked"]
            degraded = [r for r in results if r.status == "degraded"]
            return {
                "skill": self.name,
                "status": "executed" if executed else "plan_only",
                "risk_level": self.risk_level,
                "plan_steps": [s.type for s in self.steps],
                "execution": [r.__dict__ for r in results],
                "summary": {
                    "executed": len(executed),
                    "blocked": len(blocked),
                    "degraded": len(degraded),
                },
                "handover_summary": self.handover_summary(context),
            }

        # 未注入 executor：仅产出处置计划 + 交接摘要（默认行为）
        return {
            "skill": self.name,
            "status": "plan_only",
            "risk_level": self.risk_level,
            "plan_steps": [s.type for s in self.steps],
            "execution": [],
            "summary": {"executed": 0, "blocked": 0, "degraded": 0},
            "handover_summary": self.handover_summary(context),
        }


class AIOpsSkillRegistry:
    """Skill 注册表：扫描目录、动态加载、按告警匹配"""

    def __init__(self, scan_dir: Optional[str] = None):
        self.skills: dict[str, AIOpsSkill] = {}
        self._scan_dir = scan_dir or str(Path(__file__).parent / "definitions")
        self._load_all()

    def _load_all(self) -> None:
        """扫描目录下的所有 SKILL.md 文件"""
        scan_path = Path(self._scan_dir)
        if not scan_path.exists():
            scan_path.mkdir(parents=True, exist_ok=True)
            logger.info("Created skill definitions dir: %s", scan_path)

        for md_file in scan_path.glob("**/*.md"):
            skill = self.parse_skill(md_file)
            if skill:
                self.skills[skill.name] = skill
                logger.info("Loaded skill: %s (risk=%s, triggers=%s)",
                           skill.name, skill.risk_level, skill.triggers)

        logger.info("Total skills loaded: %d", len(self.skills))

    def parse_skill(self, path: Path) -> Optional[AIOpsSkill]:
        """解析 SKILL.md 文件"""
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to read %s: %s", path, e)
            return None

        skill = AIOpsSkill(source_file=str(path))

        for line in text.splitlines():
            line = line.strip()
            if m := re.match(r"^name:\s*(.+)", line):
                skill.name = m.group(1).strip()
            elif m := re.match(r"^description:\s*(.+)", line):
                skill.description = m.group(1).strip()
            elif m := re.match(r"^risk_level:\s*(.+)", line):
                skill.risk_level = m.group(1).strip()
            elif m := re.match(r"^\s*-\s*error_type:\s*(.+)", line):
                skill.triggers.append(m.group(1).strip())
            elif m := re.match(r"^\s*-\s*type:\s*(.+)", line):
                skill.steps.append({"type": m.group(1).strip()})

        if not skill.name:
            logger.warning("Skill in %s has no name, skipping", path)
            return None
        return skill

    def get_skill(self, name: str) -> Optional[AIOpsSkill]:
        return self.skills.get(name)

    def get_skills_for_alert(self, error_type: str) -> list[AIOpsSkill]:
        """根据告警 error_type 匹配 Skill"""
        matched = []
        for skill in self.skills.values():
            if error_type in skill.triggers or not skill.triggers:
                matched.append(skill)
        return matched

    def get_safety_plan(self, error_type: str) -> list[AIOpsSkill]:
        """高风险场景强制叠加安全处理计划

        返回高风险 Skill 列表，由调用方执行。每个 Skill 执行结果的
        `handover_summary` 字段提供个案交接摘要，便于人工接管与审计。
        """
        return [
            s for s in self.skills.values()
            if s.risk_level in ("high", "critical")
        ]

    def list_skills(self) -> list[AIOpsSkill]:
        return list(self.skills.values())

    def register(self, skill: AIOpsSkill) -> None:
        """手动注册一个 Skill"""
        self.skills[skill.name] = skill
