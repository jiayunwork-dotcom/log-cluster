"""报告输出模块 - 终端彩色输出、JSON、HTML报告"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .anomaly import AnomalyReport
from .clustering import TemplateCluster
from .config import OutputConfig
from .drain import LogTemplate
from .timeseries import TimeSeriesAnalysis


class TerminalReporter:
    """终端彩色输出报告"""
    
    def __init__(self, config: OutputConfig):
        self.config = config
        self.console = Console()
    
    def print_summary(
        self,
        total_logs: int,
        total_templates: int,
        total_clusters: int,
        duration: float,
    ):
        """打印处理摘要"""
        self.console.print()
        self.console.rule("[bold cyan]日志聚类分析报告[/bold cyan]")
        self.console.print()
        
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("指标", style="dim")
        table.add_column("值", justify="right")
        
        table.add_row("总日志数", f"{total_logs:,}")
        table.add_row("模板数", f"{total_templates:,}")
        table.add_row("聚类组数", f"{total_clusters:,}")
        table.add_row("处理耗时", f"{duration:.2f} 秒")
        table.add_row("处理速度", f"{total_logs / max(duration, 0.001):,.0f} 行/秒")
        
        self.console.print(table)
        self.console.print()
    
    def print_top_templates(self, templates: List[LogTemplate]):
        """打印Top N模板"""
        top_n = self.config.top_n
        show_templates = templates[:top_n]
        
        self.console.rule(f"[bold green]Top {len(show_templates)} 模板 (按频率排序)[/bold green]")
        self.console.print()
        
        table = Table(show_header=True, header_style="bold blue")
        table.add_column("#", style="dim", justify="right", width=4)
        table.add_column("模板ID", width=10)
        table.add_column("频率", justify="right", width=10)
        table.add_column("级别", width=8)
        table.add_column("模板内容", overflow="fold")
        table.add_column("状态", width=12)
        
        for i, template in enumerate(show_templates, 1):
            status_parts = []
            status_style = ""
            
            if template.is_new:
                status_parts.append("新模板")
                status_style = "red bold"
            if template.is_error:
                status_parts.append("错误")
                if not status_style:
                    status_style = "red"
            if template.is_spike:
                status_parts.append("激增")
                if not status_style:
                    status_style = "yellow"
            if template.is_vanished:
                status_parts.append("消失")
            if template.is_periodic:
                status_parts.append("周期")
            if template.is_rare:
                status_parts.append("稀有")
            
            status_text = ", ".join(status_parts) if status_parts else "正常"
            
            # 获取主要级别
            level = self._get_main_level(template)
            level_style = self._get_level_style(level)
            
            # 模板内容截断
            template_str = template.template_str
            if len(template_str) > 100:
                template_str = template_str[:97] + "..."
            
            if status_style:
                table.add_row(
                    str(i),
                    template.template_id,
                    f"{template.stats.count:,}",
                    Text(level, style=level_style),
                    template_str,
                    Text(status_text, style=status_style),
                )
            else:
                table.add_row(
                    str(i),
                    template.template_id,
                    f"{template.stats.count:,}",
                    Text(level, style=level_style),
                    template_str,
                    status_text,
                )
        
        self.console.print(table)
        self.console.print()
    
    def print_clusters(self, clusters: List[TemplateCluster]):
        """打印聚类组"""
        self.console.rule("[bold yellow]模板聚类组[/bold yellow]")
        self.console.print()
        
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("组ID", width=8)
        table.add_column("总频率", justify="right", width=10)
        table.add_column("模板数", justify="right", width=8)
        table.add_column("代表模板", overflow="fold")
        table.add_column("首次出现", width=20)
        table.add_column("最近出现", width=20)
        
        for cluster in clusters[:20]:  # 只显示前20个组
            first_seen = cluster.first_seen.strftime("%Y-%m-%d %H:%M:%S") if cluster.first_seen else "未知"
            last_seen = cluster.last_seen.strftime("%Y-%m-%d %H:%M:%S") if cluster.last_seen else "未知"
            
            rep_str = cluster.representative.template_str
            if len(rep_str) > 60:
                rep_str = rep_str[:57] + "..."
            
            table.add_row(
                cluster.cluster_id,
                f"{cluster.total_count:,}",
                str(len(cluster.templates)),
                rep_str,
                first_seen,
                last_seen,
            )
        
        self.console.print(table)
        self.console.print()
    
    def print_anomalies(self, report: AnomalyReport):
        """打印异常信息"""
        self.console.rule("[bold red]异常检测结果[/bold red]")
        self.console.print()
        
        if report.data_insufficient:
            self.console.print(f"[yellow]⚠ {report.data_insufficient_message}[/yellow]")
            self.console.print()
        
        if report.new_templates:
            self.console.print(f"[red]🚨 发现 {len(report.new_templates)} 个新模板[/red]")
            for t in report.new_templates[:10]:
                self.console.print(f"  - [{t.template_id}] {t.template_str[:80]} (频率: {t.stats.count})")
            if len(report.new_templates) > 10:
                self.console.print(f"  ... 还有 {len(report.new_templates) - 10} 个")
            self.console.print()
        
        if report.error_templates:
            self.console.print(f"[red]❌ 发现 {len(report.error_templates)} 个错误模板[/red]")
            for t in report.error_templates[:10]:
                self.console.print(f"  - [{t.template_id}] {t.template_str[:80]} (频率: {t.stats.count})")
            if len(report.error_templates) > 10:
                self.console.print(f"  ... 还有 {len(report.error_templates) - 10} 个")
            self.console.print()
        
        if report.spike_templates:
            self.console.print(f"[yellow]📈 发现 {len(report.spike_templates)} 个频率激增模板[/yellow]")
            for t in report.spike_templates[:10]:
                self.console.print(f"  - [{t.template_id}] {t.template_str[:80]} (频率: {t.stats.count})")
            if len(report.spike_templates) > 10:
                self.console.print(f"  ... 还有 {len(report.spike_templates) - 10} 个")
            self.console.print()
        
        if report.vanished_templates:
            self.console.print(f"[yellow]📉 发现 {len(report.vanished_templates)} 个频率消失模板[/yellow]")
            for t in report.vanished_templates[:10]:
                self.console.print(f"  - [{t.template_id}] {t.template_str[:80]}")
            if len(report.vanished_templates) > 10:
                self.console.print(f"  ... 还有 {len(report.vanished_templates) - 10} 个")
            self.console.print()
        
        if report.periodic_templates:
            self.console.print(f"[blue]🔄 发现 {len(report.periodic_templates)} 个周期性模板[/blue]")
            for t in report.periodic_templates[:10]:
                self.console.print(f"  - [{t.template_id}] {t.template_str[:80]}")
            if len(report.periodic_templates) > 10:
                self.console.print(f"  ... 还有 {len(report.periodic_templates) - 10} 个")
            self.console.print()
        
        if not report.has_anomaly and not report.data_insufficient:
            self.console.print("[green]✅ 未发现异常[/green]")
            self.console.print()
    
    def _get_main_level(self, template: LogTemplate) -> str:
        """获取模板的主要日志级别"""
        if not template.stats.level_counts:
            return "INFO"
        
        return max(template.stats.level_counts.items(), key=lambda x: x[1])[0]
    
    def _get_level_style(self, level: str) -> str:
        """获取日志级别的显示样式"""
        styles = {
            "DEBUG": "dim",
            "INFO": "green",
            "WARN": "yellow",
            "ERROR": "red",
            "FATAL": "red bold",
        }
        return styles.get(level.upper(), "")


class JsonReporter:
    """JSON格式报告"""
    
    def __init__(self, config: OutputConfig):
        self.config = config
    
    def generate_report(
        self,
        templates: List[LogTemplate],
        clusters: List[TemplateCluster],
        anomaly_report: AnomalyReport,
        total_logs: int,
        duration: float,
        time_series_analyses: Optional[Dict[str, TimeSeriesAnalysis]] = None,
    ) -> dict:
        """生成JSON格式报告"""
        return {
            "summary": {
                "total_logs": total_logs,
                "total_templates": len(templates),
                "total_clusters": len(clusters),
                "duration_seconds": duration,
                "generated_at": datetime.now().isoformat(),
            },
            "templates": [t.to_dict() for t in templates],
            "clusters": [c.to_dict() for c in clusters],
            "anomalies": {
                "has_anomaly": anomaly_report.has_anomaly,
                "new_templates": [t.template_id for t in anomaly_report.new_templates],
                "error_templates": [t.template_id for t in anomaly_report.error_templates],
                "spike_templates": [t.template_id for t in anomaly_report.spike_templates],
                "vanished_templates": [t.template_id for t in anomaly_report.vanished_templates],
                "periodic_templates": [t.template_id for t in anomaly_report.periodic_templates],
                "data_insufficient": anomaly_report.data_insufficient,
                "data_insufficient_message": anomaly_report.data_insufficient_message,
            },
            "time_series": {
                tid: self._ts_to_dict(tsa)
                for tid, tsa in (time_series_analyses or {}).items()
            },
        }
    
    def save_report(self, report: dict, output_path: str) -> str:
        """保存报告到文件"""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        return str(output_file)
    
    def _ts_to_dict(self, tsa: TimeSeriesAnalysis) -> dict:
        return {
            "template_id": tsa.template_id,
            "is_periodic": tsa.is_periodic,
            "periodicity_hours": tsa.periodicity_hours,
            "has_spike": tsa.has_spike,
            "spike_timestamps": [ts.isoformat() for ts in tsa.spike_timestamps],
            "has_vanished": tsa.has_vanished,
            "vanished_since": tsa.vanished_since.isoformat() if tsa.vanished_since else None,
            "mean": tsa.mean,
            "std": tsa.std,
            "data_points": [
                {"timestamp": p.timestamp.isoformat(), "value": p.value}
                for p in tsa.time_series
            ],
        }


class HtmlReporter:
    """HTML格式报告（带SVG时序图）"""
    
    def __init__(self, config: OutputConfig):
        self.config = config
    
    def generate_report(
        self,
        templates: List[LogTemplate],
        clusters: List[TemplateCluster],
        anomaly_report: AnomalyReport,
        total_logs: int,
        duration: float,
        time_series_analyses: Optional[Dict[str, TimeSeriesAnalysis]] = None,
    ) -> str:
        """生成HTML报告"""
        top_templates = templates[:self.config.html_max_templates]
        
        charts_html = ""
        if time_series_analyses:
            charts_html = self._generate_charts(top_templates, time_series_analyses)
        
        templates_html = self._generate_templates_table(templates)
        clusters_html = self._generate_clusters_table(clusters)
        anomalies_html = self._generate_anomalies_section(anomaly_report)
        
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>日志聚类分析报告</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
       background: #f5f5f5; color: #333; padding: 20px; }}
.container {{ max-width: 1400px; margin: 0 auto; }}
h1 {{ color: #2c3e50; margin-bottom: 20px; }}
h2 {{ color: #34495e; margin: 30px 0 15px; border-bottom: 2px solid #3498db; padding-bottom: 5px; }}
.summary-card {{ background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px;
                 box-shadow: 0 2px 4px rgba(0,0,0,0.1); display: flex; gap: 30px; flex-wrap: wrap; }}
.summary-item {{ text-align: center; }}
.summary-value {{ font-size: 2em; font-weight: bold; color: #2980b9; }}
.summary-label {{ color: #7f8c8d; margin-top: 5px; }}
table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px;
         box-shadow: 0 2px 4px rgba(0,0,0,0.1); overflow: hidden; }}
th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ecf0f1; }}
th {{ background: #3498db; color: white; }}
tr:hover {{ background: #f8f9fa; }}
.new {{ color: #e74c3c; font-weight: bold; }}
.error {{ color: #c0392b; font-weight: bold; }}
.spike {{ color: #f39c12; font-weight: bold; }}
.periodic {{ color: #27ae60; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.8em;
         margin-right: 4px; }}
.badge-new {{ background: #e74c3c; color: white; }}
.badge-error {{ background: #c0392b; color: white; }}
.badge-spike {{ background: #f39c12; color: white; }}
.badge-periodic {{ background: #27ae60; color: white; }}
.badge-vanished {{ background: #7f8c8d; color: white; }}
.chart-container {{ background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.chart-title {{ font-weight: bold; margin-bottom: 10px; color: #2c3e50; }}
svg {{ width: 100%; height: 150px; }}
.anomaly-section {{ background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.anomaly-section h3 {{ color: #e74c3c; margin-bottom: 10px; }}
.anomaly-list {{ list-style: none; }}
.anomaly-list li {{ padding: 8px 0; border-bottom: 1px solid #ecf0f1; }}
.template-str {{ font-family: monospace; background: #f8f9fa; padding: 2px 6px; 
                 border-radius: 4px; font-size: 0.9em; }}
.tabs {{ display: flex; gap: 10px; margin-bottom: 20px; }}
.tab {{ padding: 10px 20px; background: #bdc3c7; border: none; border-radius: 4px;
        cursor: pointer; font-size: 14px; }}
.tab.active {{ background: #3498db; color: white; }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}
.collapsible {{ cursor: pointer; }}
.collapsible::before {{ content: "▼ "; }}
.collapsible.collapsed::before {{ content: "▶ "; }}
.collapsed-content {{ }}
.collapsed .collapsed-content {{ display: none; }}
</style>
</head>
<body>
<div class="container">
<h1>📊 日志聚类分析报告</h1>

<div class="summary-card">
    <div class="summary-item">
        <div class="summary-value">{total_logs:,}</div>
        <div class="summary-label">总日志数</div>
    </div>
    <div class="summary-item">
        <div class="summary-value">{len(templates)}</div>
        <div class="summary-label">模板数</div>
    </div>
    <div class="summary-item">
        <div class="summary-value">{len(clusters)}</div>
        <div class="summary-label">聚类组数</div>
    </div>
    <div class="summary-item">
        <div class="summary-value">{duration:.2f}s</div>
        <div class="summary-label">处理耗时</div>
    </div>
    <div class="summary-item">
        <div class="summary-value">{total_logs / max(duration, 0.001):,.0f}</div>
        <div class="summary-label">行/秒</div>
    </div>
</div>

<h2>⚠️ 异常检测</h2>
{anomalies_html}

<h2>📈 时序图表</h2>
{charts_html}

<div class="tabs">
    <button class="tab active" onclick="switchTab('templates')">模板列表</button>
    <button class="tab" onclick="switchTab('clusters')">聚类组</button>
</div>

<div id="tab-templates" class="tab-content active">
    {templates_html}
</div>

<div id="tab-clusters" class="tab-content">
    {clusters_html}
</div>

</div>

<script>
function switchTab(tabName) {{
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
    document.getElementById('tab-' + tabName).classList.add('active');
    event.target.classList.add('active');
}}

function toggleCollapse(id) {{
    var el = document.getElementById(id);
    el.classList.toggle('collapsed');
}}
</script>

</body>
</html>"""
        
        return html
    
    def save_report(self, html: str, output_path: str) -> str:
        """保存HTML报告到文件"""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(html)
        
        return str(output_file)
    
    def _generate_charts(
        self,
        templates: List[LogTemplate],
        time_series_analyses: Dict[str, TimeSeriesAnalysis],
    ) -> str:
        """生成SVG时序图"""
        charts = []
        
        for template in templates:
            tsa = time_series_analyses.get(template.template_id)
            if not tsa or not tsa.time_series:
                continue
            
            svg = self._generate_svg_chart(tsa)
            
            badges = []
            if template.is_new:
                badges.append('<span class="badge badge-new">新</span>')
            if template.is_error:
                badges.append('<span class="badge badge-error">错误</span>')
            if template.is_spike:
                badges.append('<span class="badge badge-spike">激增</span>')
            if template.is_periodic:
                badges.append('<span class="badge badge-periodic">周期</span>')
            
            chart_html = f"""
            <div class="chart-container">
                <div class="chart-title">
                    {''.join(badges)}
                    [{template.template_id}] <span class="template-str">{self._escape_html(template.template_str[:80])}</span>
                    (频率: {template.stats.count:,})
                </div>
                {svg}
            </div>
            """
            charts.append(chart_html)
        
        if not charts:
            return "<p>无时序数据</p>"
        
        return "\n".join(charts)
    
    def _generate_svg_chart(self, tsa: TimeSeriesAnalysis) -> str:
        """生成单个SVG时序图"""
        data = tsa.time_series
        if not data:
            return "<svg></svg>"
        
        width = 800
        height = 120
        padding_top = 10
        padding_bottom = 20
        padding_left = 40
        padding_right = 10
        
        chart_width = width - padding_left - padding_right
        chart_height = height - padding_top - padding_bottom
        
        values = [p.value for p in data]
        max_val = max(values) if values else 1
        if max_val == 0:
            max_val = 1
        
        n = len(data)
        if n <= 1:
            step = chart_width
        else:
            step = chart_width / (n - 1)
        
        # 生成路径
        path_points = []
        for i, point in enumerate(data):
            x = padding_left + i * step
            y = padding_top + chart_height - (point.value / max_val) * chart_height
            path_points.append(f"{x:.1f},{y:.1f}")
        
        path_d = "M" + " L".join(path_points)
        
        # 生成填充区域
        area_d = f"M{padding_left},{padding_top + chart_height} L" + " L".join(path_points) + f" L{padding_left + (n-1)*step:.1f},{padding_top + chart_height} Z"
        
        # Y轴刻度
        y_ticks = ""
        for i in range(5):
            val = max_val * (4 - i) / 4
            y = padding_top + chart_height * i / 4
            y_ticks += f'<text x="{padding_left - 5}" y="{y + 4}" text-anchor="end" font-size="10" fill="#999">{val:.0f}</text>'
        
        # X轴标签（只显示首尾）
        x_labels = ""
        if n > 0:
            first_ts = data[0].timestamp.strftime("%m-%d %H:%M")
            last_ts = data[-1].timestamp.strftime("%m-%d %H:%M")
            x_labels += f'<text x="{padding_left}" y="{height - 5}" font-size="10" fill="#999">{first_ts}</text>'
            x_labels += f'<text x="{width - padding_right}" y="{height - 5}" text-anchor="end" font-size="10" fill="#999">{last_ts}</text>'
        
        # 激增点标记
        spike_markers = ""
        if tsa.spike_timestamps:
            for spike_ts in tsa.spike_timestamps:
                # 找到最近的数据点
                for i, point in enumerate(data):
                    if point.timestamp >= spike_ts:
                        x = padding_left + i * step
                        y = padding_top + chart_height - (point.value / max_val) * chart_height
                        spike_markers += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#e74c3c" />'
                        break
        
        svg = f"""
        <svg viewBox="0 0 {width} {height}" preserveAspectRatio="none">
            <!-- 网格线 -->
            <line x1="{padding_left}" y1="{padding_top}" x2="{width - padding_right}" y2="{padding_top}" stroke="#eee" stroke-width="1"/>
            <line x1="{padding_left}" y1="{padding_top + chart_height/2}" x2="{width - padding_right}" y2="{padding_top + chart_height/2}" stroke="#eee" stroke-width="1"/>
            <line x1="{padding_left}" y1="{padding_top + chart_height}" x2="{width - padding_right}" y2="{padding_top + chart_height}" stroke="#eee" stroke-width="1"/>
            
            <!-- 填充区域 -->
            <path d="{area_d}" fill="#3498db" fill-opacity="0.2"/>
            
            <!-- 折线 -->
            <path d="{path_d}" fill="none" stroke="#3498db" stroke-width="2"/>
            
            <!-- Y轴刻度 -->
            {y_ticks}
            
            <!-- X轴标签 -->
            {x_labels}
            
            <!-- 激增点 -->
            {spike_markers}
        </svg>
        """
        
        return svg
    
    def _generate_templates_table(self, templates: List[LogTemplate]) -> str:
        """生成模板表格"""
        rows = []
        for i, t in enumerate(templates, 1):
            badges = []
            if t.is_new:
                badges.append('<span class="badge badge-new">新模板</span>')
            if t.is_error:
                badges.append('<span class="badge badge-error">错误</span>')
            if t.is_spike:
                badges.append('<span class="badge badge-spike">激增</span>')
            if t.is_vanished:
                badges.append('<span class="badge badge-vanished">消失</span>')
            if t.is_periodic:
                badges.append('<span class="badge badge-periodic">周期</span>')
            
            first_seen = t.stats.first_seen.strftime("%Y-%m-%d %H:%M:%S") if t.stats.first_seen else "未知"
            last_seen = t.stats.last_seen.strftime("%Y-%m-%d %H:%M:%S") if t.stats.last_seen else "未知"
            
            level = max(t.stats.level_counts.items(), key=lambda x: x[1])[0] if t.stats.level_counts else "INFO"
            
            rows.append(f"""
            <tr>
                <td>{i}</td>
                <td>{t.template_id}</td>
                <td style="text-align:right">{t.stats.count:,}</td>
                <td>{level}</td>
                <td><span class="template-str">{self._escape_html(t.template_str)}</span></td>
                <td>{''.join(badges)}</td>
                <td>{first_seen}</td>
                <td>{last_seen}</td>
            </tr>
            """)
        
        return f"""
        <table>
        <thead>
            <tr>
                <th>#</th>
                <th>ID</th>
                <th>频率</th>
                <th>级别</th>
                <th>模板内容</th>
                <th>状态</th>
                <th>首次出现</th>
                <th>最近出现</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
        </table>
        """
    
    def _generate_clusters_table(self, clusters: List[TemplateCluster]) -> str:
        """生成聚类组表格"""
        rows = []
        for i, c in enumerate(clusters, 1):
            first_seen = c.first_seen.strftime("%Y-%m-%d %H:%M:%S") if c.first_seen else "未知"
            last_seen = c.last_seen.strftime("%Y-%m-%d %H:%M:%S") if c.last_seen else "未知"
            
            sub_templates = []
            for t in c.templates:
                sub_templates.append(f"<li>[{t.template_id}] ({t.stats.count:,}) {self._escape_html(t.template_str[:60])}</li>")
            
            row_id = f"cluster-{c.cluster_id}"
            
            rows.append(f"""
            <tr class="collapsible" onclick="toggleCollapse('{row_id}')" id="{row_id}">
                <td>{i}</td>
                <td>{c.cluster_id}</td>
                <td style="text-align:right">{c.total_count:,}</td>
                <td style="text-align:right">{len(c.templates)}</td>
                <td><span class="template-str">{self._escape_html(c.representative.template_str[:80])}</span></td>
                <td>{first_seen}</td>
                <td>{last_seen}</td>
            </tr>
            <tr class="collapsed-content">
                <td colspan="7">
                    <ul style="margin-left: 20px;">
                        {''.join(sub_templates)}
                    </ul>
                </td>
            </tr>
            """)
        
        return f"""
        <table>
        <thead>
            <tr>
                <th>#</th>
                <th>组ID</th>
                <th>总频率</th>
                <th>模板数</th>
                <th>代表模板</th>
                <th>首次出现</th>
                <th>最近出现</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
        </table>
        """
    
    def _generate_anomalies_section(self, report: AnomalyReport) -> str:
        """生成异常检测部分"""
        sections = []
        
        if report.data_insufficient:
            sections.append(f"""
            <div class="anomaly-section">
                <h3>⚠️ 数据不足</h3>
                <p>{self._escape_html(report.data_insufficient_message)}</p>
            </div>
            """)
        
        if report.new_templates:
            items = []
            for t in report.new_templates[:20]:
                items.append(f"<li>[{t.template_id}] <span class='template-str'>{self._escape_html(t.template_str[:80])}</span> ({t.stats.count} 次)</li>")
            sections.append(f"""
            <div class="anomaly-section">
                <h3>🚨 新模板 ({len(report.new_templates)} 个)</h3>
                <ul class="anomaly-list">
                    {''.join(items)}
                </ul>
            </div>
            """)
        
        if report.error_templates:
            items = []
            for t in report.error_templates[:20]:
                items.append(f"<li>[{t.template_id}] <span class='template-str error'>{self._escape_html(t.template_str[:80])}</span> ({t.stats.count} 次)</li>")
            sections.append(f"""
            <div class="anomaly-section">
                <h3>❌ 错误模板 ({len(report.error_templates)} 个)</h3>
                <ul class="anomaly-list">
                    {''.join(items)}
                </ul>
            </div>
            """)
        
        if report.spike_templates:
            items = []
            for t in report.spike_templates[:20]:
                items.append(f"<li>[{t.template_id}] <span class='template-str'>{self._escape_html(t.template_str[:80])}</span> ({t.stats.count} 次)</li>")
            sections.append(f"""
            <div class="anomaly-section">
                <h3>📈 频率激增 ({len(report.spike_templates)} 个)</h3>
                <ul class="anomaly-list">
                    {''.join(items)}
                </ul>
            </div>
            """)
        
        if report.vanished_templates:
            items = []
            for t in report.vanished_templates[:20]:
                items.append(f"<li>[{t.template_id}] <span class='template-str'>{self._escape_html(t.template_str[:80])}</span></li>")
            sections.append(f"""
            <div class="anomaly-section">
                <h3>📉 频率消失 ({len(report.vanished_templates)} 个)</h3>
                <ul class="anomaly-list">
                    {''.join(items)}
                </ul>
            </div>
            """)
        
        if report.periodic_templates:
            items = []
            for t in report.periodic_templates[:20]:
                items.append(f"<li>[{t.template_id}] <span class='template-str'>{self._escape_html(t.template_str[:80])}</span></li>")
            sections.append(f"""
            <div class="anomaly-section">
                <h3>🔄 周期性模板 ({len(report.periodic_templates)} 个)</h3>
                <ul class="anomaly-list">
                    {''.join(items)}
                </ul>
            </div>
            """)
        
        if not sections:
            sections.append("""
            <div class="anomaly-section">
                <h3 style="color: #27ae60;">✅ 未发现异常</h3>
            </div>
            """)
        
        return "\n".join(sections)
    
    def _escape_html(self, text: str) -> str:
        """转义HTML特殊字符"""
        return (
            text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&#39;")
        )
