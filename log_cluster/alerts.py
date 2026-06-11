"""告警规则引擎 - 评估规则并生成告警"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List

from .config import AlertRule, AppConfig


# 允许在condition表达式中使用的安全变量白名单
_SAFE_VARS = {
    "new_template_count",
    "error_template_count",
    "spike_count",
    "total_templates",
    "processing_speed",
}

# 安全内建函数白名单
_SAFE_BUILTINS = {
    "abs": abs,
    "min": min,
    "max": max,
    "len": len,
    "int": int,
    "float": float,
    "bool": bool,
    "str": str,
    "round": round,
}


@dataclass
class TriggeredAlert:
    """触发的告警"""
    name: str
    severity: str
    message: str
    condition: str
    triggered_at: datetime = field(default_factory=datetime.now)
    variables: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "severity": self.severity,
            "message": self.message,
            "condition": self.condition,
            "triggered_at": self.triggered_at.isoformat(),
            "variables": self.variables,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TriggeredAlert":
        return cls(
            name=data["name"],
            severity=data["severity"],
            message=data["message"],
            condition=data.get("condition", ""),
            triggered_at=datetime.fromisoformat(data["triggered_at"]) if data.get("triggered_at") else datetime.now(),
            variables=data.get("variables", {}),
        )


@dataclass
class AlertContext:
    """告警评估上下文变量"""
    new_template_count: int = 0
    error_template_count: int = 0
    spike_count: int = 0
    total_templates: int = 0
    processing_speed: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "new_template_count": self.new_template_count,
            "error_template_count": self.error_template_count,
            "spike_count": self.spike_count,
            "total_templates": self.total_templates,
            "processing_speed": self.processing_speed,
        }


_SEVERITY_ORDER = {"critical": 3, "warning": 2, "info": 1}


def severity_order(severity: str) -> int:
    """获取告警级别排序值，数值越大越严重"""
    return _SEVERITY_ORDER.get(severity.lower(), 0)


class AlertEngine:
    """告警规则引擎"""

    def __init__(self, config: AppConfig):
        self.config = config
        self.rules: List[AlertRule] = list(config.alerts)

    def _validate_condition(self, condition: str) -> None:
        """校验条件表达式中的变量是否在白名单内"""
        # 提取标识符
        identifiers = set(re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", condition))
        # 过滤掉常见的Python关键字和安全内建函数
        keywords = {
            "and", "or", "not", "in", "is", "True", "False", "None",
            "if", "else", "elif", "for", "while", "break", "continue",
            "return", "def", "class", "import", "from", "as", "with",
            "try", "except", "finally", "raise", "pass", "lambda",
            "yield", "global", "nonlocal", "assert", "del",
        }
        unsafe = (
            identifiers
            - keywords
            - _SAFE_VARS
            - set(_SAFE_BUILTINS.keys())
        )
        if unsafe:
            raise ValueError(
                f"告警条件中包含未授权的变量/标识符: {', '.join(sorted(unsafe))}"
            )

    def _evaluate_condition(self, condition: str, context: AlertContext) -> bool:
        """安全地评估条件表达式
        
        Args:
            condition: 条件表达式字符串
            context: 上下文变量
        
        Returns:
            表达式布尔结果
        """
        self._validate_condition(condition)
        var_dict = context.to_dict()

        # 构建安全执行环境
        safe_globals = {"__builtins__": {}}
        safe_globals.update(_SAFE_BUILTINS)

        try:
            result = eval(condition, safe_globals, var_dict)
            return bool(result)
        except (NameError, TypeError, SyntaxError, ZeroDivisionError):
            return False

    def _interpolate_message(self, message: str, context: AlertContext) -> str:
        """对消息模板进行变量插值
        
        支持 {var} 和 {var:.2f} 这样的格式化语法
        """
        var_dict = context.to_dict()
        try:
            return message.format(**var_dict)
        except (KeyError, IndexError, ValueError):
            # 降级为简单替换
            result = message
            for key, value in var_dict.items():
                result = result.replace(f"{{{key}}}", str(value))
            return result

    def evaluate(self, context: AlertContext) -> List[TriggeredAlert]:
        """评估所有规则，返回触发的告警列表（按严重程度降序）"""
        triggered: List[TriggeredAlert] = []

        for rule in self.rules:
            try:
                if self._evaluate_condition(rule.condition, context):
                    message = self._interpolate_message(rule.message, context)
                    triggered.append(TriggeredAlert(
                        name=rule.name,
                        severity=rule.severity.lower(),
                        message=message,
                        condition=rule.condition,
                        variables=context.to_dict(),
                    ))
            except ValueError:
                # 条件包含非法变量，跳过
                continue

        # 按严重程度降序排列
        triggered.sort(
            key=lambda a: severity_order(a.severity),
            reverse=True,
        )
        return triggered
