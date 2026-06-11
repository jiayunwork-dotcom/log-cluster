"""性能剖析脚本"""
import cProfile
import pstats
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from log_cluster.config import load_config
from log_cluster.processor import LogProcessor

def main():
    config = load_config()
    processor = LogProcessor(config)
    
    # 生成10万行测试日志
    print("生成测试数据...")
    start = time.time()
    from datetime import datetime, timedelta
    import random
    
    templates = [
        ("INFO", "Connection from 192.168.1.{ip} port {port}", 1.0),
        ("INFO", "User user{user} logged in successfully", 0.5),
        ("INFO", "Request processed in {ms}ms for endpoint /api/users", 1.5),
        ("WARN", "High memory usage detected: {pct}%", 0.1),
        ("ERROR", "Database connection failed for host db{host}.example.com", 0.05),
    ]
    
    start_time = datetime(2024, 1, 15, 10, 0, 0)
    num_lines = 100000
    
    with open("/tmp/perf_bench.log", "w", encoding="utf-8") as f:
        for i in range(num_lines):
            level, template, _ = random.choice(templates)
            ts = start_time + timedelta(milliseconds=i * 10)
            msg = template.format(
                ip=random.randint(1, 254),
                port=random.randint(1024, 65535),
                user=random.randint(1, 10000),
                ms=random.randint(10, 1000),
                pct=random.randint(70, 95),
                host=random.randint(1, 10),
            )
            f.write(f"{ts.strftime('%Y-%m-%d %H:%M:%S')} {level} server01 {msg}\n")
    
    print(f"生成完成: {time.time() - start:.2f}秒")
    print("开始性能剖析...")
    
    # cProfile剖析
    profiler = cProfile.Profile()
    profiler.enable()
    
    start = time.time()
    result = processor.process_file("/tmp/perf_bench.log")
    elapsed = time.time() - start
    
    profiler.disable()
    
    print(f"\n处理 {result.total_logs} 行日志")
    print(f"耗时: {elapsed:.2f} 秒")
    print(f"速度: {result.total_logs / elapsed:,.0f} 行/秒")
    print(f"模板数: {len(result.templates)}")
    
    print("\n=== 热点函数 Top 20 ===")
    stats = pstats.Stats(profiler)
    stats.sort_stats("cumulative").print_stats(20)
    
    print("\n=== 按调用时间排序 Top 20 ===")
    stats.sort_stats("tottime").print_stats(20)


if __name__ == "__main__":
    main()
