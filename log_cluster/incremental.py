"""增量处理模块 - 状态序列化和文件偏移管理"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from .config import IncrementalConfig
from .drain import Drain, LogPreprocessor


@dataclass
class FileOffset:
    """文件偏移信息"""
    path: str
    offset: int = 0
    inode: int = 0
    
    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "offset": self.offset,
            "inode": self.inode,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "FileOffset":
        return cls(
            path=data["path"],
            offset=data.get("offset", 0),
            inode=data.get("inode", 0),
        )


@dataclass
class ProcessorState:
    """处理器状态"""
    drain_state: dict = field(default_factory=dict)
    file_offsets: Dict[str, FileOffset] = field(default_factory=dict)
    version: str = "1.0"
    
    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "drain_state": self.drain_state,
            "file_offsets": {k: v.to_dict() for k, v in self.file_offsets.items()},
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ProcessorState":
        state = cls(
            version=data.get("version", "1.0"),
            drain_state=data.get("drain_state", {}),
        )
        offsets = data.get("file_offsets", {})
        for key, value in offsets.items():
            state.file_offsets[key] = FileOffset.from_dict(value)
        return state


class StateManager:
    """状态管理器"""
    
    def __init__(self, config: IncrementalConfig):
        self.config = config
    
    def save_state(self, drain: Drain, file_offsets: Dict[str, FileOffset]) -> str:
        """保存状态到文件
        
        Args:
            drain: Drain算法实例
            file_offsets: 文件偏移字典
        
        Returns:
            状态文件路径
        """
        state = ProcessorState(
            drain_state=drain.to_dict(),
            file_offsets=file_offsets,
        )
        
        state_file = Path(self.config.state_file)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        
        return str(state_file)
    
    def load_state(self, preprocessor: LogPreprocessor) -> tuple[Optional[Drain], Dict[str, FileOffset]]:
        """从文件加载状态
        
        Args:
            preprocessor: 日志预处理器
        
        Returns:
            (Drain实例或None, 文件偏移字典)
        """
        state_file = Path(self.config.state_file)
        
        if not state_file.exists():
            return None, {}
        
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            state = ProcessorState.from_dict(data)
            
            drain = None
            if state.drain_state:
                drain = Drain.from_dict(state.drain_state, preprocessor)
            
            return drain, state.file_offsets
        except (json.JSONDecodeError, KeyError, ValueError):
            return None, {}
    
    def get_file_offset(self, file_path: str, file_offsets: Dict[str, FileOffset]) -> int:
        """获取文件的处理偏移量
        
        Args:
            file_path: 文件路径
            file_offsets: 文件偏移字典
        
        Returns:
            偏移量（字节），0表示从头开始
        """
        abs_path = os.path.abspath(file_path)
        
        if abs_path not in file_offsets:
            return 0
        
        offset_info = file_offsets[abs_path]
        
        # 检查文件inode是否变化
        try:
            stat = os.stat(file_path)
            if stat.st_ino != offset_info.inode:
                return 0
        except OSError:
            return 0
        
        # 检查文件是否比偏移量小（可能被截断了）
        try:
            file_size = os.path.getsize(file_path)
            if offset_info.offset > file_size:
                return 0
        except OSError:
            return 0
        
        return offset_info.offset
    
    def update_file_offset(self, file_path: str, offset: int, file_offsets: Dict[str, FileOffset]):
        """更新文件偏移量
        
        Args:
            file_path: 文件路径
            offset: 新的偏移量
            file_offsets: 文件偏移字典（会被修改）
        """
        abs_path = os.path.abspath(file_path)
        inode = 0
        
        try:
            stat = os.stat(file_path)
            inode = stat.st_ino
        except OSError:
            pass
        
        file_offsets[abs_path] = FileOffset(
            path=abs_path,
            offset=offset,
            inode=inode,
        )
