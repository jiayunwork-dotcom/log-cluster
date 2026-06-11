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
    # ISO 8601 - 2024-01-15 10:00:00 or 2024-01-15T10:00:00
    (re.compile(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})"), "iso_basic"),
    # ISO with ms - 2024-01-15 10:00:00.123
    (re.compile(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\.\d+)"), "iso_ms"),
    # ISO with tz - 2024-01-15T10:00:00Z or +0800
    (re.compile(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))"), "iso_tz"),
    # Syslog RFC3164: "Jan  1 00:00:00"
    (re.compile(r"^([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})"), "syslog3164"),
    # Nginx/Common Log Format
    (re.compile(r"^(\[\d{2}/[A-Z][a-z]{2}/\d{4}:\d{2}:\d{2}:\d{2}\s+[+-]\d{4}\])"), "nginx"),
    # 数字时间戳
    (re.compile(r"^(\d{10}(?:\.\d+)?)"), "unix_timestamp"),
]

# 常用格式快速查找表
_FAST_STRPTIME_FORMATS = {
    # 标准格式
    "%Y-%m-%d %H:%M:%S": None,
    "%Y-%m-%dT%H:%M:%S": None,
    "%Y-%m-%d %H:%M:%S.%f": None,
    "%Y-%m-%dT%H:%M:%S.%f": None,
}

# 日志级别快速查找
_LEVEL_SET = {"DEBUG", "INFO", "WARN", "WARNING", "ERROR", "FATAL", "CRITICAL"}

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
    
    # 预编译常用正则
    _LEVEL_SOURCE_RE = re.compile(
        r"^(DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\s+(\S+)\s+",
        re.IGNORECASE,
    )
    _BRACKET_SOURCE_RE = re.compile(r"^\[([^\]]+)\]\s*")
    _COLON_SOURCE_RE = re.compile(r"^(\S+?):\s+")
    
    @staticmethod
    def _fast_parse_level_source(remaining: str) -> tuple:
        """快速解析级别和来源，不使用正则"""
        # 快速去掉前导空格
        s = remaining
        n = len(s)
        i = 0
        while i < n and s[i] == ' ':
            i += 1
        if i >= n:
            return "INFO", "", s
        
        # 解析第一个token（可能是级别）
        start = i
        while i < n and s[i] != ' ':
            i += 1
        tok1 = s[start:i]
        
        # 检查是否是级别词（直接比较，避免upper开销）
        tok1_upper = tok1.upper() if tok1 else ""
        is_level = tok1_upper in ("INFO", "ERROR", "WARN", "DEBUG", "FATAL", "WARNING", "CRITICAL")
        
        if is_level:
            # 规范化级别
            if tok1_upper == "WARNING":
                level = "WARN"
            elif tok1_upper == "CRITICAL":
                level = "FATAL"
            else:
                level = tok1_upper
            # 尝试获取来源（下一个token）
            src_start = i
            while src_start < n and s[src_start] == ' ':
                src_start += 1
            source = ""
            rest_start = n
            if src_start < n:
                if s[src_start] == '[':
                    # [source]格式
                    src_end = s.find(']', src_start + 1)
                    if src_end > 0:
                        source = s[src_start + 1:src_end]
                        rest_start = src_end + 1
                    else:
                        rest_start = src_start
                else:
                    # 普通source token（直到下一个空格）
                    src_end = src_start
                    while src_end < n and s[src_end] != ' ':
                        src_end += 1
                    source = s[src_start:src_end]
                    rest_start = src_end
            remaining_rest = s[rest_start:]
            return level, source, remaining_rest
        else:
            # 没有级别在开头，尝试匹配[source]
            if start < n and s[start] == '[':
                src_end = s.find(']', start + 1)
                if src_end > 0:
                    source = s[start + 1:src_end]
                    rest_start = src_end + 1
                    remaining_rest = s[rest_start:]
                    # 从剩余部分提取级别 - 这里用快速查找级别词
                    return None, source, remaining_rest
            # 尝试source:开头
            colon_pos = s.find(':', start)
            if colon_pos > 0 and colon_pos - start < 40:
                # 检查冒号后是否是空格或文本
                k = colon_pos + 1
                if k < n and s[k] == ' ':
                    # 前面的部分是否包含空格？不包含就是source
                    has_space = False
                    for ch_idx in range(start, colon_pos):
                        if s[ch_idx] == ' ':
                            has_space = True
                            break
                    if not has_space:
                        source = s[start:colon_pos]
                        remaining_rest = s[colon_pos + 1:]
                        return None, source, remaining_rest
            # 最后只提取级别 - 标记None让调用方用_extract_level
            return None, "", s
    
    def _parse_text(self, line: str) -> LogEntry:
        """解析纯文本日志（时间戳在行首）- 高性能版本"""
        timestamp = None
        remaining = line.strip()
        
        # 快速路径: 最常见的格式 YYYY-MM-DD HH:MM:SS
        rem_len = len(remaining)
        if (rem_len >= 19 
            and remaining[4] == '-' 
            and remaining[7] == '-' 
            and remaining[10] in (' ', 'T')
            and remaining[13] == ':'
            and remaining[16] == ':'):
            ts_str = remaining[:19]
            # 检查是否带毫秒
            ms_end = 19
            if rem_len > 19 and remaining[19] == '.' and rem_len >= 23:
                # 跳过数字和后缀(Z/+0800)
                i = 20
                while i < rem_len and (remaining[i].isdigit() or remaining[i] in 'Z+-:. '):
                    i += 1
                    if i - 19 > 15:
                        break
                ms_end = i
                ts_str = remaining[:ms_end]
            
            timestamp = self._fast_parse_iso_basic(ts_str[:19])
            if timestamp is None and ms_end > 19:
                timestamp = self._fast_parse_iso_ms(ts_str)
            # 手动lstrip(" -:")
            strip_idx = ms_end
            while strip_idx < rem_len and remaining[strip_idx] in ' -:':
                strip_idx += 1
            remaining = remaining[strip_idx:]
        else:
            # 其他格式: 回退到正则匹配
            ts_match = None
            ts_format = None
            for pattern, fmt in TIMESTAMP_PATTERNS:
                m = pattern.match(remaining)
                if m:
                    ts_match = m
                    ts_format = fmt
                    break
            
            if ts_match:
                ts_str = ts_match.group(1)
                timestamp = self._parse_timestamp(ts_str, ts_format)
                # 手动lstrip
                after = len(ts_match.group(0))
                rem_len2 = len(remaining)
                while after < rem_len2 and remaining[after] in ' -:':
                    after += 1
                remaining = remaining[after:]
        
        # 快速提取级别和来源（无正则）
        level, source, remaining = self._fast_parse_level_source(remaining)
        if level is None:
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
    
    @staticmethod
    def _fast_parse_iso_basic(ts_str: str) -> Optional[datetime]:
        """快速解析 ISO 基础格式: 2024-01-15 10:00:00 或 2024-01-15T10:00:00"""
        if len(ts_str) != 19:
            return None
        try:
            # 手动解析，比strptime快约2倍
            # YYYY-MM-DD HH:MM:SS or YYYY-MM-DDTHH:MM:SS
            if (ts_str[4] != '-' or ts_str[7] != '-' 
                or ts_str[13] != ':' or ts_str[16] != ':'):
                return None
            sep = ts_str[10]
            if sep not in ('T', ' '):
                return None
            return datetime(
                int(ts_str[0:4]),
                int(ts_str[5:7]),
                int(ts_str[8:10]),
                int(ts_str[11:13]),
                int(ts_str[14:16]),
                int(ts_str[17:19]),
            )
        except (ValueError, IndexError):
            return None
    
    @staticmethod
    def _fast_parse_iso_ms(ts_str: str) -> Optional[datetime]:
        """快速解析带毫秒的 ISO 格式"""
        if len(ts_str) < 21:
            return None
        try:
            dt_part = ts_str[:19]
            sep = dt_part[10]
            if sep not in ('T', ' '):
                return None
            # 提取微秒部分（最多6位）
            ms_str = ts_str[20:].rstrip('Z')
            if len(ms_str) > 6:
                ms_str = ms_str[:6]
            else:
                ms_str = ms_str.ljust(6, '0')
            return datetime(
                int(dt_part[0:4]),
                int(dt_part[5:7]),
                int(dt_part[8:10]),
                int(dt_part[11:13]),
                int(dt_part[14:16]),
                int(dt_part[17:19]),
                int(ms_str),
            )
        except (ValueError, IndexError):
            return None
    
    def _parse_timestamp(self, ts_str: str, hint: str = "") -> Optional[datetime]:
        """解析时间戳字符串 - 多路径快速优化版本"""
        if not ts_str:
            return None
        
        result = None
        
        # 用户自定义格式最高优先级
        if self.config.time_format:
            try:
                result = datetime.strptime(ts_str, self.config.time_format)
                if result is not None and result.tzinfo is not None:
                    result = result.replace(tzinfo=None)
                return result
            except ValueError:
                pass
        
        # 快速路径1: ISO基础格式 (最常见)
        if hint in ("iso_basic", ""):
            result = self._fast_parse_iso_basic(ts_str)
            if result is not None:
                return result
        
        # 快速路径2: ISO带毫秒格式
        if hint in ("iso_ms", "iso_tz", ""):
            result = self._fast_parse_iso_ms(ts_str)
            if result is not None:
                return result
        
        # 快速路径3: Unix时间戳
        if hint in ("unix_timestamp", "") and ts_str and (ts_str[0].isdigit() or (ts_str[0] == '-' and len(ts_str) > 1)):
            try:
                ts = float(ts_str)
                return datetime.fromtimestamp(ts)
            except (ValueError, OSError, OverflowError):
                pass
        
        # 快速路径4: Syslog 3164
        if hint in ("syslog3164", ""):
            try:
                now = datetime.now()
                parsed = datetime.strptime(ts_str, "%b %d %H:%M:%S")
                return parsed.replace(year=now.year)
            except ValueError:
                pass
        
        # 快速路径5: Nginx格式
        if hint in ("nginx", ""):
            try:
                ts_str_clean = ts_str.strip("[]")
                return datetime.strptime(ts_str_clean, "%d/%b/%Y:%H:%M:%S %z").replace(tzinfo=None)
            except ValueError:
                pass
        
        # 通用回退（仅在所有快速路径失败时使用）
        try:
            result = date_parser.parse(ts_str, fuzzy=True)
        except (ValueError, OverflowError):
            return None
        
        if result is not None and result.tzinfo is not None:
            result = result.replace(tzinfo=None)
        
        return result
    
    def _extract_level(self, text: str) -> str:
        """从文本中提取日志级别 - 快速路径优先"""
        # 快速扫描前60个字符（通常级别在开头）
        scan_len = min(len(text), 60)
        text_prefix = text[:scan_len]
        # 用大写查找
        text_prefix_upper = text_prefix.upper()
        
        # 查找所有级别位置，取最早出现的且是独立单词的
        # INFO的子串问题（如 "debug info"中的info作为后缀）所以先找长词优先
        level_order = ("WARNING", "CRITICAL", "DEBUG", "ERROR", "FATAL", "INFO", "WARN")
        best_pos = None
        best_level = None
        
        for level in level_order:
            pos = text_prefix_upper.find(level)
            while pos >= 0:
                # 确保是独立单词
                before_ok = (pos == 0) or (not text_prefix_upper[pos - 1].isalpha())
                after_pos = pos + len(level)
                after_ok = (after_pos >= len(text_prefix_upper)) or (not text_prefix_upper[after_pos].isalpha())
                if before_ok and after_ok:
                    if best_pos is None or pos < best_pos:
                        best_pos = pos
                        best_level = level
                    break  # 最早的即可
                # 继续找下一个位置
                pos = text_prefix_upper.find(level, pos + 1)
        
        if best_level is not None:
            if best_level == "WARNING":
                return "WARN"
            if best_level == "CRITICAL":
                return "FATAL"
            return best_level
        
        # 快速失败：如果快速扫描没找到，再用正则（更慢但全面）
        match = LEVEL_PATTERN.search(text)
        if match:
            return match.group(1).upper()
        return "INFO"
