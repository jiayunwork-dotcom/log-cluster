import os
import sys
import tempfile
import shutil
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from log_cluster.config import AppConfig, load_config
from log_cluster.drain import Drain, LogTemplate, TemplateEvent, LogPreprocessor
from log_cluster.parser import LogParser, LogEntry
from log_cluster.tags import TagEngine
from log_cluster.alerts import AlertEngine, AlertContext, TriggeredAlert
from log_cluster.parallel import ParallelProcessor


def test_alert_engine():
    print("=" * 60)
    print("🧪 测试1: 告警规则引擎")
    print("=" * 60)
    config = load_config()
    alert_engine = AlertEngine(config)
    ctx = AlertContext(
        new_template_count=15,
        error_template_count=8,
        spike_count=12,
        total_templates=42,
        processing_speed=20000,
    )
    triggered = alert_engine.evaluate(ctx)
    print(f"触发告警数: {len(triggered)}")
    for a in triggered:
        print(f"  - [{a.severity.upper()}] {a.name}: {a.message}")
    assert len(triggered) >= 3, f"应该至少触发3条内置规则，实际触发{len(triggered)}"
    print("✅ 告警引擎测试通过！\n")


def test_tag_engine():
    print("=" * 60)
    print("🧪 测试2: 标签系统")
    print("=" * 60)
    config = load_config()
    tag_engine = TagEngine(config)
    template1 = LogTemplate(
        template_id="T001",
        template_str="ERROR Database connection failed, retrying...",
        tokens=["ERROR", "Database", "connection", "failed", "retrying"],
        created_at=datetime.now(),
    )
    template2 = LogTemplate(
        template_id="T002",
        template_str="INFO User login successful",
        tokens=["INFO", "User", "login", "successful"],
        created_at=datetime.now(),
    )
    template3 = LogTemplate(
        template_id="T003",
        template_str="FATAL Out of memory exception",
        tokens=["FATAL", "Out", "of", "memory", "exception"],
        created_at=datetime.now(),
    )
    templates = [template1, template2, template3]
    tag_engine.apply_tags(templates)
    for t in templates:
        print(f"{t.template_id}: tags={t.tags}")
    assert len(template1.tags) >= 1, "模板1应该匹配到标签"
    assert len(template3.tags) >= 1, "模板3应该匹配到标签"
    infra_templates = list(tag_engine.filter_by_tag(templates, "infra"))
    print(f"过滤infra标签: {len(infra_templates)}个模板")
    print("✅ 标签系统测试通过！\n")


def test_event_system():
    print("=" * 60)
    print("🧪 测试3: 模板演化事件系统")
    print("=" * 60)
    config = load_config()
    preprocessor = LogPreprocessor(config.preprocess)
    drain = Drain(config.drain, preprocessor)
    parser = LogParser(config.input)
    test_logs = [
        "2024-01-01 10:00:00 ERROR Database connection failed",
        "2024-01-01 10:00:01 INFO User login successful",
        "2024-01-01 10:00:02 FATAL Out of memory exception",
        "2024-01-01 10:00:03 ERROR Database connection failed, retrying",
        "2024-01-01 10:00:04 WARN Disk space low",
    ]
    for log in test_logs:
        entry = parser.parse_line(log)
        if entry:
            drain.add_log_message(entry)
    print(f"处理{len(test_logs)}条日志，模板数: {len(drain.templates)}, 事件数: {len(drain.events)}")
    create_events = [e for e in drain.events if e.event_type == "created"]
    merge_events = [e for e in drain.events if e.event_type == "merged"]
    print(f"创建事件: {len(create_events)}, 合并事件: {len(merge_events)}")
    for e in drain.events[:5]:
        print(f"  [{e.event_type}] template={e.template_id} details={e.details[:50] if e.details else ''}")
    assert len(create_events) >= 3, f"应该有多个创建事件，实际{len(create_events)}"
    event_dicts = [e.to_dict() for e in drain.events]
    for i, e in enumerate(event_dicts[:3]):
        print(f"  事件{i}: {e['event_type']} template={e.get('template_id','')}")
    if drain.events:
        restored = TemplateEvent.from_dict(event_dicts[0])
        assert restored.template_id == drain.events[0].template_id
        assert restored.event_type == drain.events[0].event_type
    print("✅ 事件系统测试通过！\n")


def test_parallel():
    print("=" * 60)
    print("🧪 测试4: 并行处理模块")
    print("=" * 60)
    config = load_config()
    tmpdir = tempfile.mkdtemp()
    files = []
    total_logs_per_file = 20
    num_files = 3
    for i in range(num_files):
        fpath = os.path.join(tmpdir, f"test_{i}.log")
        with open(fpath, "w") as f:
            for j in range(total_logs_per_file):
                ts = f"2024-01-01 10:{i:02d}:{j:02d}"
                f.write(f"{ts} INFO Service started {j}\n")
                f.write(f"{ts} INFO Processing request id={1000 + j}\n")
                f.write(f"{ts} ERROR Database timeout {j % 5}\n")
                if j % 2 == 0:
                    f.write(f"{ts} WARN High memory usage\n")
        files.append(fpath)
    parallel = ParallelProcessor(config)
    from multiprocessing import cpu_count
    print(f"临时日志文件: {num_files}个, 每个{total_logs_per_file}条, CPU核心={cpu_count()}")
    result_drain, total, timestamps, offsets, duration = parallel.process_files_parallel(
        files,
        existing_drain=None,
        file_offsets={},
        workers=0,
    )
    expected_total = num_files * total_logs_per_file * 4 - num_files * total_logs_per_file // 2
    print(f"合并后模板数: {len(result_drain.templates)}")
    print(f"总日志数: {total}")
    print(f"总耗时: {duration:.3f}s")
    assert len(result_drain.templates) >= 3, f"应该识别出多个模板，实际{len(result_drain.templates)}"
    drain_dict = result_drain.to_dict()
    test_preprocessor = LogPreprocessor(config.preprocess)
    restored = Drain.from_dict(drain_dict, test_preprocessor)
    assert len(restored.templates) == len(result_drain.templates), "模板数不一致"
    assert len(restored.events) == len(result_drain.events), "事件数不一致"
    print(f"  序列化验证通过: {len(restored.templates)}模板, {len(restored.events)}事件")
    shutil.rmtree(tmpdir)
    print("✅ 并行处理模块测试通过！\n")


def test_alert_serialization():
    print("=" * 60)
    print("🧪 测试5: TriggeredAlert序列化")
    print("=" * 60)
    config = load_config()
    alert_engine = AlertEngine(config)
    ctx = AlertContext(
        new_template_count=10,
        error_template_count=7,
        spike_count=11,
        total_templates=50,
        processing_speed=30000,
    )
    triggered = alert_engine.evaluate(ctx)
    for a in triggered:
        d = a.to_dict()
        restored = TriggeredAlert.from_dict(d)
        assert restored.name == a.name
        assert restored.severity == a.severity
        assert restored.message == a.message
    print(f"  序列化{len(triggered)}条告警，全部验证通过")
    print("✅ TriggeredAlert序列化通过！\n")


def main():
    try:
        test_alert_engine()
        test_tag_engine()
        test_event_system()
        test_parallel()
        test_alert_serialization()
        print("=" * 60)
        print("🎉 所有新功能测试通过！")
        print("=" * 60)
        return 0
    except AssertionError as e:
        print(f"❌ 断言失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
