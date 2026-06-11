"""命令行接口 - 使用Typer实现"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import __version__
from .ci import compare_templates, print_diff_report
from .config import AppConfig, CorrelateConfig, load_config, override_config
from .correlation import (
    AssociationRule,
    CausalChain,
    CorrelationAnalyzer,
    CorrelationResult,
    CorrelationState,
    load_correlation_state,
    save_correlation_state,
)
from .processor import LogProcessor
from .reporter import HtmlReporter, JsonReporter, TerminalReporter


app = typer.Typer(
    add_completion=False,
    help="日志模式自动聚类与异常模板发现工具",
    no_args_is_help=True,
)


def _get_config(config_path: Optional[str], overrides: dict) -> AppConfig:
    """加载并覆盖配置"""
    config = load_config(config_path)
    config = override_config(config, overrides)
    return config


def _process_and_report(
    processor: LogProcessor,
    result,
    output_format: str,
    output_dir: str,
    config: AppConfig,
):
    """处理结果并生成报告"""
    # 终端输出
    if output_format == "terminal" or output_format == "all":
        reporter = TerminalReporter(config.output)
        reporter.print_summary(
            total_logs=result.total_logs,
            total_templates=len(result.templates),
            total_clusters=len(result.clusters),
            duration=result.duration,
        )
        # 告警（醒目显示）
        if result.triggered_alerts:
            reporter.print_triggered_alerts(result.triggered_alerts)
        reporter.print_top_templates(result.templates)
        reporter.print_clusters(result.clusters)
        # 模板演化区域
        if result.events:
            reporter.print_template_evolution(
                events=result.events,
                templates=result.templates,
            )
        if result.anomaly_report:
            reporter.print_anomalies(result.anomaly_report)

    # JSON输出
    json_path = None
    if output_format == "json" or output_format == "all":
        json_reporter = JsonReporter(config.output)
        report = json_reporter.generate_report(
            templates=result.templates,
            clusters=result.clusters,
            anomaly_report=result.anomaly_report,
            total_logs=result.total_logs,
            duration=result.duration,
            time_series_analyses=result.anomaly_report.time_series_analyses if result.anomaly_report else None,
            events=result.events,
            triggered_alerts=result.triggered_alerts,
        )

        os.makedirs(output_dir, exist_ok=True)
        json_path = os.path.join(output_dir, "report.json")
        json_reporter.save_report(report, json_path)
        print(f"JSON报告已保存: {json_path}")

    # HTML输出
    if output_format == "html" or output_format == "all":
        html_reporter = HtmlReporter(config.output)
        html = html_reporter.generate_report(
            templates=result.templates,
            clusters=result.clusters,
            anomaly_report=result.anomaly_report,
            total_logs=result.total_logs,
            duration=result.duration,
            time_series_analyses=result.anomaly_report.time_series_analyses if result.anomaly_report else None,
            triggered_alerts=result.triggered_alerts,
            events=result.events,
        )

        os.makedirs(output_dir, exist_ok=True)
        html_path = os.path.join(output_dir, "report.html")
        html_reporter.save_report(html, html_path)
        print(f"HTML报告已保存: {html_path}")

    return json_path


@app.command("analyze")
def analyze(
    files: List[str] = typer.Argument(..., help="日志文件路径列表"),
    config_path: Optional[str] = typer.Option(
        None, "--config", "-c", help="配置文件路径"
    ),
    output_format: str = typer.Option(
        "terminal", "--format", "-f",
        help="输出格式: terminal/json/html/all"
    ),
    output_dir: str = typer.Option(
        "./output", "--output-dir", "-o", help="输出目录"
    ),
    incremental: bool = typer.Option(
        False, "--incremental", "-i", help="增量模式"
    ),
    baseline: Optional[str] = typer.Option(
        None, "--baseline", "-b", help="基线模板库文件"
    ),
    depth: Optional[int] = typer.Option(
        None, "--depth", help="Drain树深度"
    ),
    similarity: Optional[float] = typer.Option(
        None, "--similarity", "-s", help="相似度阈值"
    ),
    top_n: Optional[int] = typer.Option(
        None, "--top", help="显示Top N个模板"
    ),
    workers: Optional[int] = typer.Option(
        None, "--workers", "-w",
        help="并行进程数，0=禁用并行单进程，负数=CPU核心数一半，默认使用配置"
    ),
    tag: Optional[str] = typer.Option(
        None, "--tag", "-t",
        help="按标签过滤输出（支持层级匹配，如infra/database）"
    ),
):
    """分析日志文件，提取模板并检测异常"""

    overrides = {}
    if depth is not None:
        overrides["drain.depth"] = depth
    if similarity is not None:
        overrides["drain.st"] = similarity
        overrides["drain.sim_th"] = similarity
    if top_n is not None:
        overrides["output.top_n"] = top_n
    if baseline:
        overrides["anomaly.baseline_file"] = baseline
    if output_dir:
        overrides["output.output_dir"] = output_dir
    if workers is not None:
        overrides["parallel.workers"] = workers

    config = _get_config(config_path, overrides)
    processor = LogProcessor(config)

    result = processor.process_files(
        file_paths=files,
        incremental=incremental,
        load_state=incremental,
        workers=workers,
        tag_filter=tag,
    )

    _process_and_report(
        processor=processor,
        result=result,
        output_format=output_format,
        output_dir=config.output.output_dir,
        config=config,
    )


@app.command("stream")
def stream(
    config_path: Optional[str] = typer.Option(
        None, "--config", "-c", help="配置文件路径"
    ),
    output_format: str = typer.Option(
        "terminal", "--format", "-f",
        help="输出格式: terminal/json/html"
    ),
    output_dir: str = typer.Option(
        "./output", "--output-dir", "-o", help="输出目录"
    ),
    depth: Optional[int] = typer.Option(
        None, "--depth", help="Drain树深度"
    ),
    similarity: Optional[float] = typer.Option(
        None, "--similarity", "-s", help="相似度阈值"
    ),
    top_n: Optional[int] = typer.Option(
        None, "--top", help="显示Top N个模板"
    ),
    state_file: Optional[str] = typer.Option(
        None, "--state", help="状态文件路径（用于流式增量）"
    ),
    tag: Optional[str] = typer.Option(
        None, "--tag", "-t",
        help="按标签过滤输出（支持层级匹配）"
    ),
):
    """从标准输入读取日志流进行分析

    用法: tail -f access.log | log-cluster stream
    """

    overrides = {}
    if depth is not None:
        overrides["drain.depth"] = depth
    if similarity is not None:
        overrides["drain.st"] = similarity
        overrides["drain.sim_th"] = similarity
    if top_n is not None:
        overrides["output.top_n"] = top_n
    if output_dir:
        overrides["output.output_dir"] = output_dir
    if state_file:
        overrides["incremental.state_file"] = state_file

    config = _get_config(config_path, overrides)
    processor = LogProcessor(config)

    def line_generator():
        for line in sys.stdin:
            yield line

    result = processor.process_stream(line_generator(), tag_filter=tag)

    _process_and_report(
        processor=processor,
        result=result,
        output_format=output_format,
        output_dir=config.output.output_dir,
        config=config,
    )


@app.command("save-state")
def save_state(
    files: List[str] = typer.Argument(..., help="日志文件路径列表"),
    state_file: str = typer.Option(
        "./log-cluster-state.json", "--state", "-s", help="状态文件输出路径"
    ),
    config_path: Optional[str] = typer.Option(
        None, "--config", "-c", help="配置文件路径"
    ),
    incremental: bool = typer.Option(
        False, "--incremental", "-i", help="增量模式（加载已有状态）"
    ),
):
    """处理日志并保存状态文件"""
    
    overrides = {}
    overrides["incremental.state_file"] = state_file
    
    config = _get_config(config_path, overrides)
    processor = LogProcessor(config)
    
    result = processor.process_files(
        file_paths=files,
        incremental=incremental,
        load_state=incremental,
    )
    
    # 确保状态被保存
    if result.drain is not None:
        processor.state_manager.save_state(result.drain, result.file_offsets)
    
    print(f"处理完成，共 {result.total_logs} 条日志")
    print(f"模板数: {len(result.templates)}")
    print(f"状态文件: {config.incremental.state_file}")


@app.command("diff")
def diff(
    old_file: str = typer.Argument(..., help="旧版本模板库JSON"),
    new_file: str = typer.Argument(..., help="新版本模板库JSON"),
    output_json: Optional[str] = typer.Option(
        None, "--output", "-o", help="输出JSON结果到文件"
    ),
    spike_threshold: float = typer.Option(
        2.0, "--spike-threshold", help="频率激增阈值（倍数）"
    ),
    exit_with_code: bool = typer.Option(
        True, "--exit-code/--no-exit-code", help="是否根据结果设置退出码"
    ),
):
    """比较两个模板库的差异（CI集成模式）
    
    退出码: 0=无异常, 1=有新ERROR模板, 2=有频率激增
    """
    
    result = compare_templates(old_file, new_file, spike_threshold)
    
    print_diff_report(result)
    
    if output_json:
        os.makedirs(os.path.dirname(output_json) or ".", exist_ok=True)
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"差异结果已保存: {output_json}")
    
    if exit_with_code:
        raise typer.Exit(code=result.exit_code)


@app.command("init-config")
def init_config(
    output_path: str = typer.Option(
        "./log-cluster.yaml", "--output", "-o", help="配置文件输出路径"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="覆盖已存在的配置文件"
    ),
):
    """生成示例配置文件"""
    
    config_file = Path(output_path)
    if config_file.exists() and not force:
        typer.echo(f"配置文件已存在: {output_path}，使用 --force 覆盖")
        raise typer.Exit(code=1)
    
    sample_config = """# log-cluster 配置文件

# Drain算法参数
drain:
  depth: 4              # 前缀树深度
  st: 0.4               # 相似度阈值（0-1，越高越严格）
  max_child: 100        # 每个节点的最大子节点数
  sim_th: 0.4           # 相似度阈值（同st）

# 预处理规则
preprocess:
  order: [IP, UUID, PATH, NUM, EMAIL]  # 预处理顺序
  patterns:
    - name: IP
      regex: '\\b\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\b'
    - name: UUID
      regex: '\\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\\b'
    - name: PATH
      regex: '(?:/[\\w\\-.~]+)+\\.?(?:[\\w\\-]+)?'
    - name: NUM
      regex: '\\b\\d+\\b'
    - name: EMAIL
      regex: '\\b[A-Za-z0-9._%+\\-]+@[A-Za-z0-9.\\-]+\\.[A-Z|a-z]{2,}\\b'

# 输入格式
input:
  format: auto          # auto/text/json/syslog/custom
  time_format: ''       # 自定义时间格式，如 '%Y-%m-%d %H:%M:%S'
  custom_regex: ''      # 自定义正则表达式
  timestamp_group: timestamp  # 时间戳分组名
  level_group: level    # 日志级别分组名
  source_group: source  # 来源分组名
  message_group: message  # 消息体分组名

# 异常检测
anomaly:
  baseline_file: ''     # 基线模板库文件
  freq_unit: hour       # 频率统计单位: minute/hour/day
  freq_n_std: 3.0       # 频率异常标准差倍数
  freq_m_periods: 3     # 频率消失周期数
  min_data_points: 24   # 最小数据点数（不足则跳过频率分析）

# 模板聚类
clustering:
  merge_threshold: 0.3  # 合并阈值（归一化编辑距离）

# 过滤规则
filters:
  whitelist: []         # 白名单正则列表（匹配的模板不参与异常检测）
  focus: []             # 关注模板正则列表（只对这些做频率分析）

# 输出配置
output:
  format: terminal      # terminal/json/html
  output_dir: ./output  # 输出目录
  top_n: 20             # 显示Top N个模板
  html_max_templates: 50  # HTML报告中最多显示的模板数

# 增量处理
incremental:
  state_file: ./log-cluster-state.json  # 状态文件路径
  max_templates: 10000  # 最大模板数（超过则压缩）
  rare_template_count: 5  # 稀有模板阈值（小于此数标记为稀有）

# 并行处理配置
parallel:
  workers: 0            # 并行进程数：0=禁用并行(单进程)，正数=指定进程数，负数=CPU核心数一半

# 日志异常关联分析
correlate:
  window_size: 60       # 滑动窗口大小(秒)，范围5-3600
  min_support: 0.01     # 最小支持度阈值
  min_confidence: 0.5   # 最小置信度阈值
  min_lift: 2.0         # 最小提升度阈值
  burst_threshold: 100  # 突发模板阈值(次数/窗口)
  min_count: 10         # 模板>100时，稀有模板最小出现次数

# 告警规则引擎
# 支持的变量：new_template_count, error_template_count, spike_count,
#            total_templates, processing_speed(行/秒)
# severity: critical/warning/info
alerts:
  - name: high_error_templates
    condition: "error_template_count > 5"
    severity: critical
    message: "检测到 {error_template_count} 个ERROR模板，超过阈值5"
  - name: spike_surge
    condition: "spike_count > 10"
    severity: warning
    message: "频率激增模板 {spike_count} 个，超过阈值10"
  - name: slow_processing
    condition: "processing_speed < 50000"
    severity: info
    message: "处理速度 {processing_speed:.0f} 行/秒，低于5万行/秒"

# 模板标签系统
# pattern: 正则表达式匹配模板字符串
# tags: 标签列表（支持层级如infra/database/timeout，上级标签包含下级）
tags:
  - pattern: "(?i)(error|exception|fail|fatal)"
    tags: ["error/general"]
  - pattern: "(?i)(timeout|timed.?out)"
    tags: ["infra/network/timeout"]
  - pattern: "(?i)(database|mysql|postgres|sql|connection.*refused)"
    tags: ["infra/database/connection"]
  - pattern: "(?i)(out.?of.?memory|oom|heap)"
    tags: ["infra/resource/memory"]
"""
    
    config_file.parent.mkdir(parents=True, exist_ok=True)
    with open(config_file, "w", encoding="utf-8") as f:
        f.write(sample_config)
    
    print(f"示例配置文件已生成: {output_path}")


def _print_correlation_terminal(
    result: CorrelationResult,
    templates: Dict[str, str],
    top_n: int,
):
    """终端模式输出关联分析结果"""
    console = Console()
    console.print()
    console.rule("[bold cyan]日志异常关联分析报告[/bold cyan]")
    console.print()

    summary_table = Table(show_header=True, header_style="bold magenta")
    summary_table.add_column("指标", style="dim")
    summary_table.add_column("值", justify="right")
    summary_table.add_row("总事件数", f"{result.total_events:,}")
    summary_table.add_row("模板总数", f"{result.template_count:,}")
    summary_table.add_row("参与分析模板数", f"{result.filtered_template_count:,}")
    summary_table.add_row("关联规则数", f"{len(result.rules):,}")
    summary_table.add_row("突发模板数", f"{len(result.burst_templates):,}")
    summary_table.add_row("因果链数", f"{len(result.chains):,}")
    console.print(summary_table)
    console.print()

    if result.rules:
        console.rule("[bold green]Top 关联规则[/bold green]")
        console.print()

        rules_table = Table(show_header=True, header_style="bold blue")
        rules_table.add_column("#", justify="right")
        rules_table.add_column("A → B")
        rules_table.add_column("support", justify="right")
        rules_table.add_column("confidence", justify="right")
        rules_table.add_column("lift", justify="right")
        rules_table.add_column("chi2", justify="right")
        rules_table.add_column("jaccard", justify="right")
        rules_table.add_column("标注")

        for i, rule in enumerate(result.rules[:top_n], 1):
            a_str = templates.get(rule.source_template_id, rule.source_template_id)
            b_str = templates.get(rule.target_template_id, rule.target_template_id)
            a_short = a_str[:40] + ("..." if len(a_str) > 40 else "")
            b_short = b_str[:40] + ("..." if len(b_str) > 40 else "")
            pair_str = f"[cyan]{rule.source_template_id}[/cyan]\n{a_short}\n  ↓\n[magenta]{rule.target_template_id}[/magenta]\n{b_short}"

            annotations = []
            if rule.is_strong_correlation:
                annotations.append("[bold red]强关联[/bold red]")
            if rule.is_burst:
                annotations.append("[bold yellow]突发[/bold yellow]")
            if rule.is_simultaneous:
                annotations.append("[bold blue]同时[/bold blue]")
            annotation_str = " ".join(annotations) if annotations else "-"

            rules_table.add_row(
                str(i),
                pair_str,
                f"{rule.support:.4f}",
                f"{rule.confidence:.4f}",
                f"{rule.lift:.4f}",
                f"{rule.chi2:.4f}",
                f"{rule.jaccard:.4f}",
                annotation_str,
            )

        console.print(rules_table)
        console.print()

    if result.chains:
        console.rule("[bold magenta]Top 因果链[/bold magenta]")
        console.print()

        for i, chain in enumerate(result.chains[:top_n], 1):
            title = Text(f"#{i} 因果链 (长度={chain.length}, 累计conf={chain.total_confidence:.4f})")
            title.stylize("bold")
            console.print(title)

            path_table = Table(show_header=True, header_style="bold green")
            path_table.add_column("步骤")
            path_table.add_column("模板")
            path_table.add_column("模板内容", style="dim")
            path_table.add_column("边confidence", justify="right")

            for j, node in enumerate(chain.path):
                tid = node.strip("[]").split(", ")[0] if node.startswith("[") else node
                t_str = templates.get(tid, tid)
                t_short = t_str[:50] + ("..." if len(t_str) > 50 else "")
                weight_str = ""
                if j < len(chain.edge_weights):
                    weight_str = f"{chain.edge_weights[j]:.4f}"
                path_table.add_row(str(j + 1), node, t_short, weight_str)

            console.print(path_table)
            console.print()


def _output_correlation_json(
    result: CorrelationResult,
    templates: Dict[str, str],
    top_n: int = 20,
):
    """JSON模式输出关联分析结果"""
    rules_data = []
    for rule in result.rules[:top_n]:
        rule_dict = rule.to_dict()
        rule_dict["source_template"] = templates.get(
            rule.source_template_id, rule.source_template_id
        )
        rule_dict["target_template"] = templates.get(
            rule.target_template_id, rule.target_template_id
        )
        rules_data.append(rule_dict)

    chains_data = [chain.to_dict() for chain in result.chains[:top_n]]

    output = {
        "summary": {
            "total_events": result.total_events,
            "template_count": result.template_count,
            "filtered_template_count": result.filtered_template_count,
            "burst_template_count": len(result.burst_templates),
            "rule_count": len(result.rules),
            "chain_count": len(result.chains),
        },
        "burst_templates": list(result.burst_templates),
        "rules": rules_data,
        "chains": chains_data,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def _load_state_for_correlate(
    state_file: str,
) -> Tuple[Dict[str, List[datetime]], Dict[str, str], List[str]]:
    """从状态文件加载模板时序数据

    Returns:
        (template_timestamps, template_id_to_str, template_ids)
    """
    with open(state_file, "r", encoding="utf-8") as f:
        state_data = json.load(f)

    drain_state = state_data.get("drain_state", {})
    templates_data = drain_state.get("templates", {})

    template_timestamps: Dict[str, List[datetime]] = {}
    template_id_to_str: Dict[str, str] = {}
    template_ids: List[str] = []

    for tid, t_data in templates_data.items():
        template_ids.append(tid)
        template_id_to_str[tid] = t_data.get("template_str", tid)

        stats = t_data.get("stats", {})
        first_seen_str = stats.get("first_seen")
        last_seen_str = stats.get("last_seen")
        count = stats.get("count", 0)

        timestamps: List[datetime] = []
        if first_seen_str and last_seen_str and count > 0:
            try:
                first_seen = datetime.fromisoformat(first_seen_str)
                if count == 1:
                    timestamps.append(first_seen)
                else:
                    last_seen = datetime.fromisoformat(last_seen_str)
                    total_seconds = max(
                        1.0, (last_seen - first_seen).total_seconds()
                    )
                    interval = total_seconds / max(count - 1, 1)
                    for k in range(count):
                        ts = first_seen.timestamp() + interval * k
                        timestamps.append(datetime.fromtimestamp(ts))
            except (ValueError, TypeError):
                pass

        template_timestamps[tid] = timestamps

    return template_timestamps, template_id_to_str, template_ids


def _is_state_file(file_path: str) -> bool:
    """判断文件是否是状态文件(JSON格式)"""
    if not file_path.endswith(".json"):
        return False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return "drain_state" in data and "version" in data
    except (json.JSONDecodeError, OSError):
        return False


@app.command("correlate")
def correlate(
    file: str = typer.Argument(..., help="日志文件路径或状态文件路径(JSON)"),
    config_path: Optional[str] = typer.Option(
        None, "--config", "-c", help="配置文件路径"
    ),
    window: Optional[int] = typer.Option(
        None, "--window", help="滑动窗口大小(秒), 范围5-3600"
    ),
    min_support: Optional[float] = typer.Option(
        None, "--min-support", help="最小支持度阈值"
    ),
    min_confidence: Optional[float] = typer.Option(
        None, "--min-confidence", help="最小置信度阈值"
    ),
    min_lift: Optional[float] = typer.Option(
        None, "--min-lift", help="最小提升度阈值"
    ),
    burst_threshold: Optional[int] = typer.Option(
        None, "--burst-threshold", help="突发模板阈值(次数/窗口)"
    ),
    output_format: str = typer.Option(
        "terminal", "--format", "-f", help="输出格式: terminal/json"
    ),
    top_n: int = typer.Option(
        20, "--top", help="显示前N条关联规则"
    ),
    save_state: Optional[str] = typer.Option(
        None, "--save-state", help="关联分析状态保存路径(JSON)"
    ),
    load_state: Optional[str] = typer.Option(
        None, "--load-state", help="加载已有关联状态文件(JSON)，文件不存在则当作首次分析"
    ),
):
    """日志异常关联分析 - 发现模板间时序关联与因果链"""

    overrides = {}
    if window is not None:
        overrides["correlate.window_size"] = window
    if min_support is not None:
        overrides["correlate.min_support"] = min_support
    if min_confidence is not None:
        overrides["correlate.min_confidence"] = min_confidence
    if min_lift is not None:
        overrides["correlate.min_lift"] = min_lift
    if burst_threshold is not None:
        overrides["correlate.burst_threshold"] = burst_threshold

    config = _get_config(config_path, overrides)
    correlate_cfg = config.correlate

    loaded_corr_state: Optional[CorrelationState] = None
    if load_state is not None:
        loaded_corr_state = load_correlation_state(load_state)
        if loaded_corr_state is not None:
            print(f"已加载关联状态: {load_state}")

    is_state = _is_state_file(file)

    template_timestamps: Dict[str, List[datetime]]
    template_id_to_str: Dict[str, str]
    template_ids: List[str]

    if is_state:
        template_timestamps, template_id_to_str, template_ids = (
            _load_state_for_correlate(file)
        )
    else:
        processor = LogProcessor(config)
        result = processor.process_files(
            file_paths=[file],
            incremental=False,
            load_state=False,
        )
        template_timestamps = result.template_timestamps
        template_id_to_str = {}
        template_ids = []
        for t in result.templates:
            template_ids.append(t.template_id)
            template_id_to_str[t.template_id] = t.template_str

    analyzer_cfg = CorrelateConfig(
        window_size=correlate_cfg.window_size,
        min_support=correlate_cfg.min_support,
        min_confidence=correlate_cfg.min_confidence,
        min_lift=correlate_cfg.min_lift,
        burst_threshold=correlate_cfg.burst_threshold,
        min_count=correlate_cfg.min_count,
    )
    analyzer = CorrelationAnalyzer(analyzer_cfg)

    corr_result = analyzer.analyze(
        template_timestamps, template_ids, loaded_state=loaded_corr_state
    )

    if save_state is not None:
        state = analyzer.get_state()
        if state is not None:
            save_correlation_state(state, save_state)
            print(f"关联状态已保存: {save_state}")

    if output_format == "json":
        _output_correlation_json(corr_result, template_id_to_str, top_n)
    else:
        _print_correlation_terminal(corr_result, template_id_to_str, top_n)


@app.command("version")
def version():
    """显示版本号"""
    print(f"log-cluster v{__version__}")


def main():
    app()


if __name__ == "__main__":
    main()
