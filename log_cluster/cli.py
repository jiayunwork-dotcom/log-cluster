"""命令行接口 - 使用Typer实现"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import List, Optional

import typer

from . import __version__
from .ci import compare_templates, print_diff_report
from .config import AppConfig, load_config, override_config
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
        reporter.print_top_templates(result.templates)
        reporter.print_clusters(result.clusters)
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
    
    config = _get_config(config_path, overrides)
    processor = LogProcessor(config)
    
    result = processor.process_files(
        file_paths=files,
        incremental=incremental,
        load_state=incremental,
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
    
    # 从stdin读取
    def line_generator():
        for line in sys.stdin:
            yield line
    
    result = processor.process_stream(line_generator())
    
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
"""
    
    config_file.parent.mkdir(parents=True, exist_ok=True)
    with open(config_file, "w", encoding="utf-8") as f:
        f.write(sample_config)
    
    print(f"示例配置文件已生成: {output_path}")


@app.command("version")
def version():
    """显示版本号"""
    print(f"log-cluster v{__version__}")


def main():
    app()


if __name__ == "__main__":
    main()
