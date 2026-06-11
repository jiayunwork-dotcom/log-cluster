"""异常检测模块 - 新模板告警、频率异常、错误聚合"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set

from .config import AnomalyConfig, FiltersConfig
from .drain import LogTemplate
from .timeseries import TimeSeriesAnalysis, TimeSeriesAnalyzer


@dataclass
class AnomalyReport:
    """异常检测报告"""
    new_templates: List[LogTemplate] = field(default_factory=list)
    error_templates: List[LogTemplate] = field(default_factory=list)
    spike_templates: List[LogTemplate] = field(default_factory=list)
    vanished_templates: List[LogTemplate] = field(default_factory=list)
    periodic_templates: List[LogTemplate] = field(default_factory=list)
    time_series_analyses: Dict[str, TimeSeriesAnalysis] = field(default_factory=dict)
    has_anomaly: bool = False
    data_insufficient: bool = False
    data_insufficient_message: str = ""


class AnomalyDetector:
    """异常检测器"""
    
    def __init__(
        self,
        anomaly_config: AnomalyConfig,
        filters_config: FiltersConfig,
        timeseries_analyzer: TimeSeriesAnalyzer,
    ):
        self.anomaly_config = anomaly_config
        self.filters_config = filters_config
        self.timeseries_analyzer = timeseries_analyzer
        self._baseline_templates: Set[str] = set()
        self._whitelist_patterns: List[re.Pattern] = []
        self._focus_patterns: List[re.Pattern] = []
        
        self._load_baseline()
        self._compile_filter_patterns()
    
    def _load_baseline(self):
        """加载基线模板库"""
        if not self.anomaly_config.baseline_file:
            return
        
        try:
            with open(self.anomaly_config.baseline_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            templates = data.get("templates", {})
            if isinstance(templates, dict):
                self._baseline_templates = set(templates.keys())
            elif isinstance(templates, list):
                for t in templates:
                    if isinstance(t, dict):
                        tid = t.get("template_id", "")
                        if tid:
                            self._baseline_templates.add(tid)
                    elif isinstance(t, str):
                        self._baseline_templates.add(t)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    
    def _compile_filter_patterns(self):
        """编译过滤正则表达式"""
        for pattern_str in self.filters_config.whitelist:
            try:
                self._whitelist_patterns.append(re.compile(pattern_str))
            except re.error:
                pass
        
        for pattern_str in self.filters_config.focus:
            try:
                self._focus_patterns.append(re.compile(pattern_str))
            except re.error:
                pass
    
    def _is_whitelisted(self, template: LogTemplate) -> bool:
        """检查模板是否在白名单中"""
        if not self._whitelist_patterns:
            return False
        
        for pattern in self._whitelist_patterns:
            if pattern.search(template.template_str):
                return True
        
        return False
    
    def _is_focused(self, template: LogTemplate) -> bool:
        """检查模板是否在关注列表中"""
        if not self._focus_patterns:
            return True
        
        for pattern in self._focus_patterns:
            if pattern.search(template.template_str):
                return True
        
        return False
    
    def detect(
        self,
        templates: List[LogTemplate],
        template_timestamps: Optional[Dict[str, List[datetime]]] = None,
    ) -> AnomalyReport:
        """执行异常检测
        
        Args:
            templates: 模板列表
            template_timestamps: 每个模板的时间戳列表，用于时序分析
        
        Returns:
            异常检测报告
        """
        report = AnomalyReport()
        
        # 检测新模板
        for template in templates:
            if self._is_whitelisted(template):
                continue
            
            # 检查是否是新模板
            if template.template_id not in self._baseline_templates and template.is_new:
                report.new_templates.append(template)
                report.has_anomaly = True
            
            # 检查是否是错误模板
            if template.is_error:
                report.error_templates.append(template)
                report.has_anomaly = True
        
        # 时序分析和频率异常检测
        if template_timestamps:
            data_sufficient_templates = 0
            
            for template in templates:
                if self._is_whitelisted(template):
                    continue
                
                if not self._is_focused(template):
                    continue
                
                timestamps = template_timestamps.get(template.template_id, [])
                if not timestamps:
                    continue
                
                analysis = self.timeseries_analyzer.analyze(template, timestamps)
                report.time_series_analyses[template.template_id] = analysis
                
                # 检查数据是否充足
                if len(analysis.time_series) >= self.anomaly_config.min_data_points:
                    data_sufficient_templates += 1
                    
                    if analysis.has_spike:
                        report.spike_templates.append(template)
                        report.has_anomaly = True
                    
                    if analysis.has_vanished:
                        report.vanished_templates.append(template)
                        report.has_anomaly = True
                    
                    if analysis.is_periodic:
                        report.periodic_templates.append(template)
            
            if data_sufficient_templates == 0 and template_timestamps:
                report.data_insufficient = True
                report.data_insufficient_message = (
                    f"数据不足：需要至少 {self.anomaly_config.min_data_points} 个时间点，"
                    f"当前不足，跳过频率分析"
                )
        
        # 排序
        report.new_templates.sort(key=lambda t: t.stats.count, reverse=True)
        report.error_templates.sort(key=lambda t: t.stats.count, reverse=True)
        report.spike_templates.sort(key=lambda t: t.stats.count, reverse=True)
        report.vanished_templates.sort(key=lambda t: t.stats.count, reverse=True)
        report.periodic_templates.sort(key=lambda t: t.stats.count, reverse=True)
        
        return report
