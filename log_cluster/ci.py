"""CI集成模块 - 模板差异比较和退出码"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class TemplateDiff:
    """模板差异"""
    template_id: str
    template_str: str
    count_old: int = 0
    count_new: int = 0
    count_change: float = 0.0  # 变化比例
    is_new: bool = False
    is_removed: bool = False
    is_significant: bool = False  # 是否显著变化
    is_error: bool = False
    
    def to_dict(self) -> dict:
        return {
            "template_id": self.template_id,
            "template_str": self.template_str,
            "count_old": self.count_old,
            "count_new": self.count_new,
            "count_change": self.count_change,
            "is_new": self.is_new,
            "is_removed": self.is_removed,
            "is_significant": self.is_significant,
            "is_error": self.is_error,
        }


@dataclass
class DiffResult:
    """Diff结果"""
    added: List[TemplateDiff] = field(default_factory=list)
    removed: List[TemplateDiff] = field(default_factory=list)
    increased: List[TemplateDiff] = field(default_factory=list)
    decreased: List[TemplateDiff] = field(default_factory=list)
    unchanged: List[TemplateDiff] = field(default_factory=list)
    
    total_old: int = 0
    total_new: int = 0
    
    new_error_templates: List[TemplateDiff] = field(default_factory=list)
    spike_templates: List[TemplateDiff] = field(default_factory=list)
    
    exit_code: int = 0
    
    def to_dict(self) -> dict:
        return {
            "added": [d.to_dict() for d in self.added],
            "removed": [d.to_dict() for d in self.removed],
            "increased": [d.to_dict() for d in self.increased],
            "decreased": [d.to_dict() for d in self.decreased],
            "unchanged": [d.to_dict() for d in self.unchanged],
            "total_old": self.total_old,
            "total_new": self.total_new,
            "new_error_templates": [d.to_dict() for d in self.new_error_templates],
            "spike_templates": [d.to_dict() for d in self.spike_templates],
            "exit_code": self.exit_code,
        }


def _load_template_file(file_path: str) -> Dict[str, dict]:
    """加载模板库文件
    
    支持格式：
    - 完整的状态文件（包含drain_state）
    - 简单的模板列表JSON
    """
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    templates: Dict[str, dict] = {}
    
    # 尝试从drain_state中获取
    if "drain_state" in data:
        drain_state = data["drain_state"]
        if "templates" in drain_state:
            tpls = drain_state["templates"]
            if isinstance(tpls, dict):
                templates = tpls
            elif isinstance(tpls, list):
                for t in tpls:
                    if isinstance(t, dict) and "template_id" in t:
                        templates[t["template_id"]] = t
    elif "templates" in data:
        tpls = data["templates"]
        if isinstance(tpls, dict):
            templates = tpls
        elif isinstance(tpls, list):
            for t in tpls:
                if isinstance(t, dict) and "template_id" in t:
                    templates[t["template_id"]] = t
    else:
        # 假设就是模板字典
        if isinstance(data, dict):
            templates = data
    
    return templates


def _get_template_count(template_data: dict) -> int:
    """获取模板的计数"""
    if "stats" in template_data:
        return template_data["stats"].get("count", 0)
    return template_data.get("count", 0)


def _get_template_str(template_data: dict) -> str:
    """获取模板字符串"""
    return template_data.get("template_str", "")


def _is_error_template(template_data: dict) -> bool:
    """检查是否是错误模板"""
    if template_data.get("is_error", False):
        return True
    
    # 检查级别统计
    if "stats" in template_data:
        level_counts = template_data["stats"].get("level_counts", {})
        if "ERROR" in level_counts or "FATAL" in level_counts:
            return True
    
    return False


def _templates_by_str(templates_dict: Dict[str, dict]) -> Dict[str, dict]:
    """将模板字典转换为以模板字符串为键的字典
    
    因为不同运行的模板ID不同，所以用模板字符串来匹配
    """
    result = {}
    for tid, tdata in templates_dict.items():
        tstr = _get_template_str(tdata)
        if tstr:
            result[tstr] = tdata
    return result


def compare_templates(
    old_file: str,
    new_file: str,
    spike_threshold: float = 2.0,
) -> DiffResult:
    """比较两个模板库
    
    Args:
        old_file: 旧版本模板库文件路径
        new_file: 新版本模板库文件路径
        spike_threshold: 频率激增阈值（倍数）
    
    Returns:
        Diff结果
    """
    old_templates_by_id = _load_template_file(old_file)
    new_templates_by_id = _load_template_file(new_file)
    
    # 用模板字符串作为键来匹配
    old_templates = _templates_by_str(old_templates_by_id)
    new_templates = _templates_by_str(new_templates_by_id)
    
    result = DiffResult()
    
    # 计算总数
    result.total_old = sum(_get_template_count(t) for t in old_templates.values())
    result.total_new = sum(_get_template_count(t) for t in new_templates.values())
    
    all_template_strs = set(old_templates.keys()) | set(new_templates.keys())
    
    for tstr in all_template_strs:
        old_data = old_templates.get(tstr)
        new_data = new_templates.get(tstr)
        
        count_old = 0
        count_new = 0
        is_error = False
        old_id = ""
        new_id = ""
        
        if old_data:
            old_id = old_data.get("template_id", "")
            count_old = _get_template_count(old_data)
            is_error = is_error or _is_error_template(old_data)
        
        if new_data:
            new_id = new_data.get("template_id", "")
            count_new = _get_template_count(new_data)
            is_error = is_error or _is_error_template(new_data)
        
        display_id = new_id or old_id
        
        # 计算变化比例
        if count_old > 0:
            count_change = (count_new - count_old) / count_old
        elif count_new > 0:
            count_change = float("inf")
        else:
            count_change = 0.0
        
        diff = TemplateDiff(
            template_id=display_id,
            template_str=tstr,
            count_old=count_old,
            count_new=count_new,
            count_change=count_change,
            is_error=is_error,
        )
        
        if tstr not in old_templates:
            diff.is_new = True
            result.added.append(diff)
            
            if is_error:
                result.new_error_templates.append(diff)
            
            if count_new > 0:
                result.spike_templates.append(diff)
                diff.is_significant = True
        
        elif tstr not in new_templates:
            diff.is_removed = True
            result.removed.append(diff)
        
        else:
            if count_change >= spike_threshold and count_new > count_old:
                diff.is_significant = True
                result.increased.append(diff)
                result.spike_templates.append(diff)
            elif count_change <= -spike_threshold and count_new < count_old:
                diff.is_removed = True
                result.decreased.append(diff)
            else:
                result.unchanged.append(diff)
    
    # 排序
    result.added.sort(key=lambda d: d.count_new, reverse=True)
    result.removed.sort(key=lambda d: d.count_old, reverse=True)
    result.increased.sort(key=lambda d: d.count_change, reverse=True)
    result.decreased.sort(key=lambda d: d.count_change)
    result.unchanged.sort(key=lambda d: d.count_new, reverse=True)
    result.new_error_templates.sort(key=lambda d: d.count_new, reverse=True)
    result.spike_templates.sort(key=lambda d: d.count_new, reverse=True)
    
    # 计算退出码
    # 0 = 无异常, 1 = 有新ERROR模板, 2 = 有频率激增
    if result.new_error_templates:
        result.exit_code = 1
    elif result.spike_templates:
        result.exit_code = 2
    else:
        result.exit_code = 0
    
    return result


def print_diff_report(result: DiffResult):
    """打印diff报告到终端"""
    from rich.console import Console
    from rich.table import Table
    
    console = Console()
    
    console.rule("[bold cyan]模板差异报告[/bold cyan]")
    console.print()
    
    # 摘要
    console.print(f"旧版本日志总数: [bold]{result.total_old:,}[/bold]")
    console.print(f"新版本日志总数: [bold]{result.total_new:,}[/bold]")
    console.print()
    
    # 新增模板
    if result.added:
        console.print(f"[green]➕ 新增模板: {len(result.added)} 个[/green]")
        table = Table(show_header=True, header_style="bold green")
        table.add_column("模板ID")
        table.add_column("频率", justify="right")
        table.add_column("模板内容", overflow="fold")
        table.add_column("错误", justify="center")
        
        for d in result.added[:20]:
            is_error = "✓" if d.is_error else ""
            table.add_row(d.template_id, f"{d.count_new:,}", d.template_str[:80], is_error)
        
        console.print(table)
        console.print()
    
    # 消失模板
    if result.removed:
        console.print(f"[red]➖ 消失模板: {len(result.removed)} 个[/red]")
        table = Table(show_header=True, header_style="bold red")
        table.add_column("模板ID")
        table.add_column("原频率", justify="right")
        table.add_column("模板内容", overflow="fold")
        
        for d in result.removed[:20]:
            table.add_row(d.template_id, f"{d.count_old:,}", d.template_str[:80])
        
        console.print(table)
        console.print()
    
    # 显著增加
    if result.increased:
        console.print(f"[yellow]📈 频率显著增加: {len(result.increased)} 个[/yellow]")
        table = Table(show_header=True, header_style="bold yellow")
        table.add_column("模板ID")
        table.add_column("旧频率", justify="right")
        table.add_column("新频率", justify="right")
        table.add_column("变化", justify="right")
        table.add_column("模板内容", overflow="fold")
        
        for d in result.increased[:20]:
            change_str = f"+{d.count_change*100:.0f}%" if d.count_change != float("inf") else "新增"
            table.add_row(
                d.template_id,
                f"{d.count_old:,}",
                f"{d.count_new:,}",
                change_str,
                d.template_str[:60],
            )
        
        console.print(table)
        console.print()
    
    # 新错误模板
    if result.new_error_templates:
        console.print(f"[red]🚨 新增错误模板: {len(result.new_error_templates)} 个[/red]")
        for d in result.new_error_templates[:10]:
            console.print(f"  - [{d.template_id}] {d.template_str[:80]} ({d.count_new} 次)")
        console.print()
    
    # 退出码
    console.print(f"退出码: [bold]{result.exit_code}[/bold]")
    if result.exit_code == 0:
        console.print("[green]✅ 无异常[/green]")
    elif result.exit_code == 1:
        console.print("[red]❌ 有新增错误模板[/red]")
    elif result.exit_code == 2:
        console.print("[yellow]⚠️  有频率激增模板[/yellow]")
    console.print()
