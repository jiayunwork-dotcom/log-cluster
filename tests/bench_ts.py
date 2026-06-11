"""对比：是否收集时间戳的性能差异"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from log_cluster.config import load_config
from log_cluster.processor import LogProcessor, ProcessResult
from log_cluster.drain import Drain
from datetime import datetime, timedelta
import random
import types

def make_logs(num_lines=1000000):
    path = f"/tmp/bench_{num_lines}.log"
    if os.path.exists(path) and os.path.getsize(path) > 10_000_000:
        return path
    templates = [
        ("INFO", "Connection from 192.168.1.{ip} port {port}"),
        ("INFO", "User user{user} logged in successfully"),
        ("INFO", "Request processed in {ms}ms for endpoint /api/users"),
    ]
    start_time = datetime(2024,1,15,10)
    print(f"生成{num_lines}行日志...")
    with open(path, "w") as f:
        for i in range(num_lines):
            level, tmpl = random.choice(templates)
            ts = start_time + timedelta(milliseconds=i*10)
            msg = tmpl.format(ip=random.randint(1,254), port=random.randint(1024,65535),
                             user=random.randint(1,10000), ms=random.randint(10,1000))
            f.write(f"{ts.strftime('%Y-%m-%d %H:%M:%S')} {level} server01 {msg}\n")
    return path

def main():
    config = load_config()
    path = make_logs(1000000)
    
    print("\n=== A. 完整收集时间戳（默认） ===")
    processor = LogProcessor(config)
    start = time.time()
    result = processor.process_file(path)
    elapsed = time.time()-start
    print(f"  耗时: {elapsed:.1f}秒, 速度: {result.total_logs/elapsed:,.0f} 行/秒")
    print(f"  收集了{sum(len(v) for v in result.template_timestamps.values()):,}个时间戳")
    
    print("\n=== B. 不收集时间戳 ===")
    processor2 = LogProcessor(config)
    # 替换process_file，跳过时间戳收集
    def fast_process(self, file_path, incremental=False, load_state=False):
        start_time = time.time()
        drain = Drain(self.config.drain, self.preprocessor)
        total_logs = 0
        tt = {}
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            parse_line = self.parser.parse_line
            add_log = drain.add_log_message
            for line in f:
                if not line or not line.strip():
                    continue
                entry = parse_line(line)
                if not entry.message or not entry.message.strip():
                    continue
                template = add_log(entry)
                total_logs += 1
        drain.merge_templates()
        templates = drain.get_templates_sorted()
        clusters = self.clustering.cluster(templates)
        ar = self.anomaly_detector.detect(templates, {})
        return ProcessResult(total_logs=total_logs, templates=templates, clusters=clusters,
                            anomaly_report=ar, template_timestamps=tt,
                            duration=time.time()-start_time, drain=drain, file_offsets={})
    processor2.process_file = types.MethodType(fast_process, processor2)
    
    start = time.time()
    result2 = processor2.process_file(path)
    elapsed2 = time.time()-start
    print(f"  耗时: {elapsed2:.1f}秒, 速度: {result2.total_logs/elapsed2:,.0f} 行/秒")
    print(f"  提升: {elapsed/elapsed2:.1f}x")
    
    print("\n=== C. 使用模板对象引用收集时间戳（避免dict.get） ===")
    processor3 = LogProcessor(config)
    def fast_process2(self, file_path, incremental=False, load_state=False):
        start_time = time.time()
        drain = Drain(self.config.drain, self.preprocessor)
        total_logs = 0
        # 用template对象的id作为key，但预先缓存引用
        tt = {}
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            parse_line = self.parser.parse_line
            add_log = drain.add_log_message
            for line in f:
                if not line or not line.strip():
                    continue
                entry = parse_line(line)
                if not entry.message or not entry.message.strip():
                    continue
                template = add_log(entry)
                total_logs += 1
                ts = entry.timestamp
                if ts is not None:
                    # 直接在模板上缓存list引用，避免字典查找
                    cached = getattr(template, "_ts_cache", None)
                    if cached is None:
                        cached = []
                        object.__setattr__(template, "_ts_cache", cached)
                        tt[template.template_id] = cached
                    cached.append(ts)
        drain.merge_templates()
        templates = drain.get_templates_sorted()
        clusters = self.clustering.cluster(templates)
        ar = self.anomaly_detector.detect(templates, tt)
        return ProcessResult(total_logs=total_logs, templates=templates, clusters=clusters,
                            anomaly_report=ar, template_timestamps=tt,
                            duration=time.time()-start_time, drain=drain, file_offsets={})
    processor3.process_file = types.MethodType(fast_process2, processor3)
    start = time.time()
    result3 = processor3.process_file(path)
    elapsed3 = time.time()-start
    print(f"  耗时: {elapsed3:.1f}秒, 速度: {result3.total_logs/elapsed3:,.0f} 行/秒")

if __name__ == "__main__":
    main()
