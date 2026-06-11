"""模板标签系统 - 正则匹配打标签、层级标签、标签过滤"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

from .config import AppConfig, TagRule
from .drain import LogTemplate


@dataclass
class TagMatchResult:
    """标签匹配结果"""
    matched_tags: List[str] = field(default_factory=list)
    matched_rules: List[str] = field(default_factory=list)


class TagEngine:
    """标签引擎"""

    def __init__(self, config: AppConfig):
        self.config = config
        self.tag_rules: List[TagRule] = list(config.tags)
        # 预编译正则
        self._compiled_patterns: List[re.Pattern] = [
            re.compile(rule.pattern)
            for rule in self.tag_rules
        ]

    def match_tags(self, template: LogTemplate) -> TagMatchResult:
        """对单个模板匹配标签"""
        result = TagMatchResult()
        template_str = template.template_str

        for i, pattern in enumerate(self._compiled_patterns):
            if pattern.search(template_str):
                rule = self.tag_rules[i]
                for tag in rule.tags:
                    if tag not in result.matched_tags:
                        result.matched_tags.append(tag)
                result.matched_rules.append(rule.pattern)

        return result

    def apply_tags(self, templates: Iterable[LogTemplate]) -> None:
        """对模板列表批量打标签（原地修改tags字段）"""
        for template in templates:
            match_result = self.match_tags(template)
            if match_result.matched_tags:
                existing = set(template.tags)
                for tag in match_result.matched_tags:
                    if tag not in existing:
                        template.tags.append(tag)

    @staticmethod
    def expand_hierarchical_tags(tags: Iterable[str]) -> Set[str]:
        """展开层级标签，上级包含下级"""
        expanded: Set[str] = set()
        for tag in tags:
            parts = tag.split("/")
            for i in range(1, len(parts) + 1):
                expanded.add("/".join(parts[:i]))
            expanded.add(tag)
        return expanded

    @staticmethod
    def filter_by_tag(
        templates: Iterable[LogTemplate],
        required_tag: str,
        use_hierarchy: bool = True,
    ) -> List[LogTemplate]:
        """按标签过滤模板"""
        result: List[LogTemplate] = []
        for template in templates:
            if use_hierarchy:
                template_hier = TagEngine.expand_hierarchical_tags(template.tags)
                if required_tag in template_hier:
                    result.append(template)
            else:
                if required_tag in template.tags:
                    result.append(template)
        return result

    @staticmethod
    def aggregate_tag_counts(
        templates: Iterable[LogTemplate],
        use_hierarchy: bool = True,
    ) -> Dict[str, int]:
        """按标签聚合计数"""
        counts: Dict[str, int] = defaultdict(int)

        for template in templates:
            if use_hierarchy:
                tags = TagEngine.expand_hierarchical_tags(template.tags)
            else:
                tags = set(template.tags)
            for tag in tags:
                counts[tag] += 1

        return dict(counts)

    @staticmethod
    def format_tag_badges(tags: Iterable[str], max_len: Optional[int] = None) -> str:
        """格式化标签为徽章字符串（用于终端显示）"""
        tag_list = list(tags)
        if max_len is not None:
            tag_list = tag_list[:max_len]
        return " ".join(f"[{tag}]" for tag in tag_list)
