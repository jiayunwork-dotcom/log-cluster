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
        self._baseline_template_strs: Set[str] = set()
        self._baseline_template_ids: Set[str] = set()
        self._whitelist_patterns: List[re.Pattern] = []
        self._focus_patterns: List[re.Pattern] = []
        
        self._load_baseline()
        self._compile_filter_patterns()
    
    def _extract_templates_from_data(self, data: dict):
        """从各种格式的数据中提取模板列表"""
        templates_data = None
        
        # 状态文件格式: {drain_state: {templates: {...}}}
        if "drain_state" in data:
            drain_state = data["drain_state"]
            templates_data = drain_state.get("templates", {})
        # 报告格式: {templates: {...}}
        elif "templates" in data:
            templates_data = data.get("templates", {})
        # 直接是模板字典
        elif isinstance(data, dict) and any(isinstance(v, dict) and "template_str" in v for v in data.values()):
            templates_data = data
        
        result_ids: Set[str] = set()
        result_strs: Set[str] = set()
        
        if templates_data is None:
            return result_ids, result_strs
        
        # 字典格式: {template_id: template_data}
        if isinstance(templates_data, dict):
            for tid, tdata in templates_data.items():
                if isinstance(tdata, dict):
                    result_ids.add(tid)
                    tstr = tdata.get("template_str", "")
                    if tstr:
                        result_strs.add(tstr)
        # 列表格式: [{template_id:..., template_str:...}]
        elif isinstance(templates_data, list):
            for t in templates_data:
                if isinstance(t, dict):
                    tid = t.get("template_id", "")
                    tstr = t.get("template_str", "")
                    if tid:
                        result_ids.add(tid)
                    if tstr:
                        result_strs.add(tstr)
        
        return result_ids, result_strs
    
    def _load_baseline(self):
        """加载基线模板库"""
        if not self.anomaly_config.baseline_file:
            return
        
        try:
            with open(self.anomaly_config.baseline_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            ids, strs = self._extract_templates_from_data(data)
            self._baseline_template_ids = ids
            self._baseline_template_strs = strs
        except (FileNotFoundError, json.JSONDecodeError, IsADirectoryError):
            pass
    
    def _is_template_in_baseline(self, template: LogTemplate) -> bool:
        """检查模板是否在基线中（优先按字符串匹配，其次按ID）"""
        if not self._baseline_template_strs and not self._baseline_template_ids:
            return False
        
        # 优先按模板字符串匹配（跨运行稳定）
        if template.template_str in self._baseline_template_strs:
            return True
        
        # 其次按ID匹配（同一运行内）
        if template.template_id in self._baseline_template_ids:
            return True
        
        return False
    
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
        
        has_baseline = bool(self._baseline_template_strs or self._baseline_template_ids)
        
        # 检测新模板
        for template in templates:
            if self._is_whitelisted(template):
                continue
            
            # 检查是否是新模板：有基线且不在基线中，或者没有基线但标记为is_new
            in_baseline = self._is_template_in_baseline(template)
            is_new_template = False
            if has_baseline:
                # 有基线时，以基线为准
                is_new_template = not in_baseline
            else:
                # 无基线时，使用Drain的is_new标记
                is_new_template = template.is_new
            
            # 清除已在基线中的模板的is_new标记
            if has_baseline and in_baseline:
                template.is_new = False
            
            if is_new_template:
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
