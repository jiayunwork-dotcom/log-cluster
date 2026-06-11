"""生成大量测试日志的生成器"""
import random
from datetime import datetime, timedelta


def generate_sample_logs(output_path: str, num_lines: int = 100000):
    """生成测试日志
    
    生成指定数量的测试日志，用于性能测试
    """
    templates = [
        ("INFO", "Connection from {ip} port {port}", 1.0),
        ("INFO", "User {user} logged in successfully", 0.5),
        ("INFO", "Request processed in {ms}ms for endpoint {path}", 1.5),
        ("WARN", "High memory usage detected: {pct}%", 0.1),
        ("ERROR", "Database connection failed for host {host}", 0.05),
        ("INFO", "Starting application version {ver}", 0.02),
        ("DEBUG", "Cache {hit_miss} for key {key}", 0.3),
        ("WARN", "Disk space low on {device}: {pct}% used", 0.05),
        ("ERROR", "Null pointer exception at {module}", 0.03),
        ("INFO", "Backup completed successfully to {path}", 0.02),
    ]
    
    ips = [f"192.168.1.{i}" for i in range(1, 101)]
    users = [f"user{i}" for i in range(1, 1000)]
    paths = ["/api/users", "/api/orders", "/api/products", "/api/health", "/api/status"]
    hosts = ["db01.example.com", "db02.example.com", "cache01.example.com"]
    devices = ["/dev/sda1", "/dev/sdb1", "/dev/sdc1"]
    modules = ["com.example.Service.process", "com.example.Controller.handle", "com.example.Utils.compute"]
    
    start_time = datetime(2024, 1, 15, 10, 0, 0)
    
    with open(output_path, "w", encoding="utf-8") as f:
        for i in range(num_lines):
            level, template, weight = random.choice(templates)
            ts = start_time + timedelta(seconds=i * 0.1)
            
            msg = template.format(
                ip=random.choice(ips),
                port=random.randint(1024, 65535),
                user=random.choice(users),
                ms=random.randint(10, 1000),
                path=random.choice(paths),
                pct=random.randint(70, 95),
                host=random.choice(hosts),
                ver=f"{random.randint(1, 3)}.{random.randint(0, 9)}.{random.randint(0, 20)}",
                hit_miss=random.choice(["hit", "miss"]),
                key=f"user_profile_{random.randint(1, 100000)}",
                device=random.choice(devices),
                module=random.choice(modules),
            )
            
            line = f"{ts.strftime('%Y-%m-%d %H:%M:%S')} {level} server01 {msg}\n"
            f.write(line)
    
    print(f"已生成 {num_lines:,} 行日志到 {output_path}")


if __name__ == "__main__":
    generate_sample_logs("perf_test.log", 100000)
