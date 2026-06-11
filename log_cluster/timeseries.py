"""时序分析模块 - 频率时间序列分析、周期性检测和突变点检测"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from .config import AnomalyConfig
from .drain import LogTemplate


@dataclass
class TimeSeriesPoint:
    """时间序列数据点"""
    timestamp: datetime
    value: int


@dataclass
class TimeSeriesAnalysis:
    """时间序列分析结果"""
    template_id: str
    time_series: List[TimeSeriesPoint] = field(default_factory=list)
    is_periodic: bool = False
    periodicity_hours: Optional[float] = None
    has_spike: bool = False
    spike_timestamps: List[datetime] = field(default_factory=list)
    has_vanished: bool = False
    vanished_since: Optional[datetime] = None
    mean: float = 0.0
    std: float = 0.0


class TimeSeriesAnalyzer:
    """时间序列分析器"""
    
    def __init__(self, config: AnomalyConfig):
        self.config = config
        self._unit_to_seconds = {
            "minute": 60,
            "hour": 3600,
            "day": 86400,
        }
    
    def _get_time_bucket(self, ts: datetime) -> datetime:
        """将时间戳对齐到时间桶"""
        unit = self.config.freq_unit
        
        if unit == "minute":
            return ts.replace(second=0, microsecond=0)
        elif unit == "hour":
            return ts.replace(minute=0, second=0, microsecond=0)
        elif unit == "day":
            return ts.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            return ts.replace(minute=0, second=0, microsecond=0)
    
    def build_time_series(self, timestamps: List[datetime]) -> List[TimeSeriesPoint]:
        """从时间戳列表构建时间序列"""
        if not timestamps:
            return []
        
        sorted_ts = sorted(timestamps)
        bucket_counts: Dict[datetime, int] = {}
        
        for ts in sorted_ts:
            bucket = self._get_time_bucket(ts)
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        
        if not bucket_counts:
            return []
        
        # 填充所有时间桶（从最早到最晚）
        start = self._get_time_bucket(min(bucket_counts.keys()))
        end = self._get_time_bucket(max(bucket_counts.keys()))
        
        time_series = []
        current = start
        delta = self._get_delta()
        
        while current <= end:
            count = bucket_counts.get(current, 0)
            time_series.append(TimeSeriesPoint(timestamp=current, value=count))
            current += delta
        
        return time_series
    
    def _get_delta(self) -> timedelta:
        """获取时间桶间隔"""
        unit = self.config.freq_unit
        if unit == "minute":
            return timedelta(minutes=1)
        elif unit == "hour":
            return timedelta(hours=1)
        elif unit == "day":
            return timedelta(days=1)
        else:
            return timedelta(hours=1)
    
    def analyze(self, template: LogTemplate, timestamps: List[datetime]) -> TimeSeriesAnalysis:
        """分析单个模板的时间序列"""
        result = TimeSeriesAnalysis(template_id=template.template_id)
        
        time_series = self.build_time_series(timestamps)
        result.time_series = time_series
        
        if not time_series or len(time_series) < self.config.min_data_points:
            return result
        
        values = [p.value for p in time_series]
        n = len(values)
        result.mean = sum(values) / n
        variance = sum((v - result.mean) ** 2 for v in values) / n
        result.std = math.sqrt(variance)
        
        # 检测频率激增
        spike_threshold = result.mean + self.config.freq_n_std * result.std
        for point in time_series:
            if point.value > spike_threshold:
                result.has_spike = True
                result.spike_timestamps.append(point.timestamp)
        
        # 检测频率消失
        zero_count = 0
        for i in range(len(time_series) - 1, -1, -1):
            if time_series[i].value == 0:
                zero_count += 1
            else:
                break
        
        if zero_count >= self.config.freq_m_periods and len(time_series) >= self.config.freq_m_periods:
            result.has_vanished = True
            result.vanished_since = time_series[-zero_count].timestamp
        
        # 检测周期性
        result.is_periodic, result.periodicity_hours = self._detect_periodicity(time_series)
        
        template.is_spike = result.has_spike
        template.is_vanished = result.has_vanished
        template.is_periodic = result.is_periodic
        
        return result
    
    def _detect_periodicity(self, time_series: List[TimeSeriesPoint]) -> Tuple[bool, Optional[float]]:
        """检测周期性（使用自相关分析）"""
        if len(time_series) < 24:
            return False, None
        
        values = [p.value for p in time_series]
        n = len(values)
        
        # 计算均值
        mean = sum(values) / n
        
        # 计算自相关系数
        autocorr = []
        for lag in range(1, min(n // 2, 48)):
            num = 0.0
            den = 0.0
            for i in range(n - lag):
                num += (values[i] - mean) * (values[i + lag] - mean)
            for i in range(n):
                den += (values[i] - mean) ** 2
            
            if den == 0:
                autocorr.append(0.0)
            else:
                autocorr.append(num / den)
        
        # 找峰值
        peaks = []
        for i in range(1, len(autocorr) - 1):
            if autocorr[i] > autocorr[i - 1] and autocorr[i] > autocorr[i + 1]:
                if autocorr[i] > 0.3:  # 自相关系数阈值
                    peaks.append((i + 1, autocorr[i]))
        
        if not peaks:
            return False, None
        
        # 取最强的峰
        peaks.sort(key=lambda x: x[1], reverse=True)
        best_lag, best_corr = peaks[0]
        
        # 检查是否是日周期或周周期
        if best_corr > 0.5:
            unit_seconds = self._unit_to_seconds.get(self.config.freq_unit, 3600)
            period_hours = best_lag * unit_seconds / 3600.0
            return True, period_hours
        
        return False, None
