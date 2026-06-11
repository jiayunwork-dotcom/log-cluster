"""单元测试 - Drain算法"""
import unittest

from log_cluster.config import DrainConfig, PreprocessConfig, PreprocessPattern
from log_cluster.drain import Drain, LogPreprocessor, LogTemplate


class TestDrain(unittest.TestCase):
    """测试Drain算法"""
    
    def setUp(self):
        preprocess_config = PreprocessConfig(
            patterns=[
                PreprocessPattern(name="IP", regex=r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
                PreprocessPattern(name="NUM", regex=r"\b\d+\b"),
            ],
            order=["IP", "NUM"],
        )
        self.preprocessor = LogPreprocessor(preprocess_config)
        
        drain_config = DrainConfig(depth=4, st=0.4, max_child=100, sim_th=0.4)
        self.drain = Drain(drain_config, self.preprocessor)
    
    def test_preprocess_ip(self):
        """测试IP地址替换"""
        msg = "Connection from 192.168.1.1 port 22"
        processed = self.preprocessor.preprocess(msg)
        self.assertIn("<IP>", processed)
        self.assertNotIn("192.168.1.1", processed)
    
    def test_preprocess_num(self):
        """测试数字替换"""
        msg = "User 12345 logged in"
        processed = self.preprocessor.preprocess(msg)
        self.assertIn("<NUM>", processed)
    
    def test_tokenize(self):
        """测试分词"""
        msg = "Connection from 192.168.1.1 port 22"
        tokens = self.preprocessor.tokenize(msg)
        self.assertIsInstance(tokens, list)
        self.assertGreater(len(tokens), 0)
    
    def test_add_log_message(self):
        """测试添加日志消息"""
        from log_cluster.parser import LogEntry
        from datetime import datetime
        
        entry = LogEntry(
            timestamp=datetime.now(),
            level="INFO",
            source="server01",
            message="Connection from 192.168.1.1 port 22",
        )
        
        template = self.drain.add_log_message(entry)
        self.assertIsInstance(template, LogTemplate)
        self.assertGreater(template.stats.count, 0)
    
    def test_similar_messages_same_template(self):
        """测试相似消息归入同一模板"""
        from log_cluster.parser import LogEntry
        from datetime import datetime
        
        messages = [
            "Connection from 192.168.1.1 port 22",
            "Connection from 10.0.0.1 port 8080",
            "Connection from 172.16.0.1 port 443",
        ]
        
        for msg in messages:
            entry = LogEntry(
                timestamp=datetime.now(),
                level="INFO",
                source="server01",
                message=msg,
            )
            self.drain.add_log_message(entry)
        
        # 应该只有1个模板（因为IP和端口都被替换了）
        self.assertEqual(self.drain.get_template_count(), 1)
    
    def test_different_messages_different_templates(self):
        """测试不同消息创建不同模板"""
        from log_cluster.parser import LogEntry
        from datetime import datetime
        
        messages = [
            "Connection from 192.168.1.1 port 22",
            "User login successful",
            "Error connecting to database",
        ]
        
        for msg in messages:
            entry = LogEntry(
                timestamp=datetime.now(),
                level="INFO",
                source="server01",
                message=msg,
            )
            self.drain.add_log_message(entry)
        
        # 应该有多个模板
        self.assertGreater(self.drain.get_template_count(), 1)
    
    def test_get_templates_sorted(self):
        """测试按频率排序获取模板"""
        from log_cluster.parser import LogEntry
        from datetime import datetime
        
        # 添加多个相同的消息
        for i in range(10):
            entry = LogEntry(
                timestamp=datetime.now(),
                level="INFO",
                source="server01",
                message=f"Common message {i}",
            )
            self.drain.add_log_message(entry)
        
        # 添加一个罕见的消息
        entry = LogEntry(
            timestamp=datetime.now(),
            level="INFO",
            source="server01",
            message="Rare unique message xyz",
        )
        self.drain.add_log_message(entry)
        
        templates = self.drain.get_templates_sorted()
        self.assertIsInstance(templates, list)
        self.assertGreater(len(templates), 0)
        # 第一个应该是频率最高的
        self.assertGreaterEqual(
            templates[0].stats.count,
            templates[-1].stats.count,
        )
    
    def test_merge_templates(self):
        """测试模板合并"""
        from log_cluster.parser import LogEntry
        from datetime import datetime
        
        # 先加一些消息让模板被创建
        for ip_suffix in range(10):
            entry = LogEntry(
                timestamp=datetime.now(),
                level="INFO",
                source="server01",
                message=f"Connection from 192.168.1.{ip_suffix} port 22",
            )
            self.drain.add_log_message(entry)
        
        initial_count = self.drain.get_template_count()
        
        # 合并模板
        self.drain.merge_templates()
        
        # 合并后模板数应该减少或不变
        self.assertLessEqual(self.drain.get_template_count(), initial_count)
    
    def test_error_template_detection(self):
        """测试错误模板检测"""
        from log_cluster.parser import LogEntry
        from datetime import datetime
        
        entry = LogEntry(
            timestamp=datetime.now(),
            level="ERROR",
            source="server01",
            message="Something went wrong",
        )
        
        template = self.drain.add_log_message(entry)
        self.assertTrue(template.is_error)


class TestLogPreprocessor(unittest.TestCase):
    """测试日志预处理器"""
    
    def setUp(self):
        config = PreprocessConfig(
            patterns=[
                PreprocessPattern(name="IP", regex=r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
                PreprocessPattern(name="UUID", regex=r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
                PreprocessPattern(name="NUM", regex=r"\b\d+\b"),
            ],
            order=["IP", "UUID", "NUM"],
        )
        self.preprocessor = LogPreprocessor(config)
    
    def test_preprocess_order(self):
        """测试预处理顺序 - IP应该先于NUM被替换"""
        msg = "Connection from 192.168.1.1 port 22"
        processed = self.preprocessor.preprocess(msg)
        
        # IP应该被整体替换，而不是数字被分别替换
        self.assertIn("<IP>", processed)
        self.assertNotIn("192", processed)


if __name__ == "__main__":
    unittest.main()
