"""简单性能测试（无profiler开销）"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from log_cluster.config import load_config
from log_cluster.processor import LogProcessor

def main():
    config = load_config()
    processor = LogProcessor(config)
    
    # 生成测试日志
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
    
    with open("/tmp/speed_bench.log", "w", encoding="utf-8") as f:
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
    print(f"开始处理 {num_lines} 行日志...")
    
    # 预热
    processor.config.output.show_progress = False
    processor.process_file("/tmp/speed_bench.log")
    
    # 正式测试
    times = []
    for run in range(3):
        start = time.time()
        result = processor.process_file("/tmp/speed_bench.log")
        elapsed = time.time() - start
        speed = result.total_logs / elapsed
        times.append(speed)
        print(f"  运行 {run+1}: {elapsed:.2f}秒, 速度: {speed:,.0f} 行/秒, 模板数: {len(result.templates)}")
    
    avg_speed = sum(times) / len(times)
    print(f"\n平均速度: {avg_speed:,.0f} 行/秒")
    print(f"预计百万行耗时: {1_000_000 / avg_speed:.1f}秒")
    
    # 与目标对比
    target = 100_000
    ratio = avg_speed / target * 100
    print(f"目标: {target:,} 行/秒, 达成: {ratio:.1f}%")

if __name__ == "__main__":
    main()
