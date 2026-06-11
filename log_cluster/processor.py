"""核心处理流程 - 整合所有模块的流水线"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from .alerts import AlertContext, AlertEngine, TriggeredAlert
from .anomaly import AnomalyDetector, AnomalyReport
from .clustering import TemplateCluster, TemplateClustering
from .config import AppConfig
from .drain import (
    Drain,
    EVENT_TYPE_COMPRESSED,
    EVENT_TYPE_CREATED,
    EVENT_TYPE_MERGED,
    LogPreprocessor,
    LogTemplate,
    TemplateEvent,
)
from .incremental import FileOffset, StateManager
from .parallel import ParallelProcessor
from .parser import LogEntry, LogParser
from .tags import TagEngine
from .timeseries import TimeSeriesAnalyzer


@dataclass
class ProcessResult:
    """处理结果"""
    total_logs: int = 0
    templates: List[LogTemplate] = field(default_factory=list)
    clusters: List[TemplateCluster] = field(default_factory=list)
    anomaly_report: Optional[AnomalyReport] = None
    template_timestamps: Dict[str, List[datetime]] = field(default_factory=dict)
    duration: float = 0.0
    drain: Optional[Drain] = None
    file_offsets: Dict[str, FileOffset] = field(default_factory=dict)
    # 新增字段
    events: List[TemplateEvent] = field(default_factory=list)
    triggered_alerts: List[TriggeredAlert] = field(default_factory=list)
    processing_speed: float = 0.0
    tag_filter: Optional[str] = None


class LogProcessor:
    """日志处理器 - 整合所有模块"""
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.preprocessor = LogPreprocessor(config.preprocess)
        self.parser = LogParser(config.input)
        self.timeseries_analyzer = TimeSeriesAnalyzer(config.anomaly)
        self.anomaly_detector = AnomalyDetector(
            config.anomaly, config.filters, self.timeseries_analyzer
        )
        self.clustering = TemplateClustering(config.clustering)
        self.state_manager = StateManager(config.incremental)
        # 新增组件
        self.parallel_processor = ParallelProcessor(config)
        self.alert_engine = AlertEngine(config)
        self.tag_engine = TagEngine(config)
    
    def process_file(
        self,
        file_path: str,
        incremental: bool = False,
        load_state: bool = False,
        workers: Optional[int] = None,
        tag_filter: Optional[str] = None,
    ) -> ProcessResult:
        """处理单个日志文件"""
        return self.process_files(
            file_paths=[file_path],
            incremental=incremental,
            load_state=load_state,
            workers=workers,
            tag_filter=tag_filter,
        )
    
    def _postprocess(
        self,
        drain: Drain,
        total_logs: int,
        template_timestamps: Dict[str, List[datetime]],
        file_offsets: Dict[str, FileOffset],
        incremental: bool,
        load_state: bool,
        duration: float,
        tag_filter: Optional[str] = None,
    ) -> ProcessResult:
        """统一后处理：打标签、聚类、异常检测、告警、状态保存"""
        # 合并模板
        drain.merge_templates()

        # 压缩模板（如果超过最大数量）
        if drain.get_template_count() > self.config.incremental.max_templates:
            drain.compress(self.config.incremental.rare_template_count)

        # 保存状态
        if incremental or load_state:
            self.state_manager.save_state(drain, file_offsets)

        # 获取排序后的模板
        templates = drain.get_templates_sorted()

        # 打标签
        self.tag_engine.apply_tags(templates)

        # 按标签过滤（如果指定）
        if tag_filter:
            templates = self.tag_engine.filter_by_tag(templates, tag_filter)

        # 聚类
        clusters = self.clustering.cluster(templates)

        # 异常检测
        anomaly_report = self.anomaly_detector.detect(templates, template_timestamps)

        # 计算处理速度
        processing_speed = total_logs / max(duration, 0.001)

        # 统计指标用于告警
        new_count = sum(1 for t in templates if t.is_new)
        error_count = sum(1 for t in templates if t.is_error)
        spike_count = sum(1 for t in templates if t.is_spike)

        # 评估告警
        alert_ctx = AlertContext(
            new_template_count=new_count,
            error_template_count=error_count,
            spike_count=spike_count,
            total_templates=len(templates),
            processing_speed=processing_speed,
        )
        triggered_alerts = self.alert_engine.evaluate(alert_ctx)

        return ProcessResult(
            total_logs=total_logs,
            templates=templates,
            clusters=clusters,
            anomaly_report=anomaly_report,
            template_timestamps=template_timestamps,
            duration=duration,
            drain=drain,
            file_offsets=file_offsets,
            events=list(drain.events),
            triggered_alerts=triggered_alerts,
            processing_speed=processing_speed,
            tag_filter=tag_filter,
        )

    def process_files(
        self,
        file_paths: List[str],
        incremental: bool = False,
        load_state: bool = False,
        workers: Optional[int] = None,
        tag_filter: Optional[str] = None,
    ) -> ProcessResult:
        """处理多个日志文件（支持并行）"""
        start_time = time.time()

        # 加载状态
        drain = None
        file_offsets: Dict[str, FileOffset] = {}

        if load_state or incremental:
            drain, file_offsets = self.state_manager.load_state(self.preprocessor)

        # 决定进程数：优先参数 > 配置
        effective_workers = workers
        if effective_workers is None:
            effective_workers = self.config.parallel.workers

        # 判断是否使用并行模式
        should_parallel = (
            len(file_paths) > 1
            and effective_workers != 0
        )

        if should_parallel:
            # 并行路径
            drain, total_logs, template_timestamps, new_offsets, _ = (
                self.parallel_processor.process_files_parallel(
                    file_paths=file_paths,
                    existing_drain=drain,
                    file_offsets=file_offsets,
                    workers=effective_workers,
                )
            )
            file_offsets = dict(new_offsets)
        else:
            # 串行路径（与原逻辑一致，但通过Drain复用）
            if drain is None:
                drain = Drain(self.config.drain, self.preprocessor)
            # 重置历史模板的is_new标记
            for t in drain.templates.values():
                t.is_new = False

            total_logs = 0
            template_timestamps: Dict[str, List[datetime]] = {}

            for file_path in file_paths:
                offset = 0
                if incremental:
                    offset = self.state_manager.get_file_offset(file_path, file_offsets)

                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(offset)
                    for line in f:
                        if not line.strip():
                            continue
                        entry = self.parser.parse_line(line)
                        if not entry.message.strip():
                            continue
                        template = drain.add_log_message(entry)
                        total_logs += 1
                        if entry.timestamp is not None:
                            tid = template.template_id
                            if tid not in template_timestamps:
                                template_timestamps[tid] = []
                            template_timestamps[tid].append(entry.timestamp)

                try:
                    new_offset = os.path.getsize(file_path)
                    self.state_manager.update_file_offset(file_path, new_offset, file_offsets)
                except OSError:
                    pass

        duration = time.time() - start_time

        # 统一后处理
        return self._postprocess(
            drain=drain,
            total_logs=total_logs,
            template_timestamps=template_timestamps,
            file_offsets=file_offsets,
            incremental=incremental,
            load_state=load_state,
            duration=duration,
            tag_filter=tag_filter,
        )
    
    def process_stream(
        self,
        stream_iterator: Iterator[str],
        tag_filter: Optional[str] = None,
    ) -> ProcessResult:
        """处理流数据（如stdin）"""
        start_time = time.time()

        drain = Drain(self.config.drain, self.preprocessor)
        total_logs = 0
        template_timestamps: Dict[str, List[datetime]] = {}

        for line in stream_iterator:
            if not line.strip():
                continue
            entry = self.parser.parse_line(line)
            if not entry.message.strip():
                continue
            template = drain.add_log_message(entry)
            total_logs += 1
            if entry.timestamp is not None:
                tid = template.template_id
                if tid not in template_timestamps:
                    template_timestamps[tid] = []
                template_timestamps[tid].append(entry.timestamp)

        duration = time.time() - start_time
        return self._postprocess(
            drain=drain,
            total_logs=total_logs,
            template_timestamps=template_timestamps,
            file_offsets={},
            incremental=False,
            load_state=False,
            duration=duration,
            tag_filter=tag_filter,
        )

    def process_entries(
        self,
        entries: List[LogEntry],
        tag_filter: Optional[str] = None,
    ) -> ProcessResult:
        """处理已解析的日志条目列表"""
        start_time = time.time()

        drain = Drain(self.config.drain, self.preprocessor)
        total_logs = 0
        template_timestamps: Dict[str, List[datetime]] = {}

        for entry in entries:
            if not entry.message.strip():
                continue
            template = drain.add_log_message(entry)
            total_logs += 1
            if entry.timestamp is not None:
                tid = template.template_id
                if tid not in template_timestamps:
                    template_timestamps[tid] = []
                template_timestamps[tid].append(entry.timestamp)

        duration = time.time() - start_time
        return self._postprocess(
            drain=drain,
            total_logs=total_logs,
            template_timestamps=template_timestamps,
            file_offsets={},
            incremental=False,
            load_state=False,
            duration=duration,
            tag_filter=tag_filter,
        )
