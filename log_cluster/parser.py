"""日志解析模块 - 支持多种日志格式解析"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator, Optional

from dateutil import parser as date_parser

from .config import InputConfig


LOG_LEVELS = {
    "DEBUG", "INFO", "WARN", "WARNING", "ERROR", "FATAL", "CRITICAL",
    "debug", "info", "warn", "warning", "error", "fatal", "critical",
}

LEVEL_PATTERN = re.compile(
    r"\b(DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\b",
    re.IGNORECASE
)


@dataclass
class LogEntry:
    """单条日志记录"""
    timestamp: Optional[datetime]
    level: str
    source: str
    message: str
    raw: str = ""
    
    @property
    def timestamp_str(self) -> str:
        if self.timestamp is None:
            return "未知"
        return self.timestamp.isoformat()
    
    @property
    def normalized_level(self) -> str:
        """标准化日志级别为大写"""
        level_upper = self.level.upper()
        if level_upper == "WARNING":
            return "WARN"
        if level_upper == "CRITICAL":
            return "FATAL"
        return level_upper


# 常见时间戳格式
TIMESTAMP_PATTERNS = [
    # ISO 8601
    (re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "iso"),
    # Syslog RFC3164: "Jan  1 00:00:00"
    (re.compile(r"^[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}"), "syslog3164"),
    # Nginx/Common Log Format
    (re.compile(r"^\[\d{2}/[A-Z][a-z]{2}/\d{4}:\d{2}:\d{2}:\d{2}\s+[+-]\d{4}\]"), "nginx"),
    # 数字时间戳
    (re.compile(r"^\d{10}(?:\.\d+)?"), "unix_timestamp"),
]

# Syslog RFC5424 格式
SYSLOG5424_PATTERN = re.compile(
    r"^<(?P<prival>\d{1,3})>(?P<version>\d)\s+"
    r"(?P<timestamp>-|\S+)\s+"
    r"(?P<hostname>-|\S+)\s+"
    r"(?P<appname>-|\S+)\s+"
    r"(?P<procid>-|\S+)\s+"
    r"(?P<msgid>-|\S+)\s+"
    r"(?P<message>.*)$"
)

# Syslog RFC3164 格式
SYSLOG3164_PATTERN = re.compile(
    r"^(?P<timestamp>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"(?P<hostname>\S+)\s+"
    r"(?P<message>.*)$"
)


class LogParser:
    """日志解析器，支持多种格式"""
    
    def __init__(self, config: InputConfig):
        self.config = config
        self._custom_regex = None
        if config.custom_regex:
            self._custom_regex = re.compile(config.custom_regex)
    
    def parse_line(self, line: str) -> LogEntry:
        """解析单行日志"""
        line = line.rstrip("\n\r")
        if not line.strip():
            return LogEntry(timestamp=None, level="INFO", source="", message="", raw=line)
        
        fmt = self.config.format
        if fmt == "auto":
            return self._parse_auto(line)
        elif fmt == "json":
            return self._parse_json(line)
        elif fmt == "syslog":
            return self._parse_syslog(line)
        elif fmt == "text":
            return self._parse_text(line)
        elif fmt == "custom":
            return self._parse_custom(line)
        else:
            return self._parse_auto(line)
    
    def parse_lines(self, lines: Iterator[str]) -> Iterator[LogEntry]:
        """逐行解析日志"""
        for line in lines:
            entry = self.parse_line(line)
            if entry.message.strip():
                yield entry
    
    def _parse_auto(self, line: str) -> LogEntry:
        """自动识别格式"""
        stripped = line.strip()
        
        # 检测JSON
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                return self._parse_json(line)
            except Exception:
                pass
        
        # 检测Syslog RFC5424
        if stripped.startswith("<") and SYSLOG5424_PATTERN.match(stripped):
            try:
                return self._parse_syslog5424(stripped)
            except Exception:
                pass
        
        # 检测Syslog RFC3164
        if SYSLOG3164_PATTERN.match(stripped):
            try:
                return self._parse_syslog3164(stripped)
            except Exception:
                pass
        
        # 默认按纯文本解析
        return self._parse_text(line)
    
    def _parse_json(self, line: str) -> LogEntry:
        """解析JSON格式日志"""
        try:
            data = json.loads(line.strip())
        except json.JSONDecodeError:
            return LogEntry(timestamp=None, level="INFO", source="", message=line, raw=line)
        
        message = str(data.get("message", data.get("msg", data.get("content", line))))
        timestamp = None
        ts_str = data.get("timestamp", data.get("time", data.get("@timestamp", "")))
        if ts_str:
            timestamp = self._parse_timestamp(str(ts_str))
        
        level = str(data.get("level", data.get("log_level", "INFO")))
        source = str(data.get("hostname", data.get("service", data.get("source", ""))))
        
        return LogEntry(
            timestamp=timestamp,
            level=level,
            source=source,
            message=message,
            raw=line,
        )
    
    def _parse_syslog(self, line: str) -> LogEntry:
        """解析Syslog格式（自动识别3164/5424）"""
        stripped = line.strip()
        if SYSLOG5424_PATTERN.match(stripped):
            return self._parse_syslog5424(stripped)
        return self._parse_syslog3164(stripped)
    
    def _parse_syslog5424(self, line: str) -> LogEntry:
        """解析Syslog RFC5424格式"""
        match = SYSLOG5424_PATTERN.match(line)
        if not match:
            return LogEntry(timestamp=None, level="INFO", source="", message=line, raw=line)
        
        ts_str = match.group("timestamp")
        timestamp = None
        if ts_str != "-":
            timestamp = self._parse_timestamp(ts_str)
        
        hostname = match.group("hostname")
        if hostname == "-":
            hostname = ""
        appname = match.group("appname")
        if appname == "-":
            appname = ""
        source = f"{hostname}:{appname}" if appname else hostname
        
        message = match.group("message")
        level = self._extract_level(message)
        
        prival = int(match.group("prival"))
        severity = prival % 8
        if not level or level == "INFO":
            severity_map = {
                0: "FATAL", 1: "FATAL", 2: "FATAL",
                3: "ERROR", 4: "WARN", 5: "INFO",
                6: "INFO", 7: "DEBUG",
            }
            level = severity_map.get(severity, "INFO")
        
        return LogEntry(
            timestamp=timestamp,
            level=level,
            source=source,
            message=message,
            raw=line,
        )
    
    def _parse_syslog3164(self, line: str) -> LogEntry:
        """解析Syslog RFC3164格式"""
        match = SYSLOG3164_PATTERN.match(line)
        if not match:
            return LogEntry(timestamp=None, level="INFO", source="", message=line, raw=line)
        
        ts_str = match.group("timestamp")
        timestamp = self._parse_timestamp(ts_str)
        hostname = match.group("hostname")
        message = match.group("message")
        level = self._extract_level(message)
        
        return LogEntry(
            timestamp=timestamp,
            level=level,
            source=hostname,
            message=message,
            raw=line,
        )
    
    def _parse_text(self, line: str) -> LogEntry:
        """解析纯文本日志（时间戳在行首）"""
        timestamp = None
        remaining = line.strip()
        
        # 尝试匹配时间戳
        ts_match = None
        ts_format = None
        for pattern, fmt in TIMESTAMP_PATTERNS:
            match = pattern.match(remaining)
            if match:
                ts_match = match
                ts_format = fmt
                break
        
        if ts_match:
            ts_str = ts_match.group(0)
            timestamp = self._parse_timestamp(ts_str, ts_format)
            remaining = remaining[len(ts_str):].lstrip(" -:")
        
        # 提取日志级别和来源
        level = "INFO"
        source = ""
        
        # 先尝试匹配级别+来源的模式（如 "INFO server01 message"）
        level_source_match = re.match(r"^(DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\s+(\S+)\s+", remaining, re.IGNORECASE)
        if level_source_match:
            level = level_source_match.group(1).upper()
            source = level_source_match.group(2)
            remaining = remaining[len(level_source_match.group(0)):]
        else:
            # 尝试匹配 [source] 格式
            source_match = re.match(r"^\[([^\]]+)\]\s*", remaining)
            if source_match:
                source = source_match.group(1)
                remaining = remaining[len(source_match.group(0)):]
                level = self._extract_level(remaining)
            else:
                # 尝试匹配 source: 格式
                source_match = re.match(r"^(\S+?):\s+", remaining)
                if source_match:
                    source = source_match.group(1)
                    remaining = remaining[len(source_match.group(0)):]
                # 提取级别
                level = self._extract_level(remaining)
        
        return LogEntry(
            timestamp=timestamp,
            level=level,
            source=source,
            message=remaining.strip(),
            raw=line,
        )
    
    def _parse_custom(self, line: str) -> LogEntry:
        """使用自定义正则解析"""
        if not self._custom_regex:
            return self._parse_auto(line)
        
        match = self._custom_regex.match(line.strip())
        if not match:
            return LogEntry(timestamp=None, level="INFO", source="", message=line, raw=line)
        
        groups = match.groupdict()
        
        timestamp = None
        ts_str = groups.get(self.config.timestamp_group, "")
        if ts_str:
            timestamp = self._parse_timestamp(ts_str)
        
        level = groups.get(self.config.level_group, "INFO")
        source = groups.get(self.config.source_group, "")
        message = groups.get(self.config.message_group, line)
        
        return LogEntry(
            timestamp=timestamp,
            level=level,
            source=source,
            message=message,
            raw=line,
        )
    
    def _parse_timestamp(self, ts_str: str, hint: str = "") -> Optional[datetime]:
        """解析时间戳字符串"""
        if not ts_str:
            return None
        
        result = None
        
        if self.config.time_format:
            try:
                result = datetime.strptime(ts_str, self.config.time_format)
            except ValueError:
                pass
        
        if result is None and hint == "unix_timestamp":
            try:
                ts = float(ts_str)
                result = datetime.fromtimestamp(ts)
            except (ValueError, OSError):
                pass
        
        if result is None and hint == "syslog3164":
            try:
                from datetime import datetime as dt
                now = dt.now()
                parsed = dt.strptime(ts_str, "%b %d %H:%M:%S")
                result = parsed.replace(year=now.year)
            except ValueError:
                pass
        
        if result is None and hint == "nginx":
            try:
                ts_str_clean = ts_str.strip("[]")
                result = datetime.strptime(ts_str_clean, "%d/%b/%Y:%H:%M:%S %z")
            except ValueError:
                pass
        
        if result is None:
            try:
                result = date_parser.parse(ts_str, fuzzy=True)
            except (ValueError, OverflowError):
                return None
        
        # 统一转换为naive datetime（移除时区信息，转为本地时间）
        if result is not None and result.tzinfo is not None:
            result = result.replace(tzinfo=None)
        
        return result
    
    def _extract_level(self, text: str) -> str:
        """从文本中提取日志级别"""
        match = LEVEL_PATTERN.search(text)
        if match:
            return match.group(1).upper()
        return "INFO"
