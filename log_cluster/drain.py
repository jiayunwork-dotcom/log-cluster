"""Drain算法 - 日志模板提取核心模块"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .config import DrainConfig, PreprocessConfig
from .parser import LogEntry


EVENT_TYPE_CREATED = "created"
EVENT_TYPE_MERGED = "merged"
EVENT_TYPE_COMPRESSED = "compressed"
EVENT_TYPE_ACTIVE = "active"


@dataclass(slots=True)
class TemplateEvent:
    """模板生命周期事件"""
    timestamp: datetime
    event_type: str
    template_id: str
    related_template_id: str = ""
    details: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type,
            "template_id": self.template_id,
            "related_template_id": self.related_template_id,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TemplateEvent":
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            event_type=data["event_type"],
            template_id=data["template_id"],
            related_template_id=data.get("related_template_id", ""),
            details=data.get("details", ""),
        )


@dataclass(slots=True)
class TemplateStats:
    """模板统计信息"""
    count: int = 0
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    level_counts: Dict[str, int] = field(default_factory=dict)
    sources: List[str] = field(default_factory=list)
    
    def update(self, entry: LogEntry):
        """更新统计信息 - 优化版本"""
        self.count += 1
        ts = entry.timestamp
        if ts is not None:
            # 99%情况：时间递增，只需要比较last_seen
            ls = self.last_seen
            if ls is None or ts > ls:
                self.last_seen = ts
                fs = self.first_seen
                if fs is None or ts < fs:
                    self.first_seen = ts
            else:
                # 偶尔需要检查first_seen
                fs = self.first_seen
                if fs is None or ts < fs:
                    self.first_seen = ts
        
        level = entry.normalized_level
        lc = self.level_counts
        lc[level] = lc.get(level, 0) + 1
        
        src = entry.source
        if src:
            sources = self.sources
            # 快速检查：sources通常很小，用not in也很快
            if src not in sources:
                sources.append(src)
                if len(sources) > 100:
                    del sources[100:]


@dataclass(slots=True)
class LogTemplate:
    """日志模板"""
    template_id: str
    template_str: str
    tokens: List[str]
    stats: TemplateStats = field(default_factory=TemplateStats)
    is_new: bool = False
    is_error: bool = False
    is_periodic: bool = False
    is_spike: bool = False
    is_vanished: bool = False
    is_rare: bool = False
    cluster_id: str = ""
    tags: List[str] = field(default_factory=list)
    created_at: Optional[datetime] = None
    merged_at: Optional[datetime] = None
    compressed_at: Optional[datetime] = None
    
    def __post_init__(self):
        if not self.template_str:
            self.template_str = " ".join(self.tokens)
    
    def active_duration_seconds(self, now: Optional[datetime] = None) -> float:
        """获取模板活跃时长（秒）"""
        end_time = self.merged_at or self.compressed_at or now or datetime.now()
        start_time = self.created_at or self.stats.first_seen
        if start_time is None:
            return 0.0
        return max(0.0, (end_time - start_time).total_seconds())
    
    def to_dict(self) -> dict:
        return {
            "template_id": self.template_id,
            "template_str": self.template_str,
            "tokens": self.tokens,
            "stats": {
                "count": self.stats.count,
                "first_seen": self.stats.first_seen.isoformat() if self.stats.first_seen else None,
                "last_seen": self.stats.last_seen.isoformat() if self.stats.last_seen else None,
                "level_counts": self.stats.level_counts,
                "sources": self.stats.sources,
            },
            "is_new": self.is_new,
            "is_error": self.is_error,
            "is_periodic": self.is_periodic,
            "is_spike": self.is_spike,
            "is_vanished": self.is_vanished,
            "is_rare": self.is_rare,
            "cluster_id": self.cluster_id,
            "tags": self.tags,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "merged_at": self.merged_at.isoformat() if self.merged_at else None,
            "compressed_at": self.compressed_at.isoformat() if self.compressed_at else None,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "LogTemplate":
        stats_data = data.get("stats", {})
        stats = TemplateStats(
            count=stats_data.get("count", 0),
            first_seen=datetime.fromisoformat(stats_data["first_seen"]) if stats_data.get("first_seen") else None,
            last_seen=datetime.fromisoformat(stats_data["last_seen"]) if stats_data.get("last_seen") else None,
            level_counts=stats_data.get("level_counts", {}),
            sources=stats_data.get("sources", []),
        )
        return cls(
            template_id=data["template_id"],
            template_str=data["template_str"],
            tokens=data["tokens"],
            stats=stats,
            is_new=data.get("is_new", False),
            is_error=data.get("is_error", False),
            is_periodic=data.get("is_periodic", False),
            is_spike=data.get("is_spike", False),
            is_vanished=data.get("is_vanished", False),
            is_rare=data.get("is_rare", False),
            cluster_id=data.get("cluster_id", ""),
            tags=data.get("tags", []),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
            merged_at=datetime.fromisoformat(data["merged_at"]) if data.get("merged_at") else None,
            compressed_at=datetime.fromisoformat(data["compressed_at"]) if data.get("compressed_at") else None,
        )


class DrainNode:
    """Drain前缀树节点"""
    
    __slots__ = ('depth', 'children', 'templates')
    
    def __init__(self, depth: int = 0):
        self.depth = depth
        self.children: Dict[str, "DrainNode"] = {}
        self.templates: List[LogTemplate] = []
    
    def to_dict(self) -> dict:
        return {
            "depth": self.depth,
            "children": {k: v.to_dict() for k, v in self.children.items()},
            "templates": [t.to_dict() for t in self.templates],
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "DrainNode":
        node = cls(depth=data["depth"])
        node.children = {k: cls.from_dict(v) for k, v in data.get("children", {}).items()}
        node.templates = [LogTemplate.from_dict(t) for t in data.get("templates", [])]
        return node


class LogPreprocessor:
    """日志预处理器 - 替换变量部分 - 优化版本"""
    
    __slots__ = (
        'config', '_compiled_patterns', '_order', '_placeholders',
        '_has_ip_pattern', '_has_num_pattern', '_has_uuid_pattern',
        '_has_path_pattern', '_has_email_pattern',
    )
    
    def __init__(self, config: PreprocessConfig):
        self.config = config
        self._compiled_patterns: Dict[str, re.Pattern] = {}
        self._order = config.order
        self._placeholders: Dict[str, str] = {}
        
        # 快速标志位，避免不必要的正则调用
        self._has_ip_pattern = False
        self._has_num_pattern = False
        self._has_uuid_pattern = False
        self._has_path_pattern = False
        self._has_email_pattern = False
        
        for pattern in config.patterns:
            try:
                compiled = re.compile(pattern.regex)
                self._compiled_patterns[pattern.name] = compiled
                self._placeholders[pattern.name] = f"<{pattern.name}>"
                lower_name = pattern.name.lower()
                if 'ip' in lower_name:
                    self._has_ip_pattern = True
                elif 'num' in lower_name or 'number' in lower_name:
                    self._has_num_pattern = True
                elif 'uuid' in lower_name:
                    self._has_uuid_pattern = True
                elif 'path' in lower_name:
                    self._has_path_pattern = True
                elif 'email' in lower_name or 'mail' in lower_name:
                    self._has_email_pattern = True
            except re.error:
                pass
    
    # 数字快速检查的字符集
    _DIGIT_CHARS = set("0123456789")
    _DOT = '.'
    _SLASH = '/'
    _BACKSLASH = '\\'
    _AT = '@'
    _DASH = '-'
    
    @staticmethod
    def _has_digit_fast(s: str) -> bool:
        """快速检查字符串中是否有数字"""
        for c in s:
            if '0' <= c <= '9':
                return True
        return False
    
    @staticmethod
    def _has_digit_prefix_fast(s: str, max_len: int = 30) -> bool:
        """快速检查前缀是否包含数字"""
        n = len(s) if len(s) < max_len else max_len
        for i in range(n):
            c = s[i]
            if '0' <= c <= '9':
                return True
        return False
    
    def preprocess(self, message: str) -> str:
        """预处理消息，替换变量部分 - 高性能版本"""
        result = message
        
        for name in self._order:
            pattern = self._compiled_patterns.get(name)
            if pattern is None:
                continue
            
            placeholder = self._placeholders[name]
            
            # 基于模式类型快速检查（避免不必要的正则调用）
            if name == "IP":
                if self._DOT not in result or not self._has_digit_prefix_fast(result):
                    continue
            elif name == "NUM":
                if not self._has_digit_fast(result):
                    continue
            elif name == "UUID":
                if self._DASH not in result:
                    continue
            elif name == "PATH":
                if self._SLASH not in result and self._BACKSLASH not in result:
                    continue
            elif name == "EMAIL":
                if self._AT not in result:
                    continue
            
            result = pattern.sub(placeholder, result)
        
        return result
    
    def tokenize(self, message: str) -> List[str]:
        """将消息分词"""
        preprocessed = self.preprocess(message)
        # 使用默认split()比split(' ')快且处理连续空格
        tokens = preprocessed.split()
        return tokens


class Drain:
    """Drain算法实现"""
    
    __slots__ = (
        'config', 'preprocessor', 'root', 'templates',
        '_template_counter', '_sim_th', '_depth_minus_2',
        '_tok', '_match_fn', '_create_fn', '_add_fn',
        '_ERROR', '_FATAL',
        'events',
    )
    
    def __init__(self, config: DrainConfig, preprocessor: LogPreprocessor):
        self.config = config
        self.preprocessor = preprocessor
        self.root = DrainNode(depth=0)
        self.templates: Dict[str, LogTemplate] = {}
        self._template_counter = 0
        # 缓存常用配置
        self._sim_th = config.sim_th
        self._depth_minus_2 = config.depth - 2
        # 预绑定方法引用，减少属性访问开销
        self._tok = preprocessor.tokenize
        self._match_fn = self._match
        self._create_fn = self._create_template
        self._add_fn = self._add_template_to_tree
        self._ERROR = "ERROR"
        self._FATAL = "FATAL"
        # 生命周期事件列表
        self.events: List[TemplateEvent] = []
    
    def add_log_message(self, entry: LogEntry) -> LogTemplate:
        """添加一条日志，返回匹配的模板 - 高性能版本"""
        tokens = self._tok(entry.message)
        if not tokens:
            tokens = ["<EMPTY>"]
        
        template = self._match_fn(tokens)
        
        if template is None:
            template = self._create_fn(tokens)
            self._add_fn(template)
        
        template.stats.update(entry)
        
        level = entry.normalized_level
        if level == self._ERROR or level == self._FATAL:
            template.is_error = True
        
        return template
    
    def _match(self, tokens: List[str]) -> Optional[LogTemplate]:
        """在前缀树中匹配模板 - 优化版本"""
        token_count = len(tokens)
        count_key = str(token_count)
        
        root_children = self.root.children
        if count_key not in root_children:
            return None
        
        current_node = root_children[count_key]
        
        # 逐层匹配 - 使用缓存的 depth-2 值
        max_depth = token_count if token_count < self._depth_minus_2 else self._depth_minus_2
        
        for i in range(max_depth):
            token = tokens[i]
            node_children = current_node.children
            if token in node_children:
                current_node = node_children[token]
            else:
                wild = node_children.get("<*>")
                if wild is not None:
                    current_node = wild
                else:
                    break
        
        # 在叶子节点的模板列表中查找最匹配的
        templates = current_node.templates
        if not templates:
            return None
        
        best_template = None
        best_sim = -1.0
        sim_th = self._sim_th
        
        for template in templates:
            sim = self._calculate_similarity(tokens, template.tokens)
            if sim >= sim_th and sim > best_sim:
                best_sim = sim
                best_template = template
                # 优化：如果相似度100%就不用再比较了
                if best_sim >= 1.0:
                    break
        
        return best_template
    
    def _calculate_similarity(self, tokens1: List[str], tokens2: List[str]) -> float:
        """计算两个token序列的相似度 - 优化版本"""
        n = len(tokens1)
        if n != len(tokens2):
            return 0.0
        
        if n == 0:
            return 1.0
        
        same_count = 0
        # 提前终止：如果已经不足则快速失败
        min_required = n - int((1.0 - self.config.sim_th) * n)
        remaining = n
        
        for i in range(n):
            t1 = tokens1[i]
            t2 = tokens2[i]
            if t1 == t2 or t1 == "<*>" or t2 == "<*>":
                same_count += 1
                if same_count >= min_required:
                    # 已经满足阈值，快速返回
                    return 1.0 if same_count == n else (same_count + 0.001) / n
            remaining -= 1
            # 快速失败：即使剩下全相同也达不到阈值
            if same_count + remaining < min_required:
                return 0.0
        
        return same_count / n
    
    def _record_event(self, event: TemplateEvent):
        """记录生命周期事件"""
        self.events.append(event)
    
    def _create_template(self, tokens: List[str]) -> LogTemplate:
        """创建新模板"""
        self._template_counter += 1
        template_id = f"T{self._template_counter:06d}"
        template_str = " ".join(tokens)
        now = datetime.now()
        
        template = LogTemplate(
            template_id=template_id,
            template_str=template_str,
            tokens=tokens.copy(),
            is_new=True,
            created_at=now,
        )
        
        self.templates[template_id] = template
        self._record_event(TemplateEvent(
            timestamp=now,
            event_type=EVENT_TYPE_CREATED,
            template_id=template_id,
            details=f"创建模板: {template_str[:80]}",
        ))
        return template
    
    def _add_template_to_tree(self, template: LogTemplate):
        """将模板添加到前缀树中"""
        tokens = template.tokens
        token_count = len(tokens)
        count_key = str(token_count)
        
        if count_key not in self.root.children:
            self.root.children[count_key] = DrainNode(depth=1)
        
        current_node = self.root.children[count_key]
        
        # 逐层创建节点
        for i in range(min(token_count, self.config.depth - 2)):
            token = tokens[i]
            
            # 如果子节点数超过maxChild，考虑使用通配符
            if token not in current_node.children and len(current_node.children) >= self.config.max_child:
                if "<*>" not in current_node.children:
                    current_node.children["<*>"] = DrainNode(depth=current_node.depth + 1)
                current_node = current_node.children["<*>"]
            else:
                if token not in current_node.children:
                    current_node.children[token] = DrainNode(depth=current_node.depth + 1)
                current_node = current_node.children[token]
        
        current_node.templates.append(template)
    
    def merge_templates(self):
        """合并相似模板（只有一个token不同且其中一个是通配符）"""
        changed = True
        while changed:
            changed = False
            template_list = list(self.templates.values())
            
            for i in range(len(template_list)):
                t1 = template_list[i]
                if t1.template_id not in self.templates:
                    continue
                
                for j in range(i + 1, len(template_list)):
                    t2 = template_list[j]
                    if t2.template_id not in self.templates:
                        continue
                    
                    if len(t1.tokens) != len(t2.tokens):
                        continue
                    
                    # 找出不同的位置
                    diff_positions = []
                    for pos, (tok1, tok2) in enumerate(zip(t1.tokens, t2.tokens)):
                        if tok1 != tok2:
                            diff_positions.append(pos)
                    
                    # 只有一个位置不同
                    if len(diff_positions) == 1:
                        pos = diff_positions[0]
                        tok1 = t1.tokens[pos]
                        tok2 = t2.tokens[pos]
                        now = datetime.now()
                        
                        # 如果其中一个是通配符，或者两个都不是通配符则合并为通配符
                        if tok1 == "<*>" or tok2 == "<*>":
                            # 保留通配符模板，合并统计
                            if tok1 == "<*>":
                                self._merge_template_stats(t1, t2)
                                self._remove_template_from_tree(t2)
                                t2.merged_at = now
                                del self.templates[t2.template_id]
                                self._record_event(TemplateEvent(
                                    timestamp=now,
                                    event_type=EVENT_TYPE_MERGED,
                                    template_id=t2.template_id,
                                    related_template_id=t1.template_id,
                                    details=f"合并到通配符模板 {t1.template_id}",
                                ))
                            else:
                                self._merge_template_stats(t2, t1)
                                self._remove_template_from_tree(t1)
                                t1.merged_at = now
                                del self.templates[t1.template_id]
                                self._record_event(TemplateEvent(
                                    timestamp=now,
                                    event_type=EVENT_TYPE_MERGED,
                                    template_id=t1.template_id,
                                    related_template_id=t2.template_id,
                                    details=f"合并到通配符模板 {t2.template_id}",
                                ))
                            changed = True
                            break
                        else:
                            # 两个都不是通配符，合并为通配符模板
                            new_tokens = t1.tokens.copy()
                            new_tokens[pos] = "<*>"
                            
                            # 检查是否已有通配符模板
                            existing = None
                            for t in self.templates.values():
                                if t.tokens == new_tokens:
                                    existing = t
                                    break
                            
                            if existing:
                                self._merge_template_stats(existing, t1)
                                self._merge_template_stats(existing, t2)
                                self._remove_template_from_tree(t1)
                                self._remove_template_from_tree(t2)
                                t1.merged_at = now
                                t2.merged_at = now
                                del self.templates[t1.template_id]
                                del self.templates[t2.template_id]
                                self._record_event(TemplateEvent(
                                    timestamp=now,
                                    event_type=EVENT_TYPE_MERGED,
                                    template_id=t1.template_id,
                                    related_template_id=existing.template_id,
                                    details=f"合并到现有通配符模板",
                                ))
                                self._record_event(TemplateEvent(
                                    timestamp=now,
                                    event_type=EVENT_TYPE_MERGED,
                                    template_id=t2.template_id,
                                    related_template_id=existing.template_id,
                                    details=f"合并到现有通配符模板",
                                ))
                            else:
                                # 创建新的通配符模板
                                new_template = self._create_template(new_tokens)
                                self._merge_template_stats(new_template, t1)
                                self._merge_template_stats(new_template, t2)
                                self._remove_template_from_tree(t1)
                                self._remove_template_from_tree(t2)
                                t1.merged_at = now
                                t2.merged_at = now
                                del self.templates[t1.template_id]
                                del self.templates[t2.template_id]
                                self._add_template_to_tree(new_template)
                                self._record_event(TemplateEvent(
                                    timestamp=now,
                                    event_type=EVENT_TYPE_MERGED,
                                    template_id=t1.template_id,
                                    related_template_id=new_template.template_id,
                                    details=f"合并创建新通配符模板",
                                ))
                                self._record_event(TemplateEvent(
                                    timestamp=now,
                                    event_type=EVENT_TYPE_MERGED,
                                    template_id=t2.template_id,
                                    related_template_id=new_template.template_id,
                                    details=f"合并创建新通配符模板",
                                ))
                            
                            changed = True
                            break
                
                if changed:
                    break
    
    def _merge_template_stats(self, target: LogTemplate, source: LogTemplate):
        """合并两个模板的统计信息"""
        target.stats.count += source.stats.count
        
        if source.stats.first_seen is not None:
            if target.stats.first_seen is None or source.stats.first_seen < target.stats.first_seen:
                target.stats.first_seen = source.stats.first_seen
        
        if source.stats.last_seen is not None:
            if target.stats.last_seen is None or source.stats.last_seen > target.stats.last_seen:
                target.stats.last_seen = source.stats.last_seen
        
        for level, count in source.stats.level_counts.items():
            target.stats.level_counts[level] = target.stats.level_counts.get(level, 0) + count
        
        for src in source.stats.sources:
            if src not in target.stats.sources:
                target.stats.sources.append(src)
        
        if source.is_error:
            target.is_error = True
    
    def _remove_template_from_tree(self, template: LogTemplate):
        """从前缀树中移除模板（只从叶子节点移除，不删除空节点）"""
        tokens = template.tokens
        token_count = len(tokens)
        count_key = str(token_count)
        
        if count_key not in self.root.children:
            return
        
        current_node = self.root.children[count_key]
        
        for i in range(min(token_count, self.config.depth - 2)):
            token = tokens[i]
            if token in current_node.children:
                current_node = current_node.children[token]
            elif "<*>" in current_node.children:
                current_node = current_node.children["<*>"]
            else:
                return
        
        current_node.templates = [t for t in current_node.templates if t.template_id != template.template_id]
    
    def get_templates_sorted(self) -> List[LogTemplate]:
        """按出现频率排序的模板列表"""
        return sorted(self.templates.values(), key=lambda t: t.stats.count, reverse=True)
    
    def get_template_count(self) -> int:
        """获取模板总数"""
        return len(self.templates)
    
    def to_dict(self) -> dict:
        return {
            "config": {
                "depth": self.config.depth,
                "st": self.config.st,
                "max_child": self.config.max_child,
                "sim_th": self.config.sim_th,
            },
            "template_counter": self._template_counter,
            "templates": {k: v.to_dict() for k, v in self.templates.items()},
            "root": self.root.to_dict(),
            "events": [e.to_dict() for e in self.events],
        }
    
    @classmethod
    def from_dict(cls, data: dict, preprocessor: LogPreprocessor) -> "Drain":
        config_data = data.get("config", {})
        config = DrainConfig(
            depth=config_data.get("depth", 4),
            st=config_data.get("st", 0.4),
            max_child=config_data.get("max_child", 100),
            sim_th=config_data.get("sim_th", 0.4),
        )
        
        drain = cls(config, preprocessor)
        drain._template_counter = data.get("template_counter", 0)
        drain.templates = {k: LogTemplate.from_dict(v) for k, v in data.get("templates", {}).items()}
        drain.root = DrainNode.from_dict(data.get("root", {}))
        drain.events = [TemplateEvent.from_dict(e) for e in data.get("events", [])]
        
        return drain
    
    def compress(self, rare_count: int = 5) -> int:
        """压缩模板树，将稀有模板标记为稀有
        
        Args:
            rare_count: 匹配日志数小于此值的模板被标记为稀有
        
        Returns:
            被标记为稀有的模板数量
        """
        rare_count_num = 0
        now = datetime.now()
        
        for template in self.templates.values():
            if template.stats.count < rare_count and not template.is_rare:
                template.is_rare = True
                template.compressed_at = now
                rare_count_num += 1
                self._record_event(TemplateEvent(
                    timestamp=now,
                    event_type=EVENT_TYPE_COMPRESSED,
                    template_id=template.template_id,
                    details=f"标记为稀有模板，count={template.stats.count}",
                ))
        
        return rare_count_num
