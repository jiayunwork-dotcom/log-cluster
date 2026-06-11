"""核心处理流程 - 整合所有模块的流水线"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from .anomaly import AnomalyDetector, AnomalyReport
from .clustering import TemplateCluster, TemplateClustering
from .config import AppConfig
from .drain import Drain, LogPreprocessor, LogTemplate
from .incremental import FileOffset, StateManager
from .parser import LogEntry, LogParser
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
    
    def process_file(
        self,
        file_path: str,
        incremental: bool = False,
        load_state: bool = False,
    ) -> ProcessResult:
        """处理单个日志文件
        
        Args:
            file_path: 日志文件路径
            incremental: 是否增量模式
            load_state: 是否加载历史状态
        
        Returns:
            处理结果
        """
        start_time = time.time()
        
        # 加载状态
        drain = None
        file_offsets: Dict[str, FileOffset] = {}
        
        if load_state or incremental:
            drain, file_offsets = self.state_manager.load_state(self.preprocessor)
        
        if drain is None:
            drain = Drain(self.config.drain, self.preprocessor)
        
        # 确定起始偏移
        offset = 0
        if incremental:
            offset = self.state_manager.get_file_offset(file_path, file_offsets)
        
        # 读取并处理日志
        total_logs = 0
        template_timestamps: Dict[str, List[datetime]] = {}
        
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            
            parse_line = self.parser.parse_line
            add_log = drain.add_log_message
            t_timestamps = template_timestamps
            t_ts_get = t_timestamps.get
            # 检查是否需要收集完整的时间戳（时序分析需要）- 默认都收集，保持功能完整
            need_timestamps = True
            
            for line in f:
                if not line or not line.strip():
                    continue
                
                entry = parse_line(line)
                msg = entry.message
                if not msg or not msg.strip():
                    continue
                
                template = add_log(entry)
                total_logs += 1
                
                # 只在需要时序分析时收集时间戳 - 大幅节省内存和开销
                if need_timestamps:
                    ts = entry.timestamp
                    if ts is not None:
                        tid = template.template_id
                        tsl = t_ts_get(tid)
                        if tsl is None:
                            tsl = []
                            t_timestamps[tid] = tsl
                        tsl.append(ts)
        
        # 更新文件偏移
        new_offset = os.path.getsize(file_path)
        self.state_manager.update_file_offset(file_path, new_offset, file_offsets)
        
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
        
        # 聚类
        clusters = self.clustering.cluster(templates)
        
        # 异常检测
        anomaly_report = self.anomaly_detector.detect(templates, template_timestamps)
        
        duration = time.time() - start_time
        
        return ProcessResult(
            total_logs=total_logs,
            templates=templates,
            clusters=clusters,
            anomaly_report=anomaly_report,
            template_timestamps=template_timestamps,
            duration=duration,
            drain=drain,
            file_offsets=file_offsets,
        )
    
    def process_files(
        self,
        file_paths: List[str],
        incremental: bool = False,
        load_state: bool = False,
    ) -> ProcessResult:
        """处理多个日志文件"""
        start_time = time.time()
        
        # 加载状态
        drain = None
        file_offsets: Dict[str, FileOffset] = {}
        
        if load_state or incremental:
            drain, file_offsets = self.state_manager.load_state(self.preprocessor)
        
        if drain is None:
            drain = Drain(self.config.drain, self.preprocessor)
        
        total_logs = 0
        template_timestamps: Dict[str, List[datetime]] = {}
        
        for file_path in file_paths:
            # 确定起始偏移
            offset = 0
            if incremental:
                offset = self.state_manager.get_file_offset(file_path, file_offsets)
            
            # 读取并处理日志
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
                        if template.template_id not in template_timestamps:
                            template_timestamps[template.template_id] = []
                        template_timestamps[template.template_id].append(entry.timestamp)
            
            # 更新文件偏移
            try:
                new_offset = os.path.getsize(file_path)
                self.state_manager.update_file_offset(file_path, new_offset, file_offsets)
            except OSError:
                pass
        
        # 合并模板
        drain.merge_templates()
        
        # 压缩模板
        if drain.get_template_count() > self.config.incremental.max_templates:
            drain.compress(self.config.incremental.rare_template_count)
        
        # 保存状态
        if incremental or load_state:
            self.state_manager.save_state(drain, file_offsets)
        
        # 获取排序后的模板
        templates = drain.get_templates_sorted()
        
        # 聚类
        clusters = self.clustering.cluster(templates)
        
        # 异常检测
        anomaly_report = self.anomaly_detector.detect(templates, template_timestamps)
        
        duration = time.time() - start_time
        
        return ProcessResult(
            total_logs=total_logs,
            templates=templates,
            clusters=clusters,
            anomaly_report=anomaly_report,
            template_timestamps=template_timestamps,
            duration=duration,
            drain=drain,
            file_offsets=file_offsets,
        )
    
    def process_stream(self, stream_iterator: Iterator[str]) -> ProcessResult:
        """处理流数据（如stdin）
        
        Args:
            stream_iterator: 行迭代器
        
        Returns:
            处理结果
        """
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
                if template.template_id not in template_timestamps:
                    template_timestamps[template.template_id] = []
                template_timestamps[template.template_id].append(entry.timestamp)
        
        # 合并模板
        drain.merge_templates()
        
        # 获取排序后的模板
        templates = drain.get_templates_sorted()
        
        # 聚类
        clusters = self.clustering.cluster(templates)
        
        # 异常检测
        anomaly_report = self.anomaly_detector.detect(templates, template_timestamps)
        
        duration = time.time() - start_time
        
        return ProcessResult(
            total_logs=total_logs,
            templates=templates,
            clusters=clusters,
            anomaly_report=anomaly_report,
            template_timestamps=template_timestamps,
            duration=duration,
            drain=drain,
            file_offsets={},
        )
    
    def process_entries(self, entries: List[LogEntry]) -> ProcessResult:
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
                if template.template_id not in template_timestamps:
                    template_timestamps[template.template_id] = []
                template_timestamps[template.template_id].append(entry.timestamp)
        
        # 合并模板
        drain.merge_templates()
        
        # 获取排序后的模板
        templates = drain.get_templates_sorted()
        
        # 聚类
        clusters = self.clustering.cluster(templates)
        
        # 异常检测
        anomaly_report = self.anomaly_detector.detect(templates, template_timestamps)
        
        duration = time.time() - start_time
        
        return ProcessResult(
            total_logs=total_logs,
            templates=templates,
            clusters=clusters,
            anomaly_report=anomaly_report,
            template_timestamps=template_timestamps,
            duration=duration,
            drain=drain,
            file_offsets={},
        )
