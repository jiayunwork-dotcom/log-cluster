"""并行处理模块 - 多进程解析日志文件并合并结果"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from multiprocessing import Pool, cpu_count
from typing import Dict, List, Tuple

from .config import AppConfig
from .drain import (
    Drain,
    EVENT_TYPE_CREATED,
    EVENT_TYPE_MERGED,
    LogPreprocessor,
    LogTemplate,
    TemplateEvent,
    TemplateStats,
)
from .parser import LogEntry, LogParser


@dataclass
class WorkerResult:
    """单个工作进程的处理结果"""
    drain_dict: dict
    total_logs: int
    template_timestamps: Dict[str, List[str]] = field(default_factory=dict)
    file_offsets: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    duration: float = 0.0


def _compute_similarity(tokens1: List[str], tokens2: List[str]) -> float:
    """计算两个token序列的相似度"""
    n = len(tokens1)
    if n != len(tokens2) or n == 0:
        return 0.0 if n != 0 else 1.0

    same_count = 0
    for i in range(n):
        t1 = tokens1[i]
        t2 = tokens2[i]
        if t1 == t2 or t1 == "<*>" or t2 == "<*>":
            same_count += 1
    return same_count / n


def _process_single_file(args) -> WorkerResult:
    """单进程处理单个文件（用于多进程调用）
    
    注意：此函数在子进程中执行，不能依赖主进程的对象状态
    """
    (
        file_path,
        offset,
        drain_config_dict,
        preprocess_config_dict,
        input_config_dict,
    ) = args

    from .config import DrainConfig, InputConfig, PreprocessConfig, PreprocessPattern

    start_time = time.time()

    # 重建配置对象
    drain_config = DrainConfig(**drain_config_dict)
    patterns = [PreprocessPattern(**p) for p in preprocess_config_dict["patterns"]]
    preprocess_config = PreprocessConfig(
        patterns=patterns,
        order=preprocess_config_dict.get("order", []),
    )
    input_config = InputConfig(**input_config_dict)

    # 创建组件
    preprocessor = LogPreprocessor(preprocess_config)
    parser = LogParser(input_config)
    drain = Drain(drain_config, preprocessor)

    total_logs = 0
    template_timestamps: Dict[str, List[str]] = {}

    # 读取并处理日志
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(offset)

        for line in f:
            if not line.strip():
                continue

            entry = parser.parse_line(line)
            if not entry.message.strip():
                continue

            template = drain.add_log_message(entry)
            total_logs += 1

            if entry.timestamp is not None:
                tid = template.template_id
                if tid not in template_timestamps:
                    template_timestamps[tid] = []
                template_timestamps[tid].append(entry.timestamp.isoformat())

    # 合并模板
    drain.merge_templates()

    # 获取文件偏移
    try:
        new_offset = os.path.getsize(file_path)
        stat = os.stat(file_path)
        inode = stat.st_ino
    except OSError:
        new_offset = offset
        inode = 0

    duration = time.time() - start_time

    return WorkerResult(
        drain_dict=drain.to_dict(),
        total_logs=total_logs,
        template_timestamps=template_timestamps,
        file_offsets={file_path: (new_offset, inode)},
        duration=duration,
    )


class ParallelProcessor:
    """并行处理器 - 使用多进程解析多个日志文件"""

    def __init__(self, config: AppConfig):
        self.config = config

    def _get_default_workers(self) -> int:
        """获取默认进程数：CPU核心数的一半，最少1"""
        return max(1, cpu_count() // 2)

    def _resolve_workers(self, requested: int) -> int:
        """解析实际进程数"""
        if requested == 0:
            return 1  # 0表示禁用并行，用单进程
        if requested < 0:
            return self._get_default_workers()
        return max(1, min(requested, cpu_count()))

    def process_files_parallel(
        self,
        file_paths: List[str],
        existing_drain: Drain | None,
        file_offsets: Dict,
        workers: int | None = None,
    ) -> Tuple[Drain, int, Dict[str, List[datetime]], Dict, float]:
        """并行处理多个文件，返回合并后的结果

        Args:
            file_paths: 文件路径列表
            existing_drain: 已有的Drain实例（用于增量模式）
            file_offsets: 文件偏移字典
            workers: 进程数，None使用配置

        Returns:
            (合并后的Drain, 总日志数, 模板时间戳, 新文件偏移, 总耗时)
        """
        start_time = time.time()

        # 确定进程数
        if workers is None:
            workers = self.config.parallel.workers

        # 单文件或明确要求单进程，走串行路径
        if len(file_paths) <= 1 or workers == 0:
            return self._process_sequential(
                file_paths, existing_drain, file_offsets
            )

        num_workers = self._resolve_workers(workers)

        # 准备配置字典（用于跨进程传递）
        drain_config_dict = {
            "depth": self.config.drain.depth,
            "st": self.config.drain.st,
            "max_child": self.config.drain.max_child,
            "sim_th": self.config.drain.sim_th,
        }
        preprocess_config_dict = {
            "patterns": [
                {"name": p.name, "regex": p.regex}
                for p in self.config.preprocess.patterns
            ],
            "order": self.config.preprocess.order,
        }
        input_config_dict = {
            "format": self.config.input.format,
            "time_format": self.config.input.time_format,
            "custom_regex": self.config.input.custom_regex,
            "timestamp_group": self.config.input.timestamp_group,
            "level_group": self.config.input.level_group,
            "source_group": self.config.input.source_group,
            "message_group": self.config.input.message_group,
        }

        # 准备任务参数
        tasks = []
        for fp in file_paths:
            abs_path = os.path.abspath(fp)
            offset = 0
            if abs_path in file_offsets:
                offset_info = file_offsets[abs_path]
                try:
                    offset = offset_info.offset
                    stat = os.stat(fp)
                    if stat.st_ino != offset_info.inode:
                        offset = 0
                except (OSError, AttributeError):
                    offset = 0
            tasks.append((
                fp, offset,
                drain_config_dict,
                preprocess_config_dict,
                input_config_dict,
            ))

        # 执行多进程
        if num_workers == 1:
            results = [_process_single_file(t) for t in tasks]
        else:
            with Pool(processes=num_workers) as pool:
                results = pool.map(_process_single_file, tasks)

        # 合并结果
        return self._merge_results(
            results, existing_drain, file_offsets, start_time
        )

    def _process_sequential(
        self,
        file_paths: List[str],
        existing_drain: Drain | None,
        file_offsets: Dict,
    ) -> Tuple[Drain, int, Dict[str, List[datetime]], Dict, float]:
        """串行处理（与原逻辑一致，使用主进程Drain）"""
        start_time = time.time()

        # 通过processor的标准流程处理，这里只返回占位
        # 实际串行逻辑在processor.py中
        if existing_drain is None:
            existing_drain = Drain(
                self.config.drain,
                LogPreprocessor(self.config.preprocess),
            )

        total_logs = 0
        template_timestamps: Dict[str, List[datetime]] = {}
        new_offsets = dict(file_offsets)

        parser = LogParser(self.config.input)

        for file_path in file_paths:
            abs_path = os.path.abspath(file_path)
            offset = 0
            if abs_path in file_offsets:
                try:
                    offset_info = file_offsets[abs_path]
                    stat = os.stat(file_path)
                    if stat.st_ino == offset_info.inode:
                        file_size = os.path.getsize(file_path)
                        if offset_info.offset <= file_size:
                            offset = offset_info.offset
                except (OSError, AttributeError):
                    pass

            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                for line in f:
                    if not line.strip():
                        continue
                    entry = parser.parse_line(line)
                    if not entry.message.strip():
                        continue
                    template = existing_drain.add_log_message(entry)
                    total_logs += 1
                    if entry.timestamp is not None:
                        tid = template.template_id
                        if tid not in template_timestamps:
                            template_timestamps[tid] = []
                        template_timestamps[tid].append(entry.timestamp)

            try:
                new_offset = os.path.getsize(file_path)
                stat = os.stat(file_path)
                from .incremental import FileOffset
                new_offsets[abs_path] = FileOffset(
                    path=abs_path,
                    offset=new_offset,
                    inode=stat.st_ino,
                )
            except OSError:
                pass

        # 合并模板
        existing_drain.merge_templates()

        duration = time.time() - start_time
        return (
            existing_drain,
            total_logs,
            template_timestamps,
            new_offsets,
            duration,
        )

    def _merge_results(
        self,
        results: List[WorkerResult],
        existing_drain: Drain | None,
        file_offsets: Dict,
        start_time: float,
    ) -> Tuple[Drain, int, Dict[str, List[datetime]], Dict, float]:
        """合并多个工作进程的结果

        合并规则：
        1. 模板字符串完全相同 -> 直接累加计数
        2. 模板字符串相似度 >= st -> 触发模板合并逻辑
        """
        preprocessor = LogPreprocessor(self.config.preprocess)
        sim_th = self.config.drain.st

        # 从增量模式恢复
        if existing_drain is not None:
            merged_drain = existing_drain
            # 重置所有模板的is_new标记，因为它们来自历史状态
            for t in merged_drain.templates.values():
                t.is_new = False
        else:
            merged_drain = Drain(self.config.drain, preprocessor)

        total_logs = 0
        template_timestamps: Dict[str, List[datetime]] = {}
        new_offsets = dict(file_offsets)

        # 收集所有子进程的模板
        all_partial_templates: List[LogTemplate] = []
        all_partial_events: List[TemplateEvent] = []

        for result in results:
            total_logs += result.total_logs

            # 解析worker的drain字典
            partial_drain = Drain.from_dict(result.drain_dict, preprocessor)
            all_partial_templates.extend(partial_drain.templates.values())
            all_partial_events.extend(partial_drain.events)

            # 合并时间戳
            for tid, ts_list in result.template_timestamps.items():
                dt_list = [datetime.fromisoformat(ts) for ts in ts_list]
                if tid not in template_timestamps:
                    template_timestamps[tid] = []
                template_timestamps[tid].extend(dt_list)

            # 合并文件偏移
            for fp, (off, ino) in result.file_offsets.items():
                abs_path = os.path.abspath(fp)
                from .incremental import FileOffset
                new_offsets[abs_path] = FileOffset(
                    path=abs_path,
                    offset=off,
                    inode=ino,
                )

        # 将所有部分模板合并到merged_drain
        self._merge_partial_templates(
            merged_drain, all_partial_templates, sim_th
        )

        # 合并事件（保留子进程产生的事件）
        # 但要注意：事件中的template_id可能需要重映射
        # 为简化，这里只追加创建事件，合并事件在主merge中重新生成
        new_events = []
        existing_tids = set(merged_drain.templates.keys())
        for ev in all_partial_events:
            if ev.event_type == EVENT_TYPE_CREATED:
                if ev.template_id in existing_tids:
                    new_events.append(ev)
            elif ev.event_type == EVENT_TYPE_MERGED:
                # 合并事件可能因template_id失效被忽略
                pass
        # 注意：实际新的合并事件在_merge_partial_templates中已经记录
        # 这里不再重复追加

        # 最终再执行一次合并
        merged_drain.merge_templates()

        duration = time.time() - start_time
        return (
            merged_drain,
            total_logs,
            template_timestamps,
            new_offsets,
            duration,
        )

    def _merge_partial_templates(
        self,
        target: Drain,
        sources: List[LogTemplate],
        sim_th: float,
    ):
        """将源模板列表合并到目标Drain中

        规则：
        1. 先按模板字符串精确匹配 -> 累加统计
        2. 再按相似度 >= st 匹配 -> 触发合并
        3. 否则作为新模板添加
        """
        now = datetime.now()

        for src_tpl in sources:
            # 第一步：精确匹配模板字符串
            matched = None
            for tgt_tpl in target.templates.values():
                if tgt_tpl.template_str == src_tpl.template_str:
                    matched = tgt_tpl
                    break

            if matched is not None:
                # 精确匹配：累加统计
                self._accumulate_stats(matched, src_tpl)
                # 重新映射时间戳对应的template_id
                continue

            # 第二步：相似度匹配
            tokens_a = src_tpl.tokens
            best_match = None
            best_sim = -1.0

            for tgt_tpl in target.templates.values():
                tokens_b = tgt_tpl.tokens
                sim = _compute_similarity(tokens_a, tokens_b)
                if sim >= sim_th and sim > best_sim:
                    best_sim = sim
                    best_match = tgt_tpl

            if best_match is not None:
                # 相似度匹配：触发合并逻辑
                # 如果源模板tokens数相同，尝试通配符合并
                if len(tokens_a) == len(best_match.tokens):
                    diff_positions = []
                    for pos, (ta, tb) in enumerate(zip(tokens_a, best_match.tokens)):
                        if ta != tb:
                            diff_positions.append(pos)
                    if len(diff_positions) == 1:
                        pos = diff_positions[0]
                        ta = tokens_a[pos]
                        tb = best_match.tokens[pos]
                        if ta != "<*>" and tb != "<*>":
                            # 将目标模板位置变成通配符
                            new_tokens = best_match.tokens.copy()
                            new_tokens[pos] = "<*>"
                            best_match.tokens = new_tokens
                            best_match.template_str = " ".join(new_tokens)
                            # 标记合并事件
                            target._record_event(TemplateEvent(
                                timestamp=now,
                                event_type=EVENT_TYPE_MERGED,
                                template_id=src_tpl.template_id,
                                related_template_id=best_match.template_id,
                                details=f"并行合并: 位置{pos}合并为<*>",
                            ))
                            src_tpl.merged_at = now
                # 累加统计
                self._accumulate_stats(best_match, src_tpl)
                src_tpl.merged_at = src_tpl.merged_at or now
            else:
                # 第三步：作为新模板添加
                # 需要重新分配template_id避免冲突
                new_id = self._allocate_template_id(target, src_tpl.template_id)
                src_tpl.template_id = new_id
                src_tpl.is_new = True
                src_tpl.created_at = src_tpl.created_at or now
                target.templates[new_id] = src_tpl
                target._add_template_to_tree(src_tpl)
                target._record_event(TemplateEvent(
                    timestamp=now,
                    event_type=EVENT_TYPE_CREATED,
                    template_id=new_id,
                    details=f"并行新增模板: {src_tpl.template_str[:80]}",
                ))

    def _allocate_template_id(self, drain: Drain, preferred: str) -> str:
        """分配不冲突的模板ID"""
        if preferred not in drain.templates:
            # 尝试保持原ID，但确保计数器大于ID数字
            try:
                num = int(preferred[1:]) if preferred.startswith("T") else 0
                if num > drain._template_counter:
                    drain._template_counter = num
            except ValueError:
                pass
            return preferred
        # 冲突时生成新ID
        drain._template_counter += 1
        return f"T{drain._template_counter:06d}"

    def _accumulate_stats(self, target_tpl: LogTemplate, source_tpl: LogTemplate):
        """累加统计信息"""
        target_tpl.stats.count += source_tpl.stats.count

        if source_tpl.stats.first_seen is not None:
            if (target_tpl.stats.first_seen is None or
                    source_tpl.stats.first_seen < target_tpl.stats.first_seen):
                target_tpl.stats.first_seen = source_tpl.stats.first_seen

        if source_tpl.stats.last_seen is not None:
            if (target_tpl.stats.last_seen is None or
                    source_tpl.stats.last_seen > target_tpl.stats.last_seen):
                target_tpl.stats.last_seen = source_tpl.stats.last_seen

        for level, count in source_tpl.stats.level_counts.items():
            target_tpl.stats.level_counts[level] = (
                target_tpl.stats.level_counts.get(level, 0) + count
            )

        for src in source_tpl.stats.sources:
            if src not in target_tpl.stats.sources:
                target_tpl.stats.sources.append(src)
                if len(target_tpl.stats.sources) > 100:
                    del target_tpl.stats.sources[100:]

        if source_tpl.is_error:
            target_tpl.is_error = True
