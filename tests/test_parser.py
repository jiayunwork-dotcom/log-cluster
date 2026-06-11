"""单元测试 - 日志解析模块"""
import unittest
from datetime import datetime

from log_cluster.config import InputConfig
from log_cluster.parser import LogParser, LogEntry


class TestLogParser(unittest.TestCase):
    """测试日志解析器"""
    
    def setUp(self):
        config = InputConfig(format="auto")
        self.parser = LogParser(config)
    
    def test_parse_plain_text(self):
        """测试纯文本日志解析"""
        line = "2024-01-15 10:00:01 INFO server01 Test message"
        entry = self.parser.parse_line(line)
        
        self.assertIsInstance(entry, LogEntry)
        self.assertIsNotNone(entry.timestamp)
        self.assertEqual(entry.level, "INFO")
        self.assertEqual(entry.source, "server01")
        self.assertIn("Test message", entry.message)
    
    def test_parse_json(self):
        """测试JSON格式日志解析"""
        line = '{"timestamp": "2024-01-15T10:00:01Z", "level": "ERROR", "hostname": "server01", "message": "Test error"}'
        entry = self.parser.parse_line(line)
        
        self.assertIsNotNone(entry.timestamp)
        self.assertEqual(entry.normalized_level, "ERROR")
        self.assertEqual(entry.source, "server01")
        self.assertEqual(entry.message, "Test error")
    
    def test_parse_syslog5424(self):
        """测试Syslog RFC5424格式"""
        line = "<165>1 2024-01-15T10:00:01Z server01 app 1234 ID1 Test syslog message"
        entry = self.parser.parse_line(line)
        
        self.assertIsNotNone(entry.timestamp)
        self.assertEqual(entry.source, "server01:app")
        self.assertIn("Test syslog message", entry.message)
    
    def test_parse_syslog3164(self):
        """测试Syslog RFC3164格式"""
        line = "Jan 15 10:00:01 server01 test: Test message"
        entry = self.parser.parse_line(line)
        
        self.assertIsNotNone(entry.timestamp)
        self.assertEqual(entry.source, "server01")
        self.assertIn("Test message", entry.message)
    
    def test_extract_level(self):
        """测试日志级别提取"""
        test_cases = [
            ("This is an ERROR message", "ERROR"),
            ("WARN: something wrong", "WARN"),
            ("debug info here", "DEBUG"),
            ("FATAL error occurred", "FATAL"),
            ("normal message", "INFO"),
        ]
        
        for text, expected in test_cases:
            level = self.parser._extract_level(text)
            self.assertEqual(level, expected, f"Failed for: {text}")
    
    def test_unknown_timestamp(self):
        """测试无法识别时间戳的情况"""
        line = "just a simple log message without timestamp"
        entry = self.parser.parse_line(line)
        
        self.assertIsNone(entry.timestamp)
        self.assertEqual(entry.timestamp_str, "未知")
        self.assertIn("simple log", entry.message)
    
    def test_empty_line(self):
        """测试空行"""
        entry = self.parser.parse_line("")
        self.assertEqual(entry.message, "")
        
        entry = self.parser.parse_line("   ")
        self.assertEqual(entry.message, "")
    
    def test_normalized_level(self):
        """测试级别标准化"""
        test_cases = [
            ("debug", "DEBUG"),
            ("INFO", "INFO"),
            ("warning", "WARN"),
            ("ERROR", "ERROR"),
            ("CRITICAL", "FATAL"),
        ]
        
        for level, expected in test_cases:
            entry = LogEntry(timestamp=None, level=level, source="", message="")
            self.assertEqual(entry.normalized_level, expected)


if __name__ == "__main__":
    unittest.main()
