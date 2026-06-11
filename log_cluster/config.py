"""配置模块 - 处理YAML配置文件和默认值"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


DEFAULT_CONFIG = {
    "drain": {
        "depth": 4,
        "st": 0.4,
        "max_child": 100,
        "sim_th": 0.4,
    },
    "correlate": {
        "window_size": 60,
        "min_support": 0.01,
        "min_confidence": 0.5,
        "min_lift": 2.0,
        "burst_threshold": 100,
        "min_count": 10,
    },
    "preprocess": {
        "patterns": [
            {"name": "IP", "regex": r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"},
            {"name": "UUID", "regex": r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"},
            {"name": "PATH", "regex": r"(?:/[\w\-.~]+)+\.?(?:[\w\-]+)?"},
            {"name": "NUM", "regex": r"\b\d+\b"},
            {"name": "EMAIL", "regex": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"},
        ],
        "order": ["IP", "UUID", "PATH", "NUM", "EMAIL"],
    },
    "input": {
        "format": "auto",
        "time_format": "",
        "custom_regex": "",
        "timestamp_group": "timestamp",
        "level_group": "level",
        "source_group": "source",
        "message_group": "message",
    },
    "anomaly": {
        "baseline_file": "",
        "freq_unit": "hour",
        "freq_n_std": 3.0,
        "freq_m_periods": 3,
        "min_data_points": 24,
    },
    "clustering": {
        "merge_threshold": 0.3,
    },
    "filters": {
        "whitelist": [],
        "focus": [],
    },
    "output": {
        "format": "terminal",
        "output_dir": "./output",
        "top_n": 20,
        "html_max_templates": 50,
    },
    "incremental": {
        "state_file": "./log-cluster-state.json",
        "max_templates": 10000,
        "rare_template_count": 5,
    },
    "parallel": {
        "workers": 0,
    },
    "alerts": [
        {
            "name": "high_error_templates",
            "condition": "error_template_count > 5",
            "severity": "critical",
            "message": "检测到 {error_template_count} 个ERROR模板，超过阈值5",
        },
        {
            "name": "spike_surge",
            "condition": "spike_count > 10",
            "severity": "warning",
            "message": "频率激增模板 {spike_count} 个，超过阈值10",
        },
        {
            "name": "slow_processing",
            "condition": "processing_speed < 50000",
            "severity": "info",
            "message": "处理速度 {processing_speed:.0f} 行/秒，低于5万行/秒",
        },
    ],
    "tags": [
        {
            "pattern": r"(?i)(error|exception|fail|fatal)",
            "tags": ["error/general"],
        },
        {
            "pattern": r"(?i)(timeout|timed.?out)",
            "tags": ["infra/network/timeout"],
        },
        {
            "pattern": r"(?i)(database|mysql|postgres|sql|connection.*refused)",
            "tags": ["infra/database/connection"],
        },
        {
            "pattern": r"(?i)(out.?of.?memory|oom|heap)",
            "tags": ["infra/resource/memory"],
        },
    ],
}


@dataclass
class DrainConfig:
    depth: int = 4
    st: float = 0.4
    max_child: int = 100
    sim_th: float = 0.4


@dataclass
class PreprocessPattern:
    name: str
    regex: str


@dataclass
class PreprocessConfig:
    patterns: List[PreprocessPattern] = field(default_factory=list)
    order: List[str] = field(default_factory=list)


@dataclass
class InputConfig:
    format: str = "auto"
    time_format: str = ""
    custom_regex: str = ""
    timestamp_group: str = "timestamp"
    level_group: str = "level"
    source_group: str = "source"
    message_group: str = "message"


@dataclass
class AnomalyConfig:
    baseline_file: str = ""
    freq_unit: str = "hour"
    freq_n_std: float = 3.0
    freq_m_periods: int = 3
    min_data_points: int = 24


@dataclass
class ClusteringConfig:
    merge_threshold: float = 0.3


@dataclass
class FiltersConfig:
    whitelist: List[str] = field(default_factory=list)
    focus: List[str] = field(default_factory=list)


@dataclass
class OutputConfig:
    format: str = "terminal"
    output_dir: str = "./output"
    top_n: int = 20
    html_max_templates: int = 50


@dataclass
class IncrementalConfig:
    state_file: str = "./log-cluster-state.json"
    max_templates: int = 10000
    rare_template_count: int = 5


@dataclass
class ParallelConfig:
    workers: int = 0


@dataclass
class CorrelateConfig:
    window_size: int = 60
    min_support: float = 0.01
    min_confidence: float = 0.5
    min_lift: float = 2.0
    burst_threshold: int = 100
    min_count: int = 10


@dataclass
class AlertRule:
    name: str
    condition: str
    severity: str = "info"
    message: str = ""


@dataclass
class TagRule:
    pattern: str
    tags: List[str] = field(default_factory=list)


@dataclass
class AppConfig:
    drain: DrainConfig = field(default_factory=DrainConfig)
    preprocess: PreprocessConfig = field(default_factory=PreprocessConfig)
    input: InputConfig = field(default_factory=InputConfig)
    anomaly: AnomalyConfig = field(default_factory=AnomalyConfig)
    clustering: ClusteringConfig = field(default_factory=ClusteringConfig)
    filters: FiltersConfig = field(default_factory=FiltersConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    incremental: IncrementalConfig = field(default_factory=IncrementalConfig)
    parallel: ParallelConfig = field(default_factory=ParallelConfig)
    correlate: CorrelateConfig = field(default_factory=CorrelateConfig)
    alerts: List[AlertRule] = field(default_factory=list)
    tags: List[TagRule] = field(default_factory=list)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """深度合并两个字典，override 覆盖 base 中的值"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """加载配置文件，合并默认配置
    
    Args:
        config_path: 配置文件路径，默认为当前目录下的 log-cluster.yaml
    
    Returns:
        AppConfig 配置对象
    """
    config_data = DEFAULT_CONFIG.copy()
    
    if config_path is None:
        config_path = "log-cluster.yaml"
    
    config_file = Path(config_path)
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        config_data = _deep_merge(config_data, user_config)
    
    return _dict_to_config(config_data)


def _dict_to_config(data: Dict[str, Any]) -> AppConfig:
    """将字典转换为配置对象"""
    drain_data = data.get("drain", {})
    preprocess_data = data.get("preprocess", {})
    input_data = data.get("input", {})
    anomaly_data = data.get("anomaly", {})
    clustering_data = data.get("clustering", {})
    filters_data = data.get("filters", {})
    output_data = data.get("output", {})
    incremental_data = data.get("incremental", {})
    parallel_data = data.get("parallel", {})
    correlate_data = data.get("correlate", {})
    alerts_data = data.get("alerts", [])
    tags_data = data.get("tags", [])
    
    patterns = []
    for p in preprocess_data.get("patterns", []):
        patterns.append(PreprocessPattern(name=p["name"], regex=p["regex"]))
    
    alert_rules = []
    for a in alerts_data:
        alert_rules.append(AlertRule(
            name=a["name"],
            condition=a["condition"],
            severity=a.get("severity", "info"),
            message=a.get("message", ""),
        ))
    
    tag_rules = []
    for t in tags_data:
        tag_rules.append(TagRule(
            pattern=t["pattern"],
            tags=t.get("tags", []),
        ))
    
    return AppConfig(
        drain=DrainConfig(
            depth=drain_data.get("depth", 4),
            st=drain_data.get("st", 0.4),
            max_child=drain_data.get("max_child", 100),
            sim_th=drain_data.get("sim_th", drain_data.get("st", 0.4)),
        ),
        preprocess=PreprocessConfig(
            patterns=patterns,
            order=preprocess_data.get("order", ["IP", "UUID", "PATH", "NUM", "EMAIL"]),
        ),
        input=InputConfig(
            format=input_data.get("format", "auto"),
            time_format=input_data.get("time_format", ""),
            custom_regex=input_data.get("custom_regex", ""),
            timestamp_group=input_data.get("timestamp_group", "timestamp"),
            level_group=input_data.get("level_group", "level"),
            source_group=input_data.get("source_group", "source"),
            message_group=input_data.get("message_group", "message"),
        ),
        anomaly=AnomalyConfig(
            baseline_file=anomaly_data.get("baseline_file", ""),
            freq_unit=anomaly_data.get("freq_unit", "hour"),
            freq_n_std=anomaly_data.get("freq_n_std", 3.0),
            freq_m_periods=anomaly_data.get("freq_m_periods", 3),
            min_data_points=anomaly_data.get("min_data_points", 24),
        ),
        clustering=ClusteringConfig(
            merge_threshold=clustering_data.get("merge_threshold", 0.3),
        ),
        filters=FiltersConfig(
            whitelist=filters_data.get("whitelist", []),
            focus=filters_data.get("focus", []),
        ),
        output=OutputConfig(
            format=output_data.get("format", "terminal"),
            output_dir=output_data.get("output_dir", "./output"),
            top_n=output_data.get("top_n", 20),
            html_max_templates=output_data.get("html_max_templates", 50),
        ),
        incremental=IncrementalConfig(
            state_file=incremental_data.get("state_file", "./log-cluster-state.json"),
            max_templates=incremental_data.get("max_templates", 10000),
            rare_template_count=incremental_data.get("rare_template_count", 5),
        ),
        parallel=ParallelConfig(
            workers=parallel_data.get("workers", 0),
        ),
        correlate=CorrelateConfig(
            window_size=correlate_data.get("window_size", 60),
            min_support=correlate_data.get("min_support", 0.01),
            min_confidence=correlate_data.get("min_confidence", 0.5),
            min_lift=correlate_data.get("min_lift", 2.0),
            burst_threshold=correlate_data.get("burst_threshold", 100),
            min_count=correlate_data.get("min_count", 10),
        ),
        alerts=alert_rules,
        tags=tag_rules,
    )


def override_config(config: AppConfig, overrides: Dict[str, Any]) -> AppConfig:
    """用命令行参数覆盖配置
    
    Args:
        config: 原始配置
        overrides: 覆盖参数字典，支持点号分隔的键名，如 "drain.depth"
    
    Returns:
        新的配置对象
    """
    config_dict = _config_to_dict(config)
    
    for key, value in overrides.items():
        if value is None:
            continue
        parts = key.split(".")
        d = config_dict
        for part in parts[:-1]:
            if part not in d:
                d[part] = {}
            d = d[part]
        d[parts[-1]] = value
    
    return _dict_to_config(config_dict)


def _config_to_dict(config: AppConfig) -> Dict[str, Any]:
    """将配置对象转换为字典"""
    return {
        "drain": {
            "depth": config.drain.depth,
            "st": config.drain.st,
            "max_child": config.drain.max_child,
            "sim_th": config.drain.sim_th,
        },
        "preprocess": {
            "patterns": [{"name": p.name, "regex": p.regex} for p in config.preprocess.patterns],
            "order": config.preprocess.order,
        },
        "input": {
            "format": config.input.format,
            "time_format": config.input.time_format,
            "custom_regex": config.input.custom_regex,
            "timestamp_group": config.input.timestamp_group,
            "level_group": config.input.level_group,
            "source_group": config.input.source_group,
            "message_group": config.input.message_group,
        },
        "anomaly": {
            "baseline_file": config.anomaly.baseline_file,
            "freq_unit": config.anomaly.freq_unit,
            "freq_n_std": config.anomaly.freq_n_std,
            "freq_m_periods": config.anomaly.freq_m_periods,
            "min_data_points": config.anomaly.min_data_points,
        },
        "clustering": {
            "merge_threshold": config.clustering.merge_threshold,
        },
        "filters": {
            "whitelist": config.filters.whitelist,
            "focus": config.filters.focus,
        },
        "output": {
            "format": config.output.format,
            "output_dir": config.output.output_dir,
            "top_n": config.output.top_n,
            "html_max_templates": config.output.html_max_templates,
        },
        "incremental": {
            "state_file": config.incremental.state_file,
            "max_templates": config.incremental.max_templates,
            "rare_template_count": config.incremental.rare_template_count,
        },
        "parallel": {
            "workers": config.parallel.workers,
        },
        "correlate": {
            "window_size": config.correlate.window_size,
            "min_support": config.correlate.min_support,
            "min_confidence": config.correlate.min_confidence,
            "min_lift": config.correlate.min_lift,
            "burst_threshold": config.correlate.burst_threshold,
            "min_count": config.correlate.min_count,
        },
        "alerts": [
            {
                "name": a.name,
                "condition": a.condition,
                "severity": a.severity,
                "message": a.message,
            }
            for a in config.alerts
        ],
        "tags": [
            {
                "pattern": t.pattern,
                "tags": t.tags,
            }
            for t in config.tags
        ],
    }
