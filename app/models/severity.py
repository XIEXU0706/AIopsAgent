"""告警级别枚举与规范化

对外只暴露三种级别：critical / warning / info。
所有来源（外部告警、LLM、规则引擎）的级别值都统一归一到这里。
"""

SEVERITY_LEVELS = ("critical", "warning", "info")
SEVERITY_ZH = {"critical": "严重", "warning": "警告", "info": "提示"}

_SEVERITY_MAP = {
    "critical": "critical", "fatal": "critical", "emergency": "critical",
    "severe": "critical", "error": "critical", "alert": "critical", "high": "critical",
    "warning": "warning", "warn": "warning", "medium": "warning", "moderate": "warning",
    "info": "info", "information": "info", "notice": "info",
    "low": "info", "ok": "info", "okay": "info", "normal": "info", "debug": "info",
}

_WORDS_CRITICAL = ("critical", "fatal", "emergency", "severe", "error", "disaster")
_WORDS_WARNING = ("warn", "medium", "moderate", "attention")
_WORDS_INFO = ("info", "notice", "low", "ok", "normal", "debug", "recovered", "recovery")


def normalize_severity(value) -> str:
    """将任意来源的严重级别归一为 critical / warning / info 三值"""
    if not value:
        return "warning"
    key = str(value).strip().lower()
    if key in _SEVERITY_MAP:
        return _SEVERITY_MAP[key]
    if any(w in key for w in _WORDS_CRITICAL):
        return "critical"
    if any(w in key for w in _WORDS_WARNING):
        return "warning"
    if any(w in key for w in _WORDS_INFO):
        return "info"
    return "warning"
