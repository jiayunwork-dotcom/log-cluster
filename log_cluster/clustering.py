"""模式聚类模块 - 对提取的模板进行层级聚类"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import ClusteringConfig
from .drain import LogTemplate


@dataclass
class TemplateCluster:
    """模板聚类组"""
    cluster_id: str
    representative: LogTemplate
    templates: List[LogTemplate] = field(default_factory=list)
    total_count: int = 0
    first_seen = None
    last_seen = None
    
    def __post_init__(self):
        self._calculate_stats()
    
    def _calculate_stats(self):
        """计算聚类组的统计信息"""
        self.total_count = sum(t.stats.count for t in self.templates)
        
        first_seen_list = [t.stats.first_seen for t in self.templates if t.stats.first_seen]
        self.first_seen = min(first_seen_list) if first_seen_list else None
        
        last_seen_list = [t.stats.last_seen for t in self.templates if t.stats.last_seen]
        self.last_seen = max(last_seen_list) if last_seen_list else None
    
    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "representative_id": self.representative.template_id,
            "representative_str": self.representative.template_str,
            "total_count": self.total_count,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "templates": [t.template_id for t in self.templates],
        }


def _normalize_edit_distance(tokens1: List[str], tokens2: List[str]) -> float:
    """计算归一化编辑距离（基于token级别的Levenshtein距离）"""
    m, n = len(tokens1), len(tokens2)
    
    if m == 0 and n == 0:
        return 0.0
    
    # 创建距离矩阵
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if tokens1[i - 1] == tokens2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])
    
    max_len = max(m, n)
    if max_len == 0:
        return 0.0
    
    return dp[m][n] / max_len


class TemplateClustering:
    """模板聚类器"""
    
    def __init__(self, config: ClusteringConfig):
        self.config = config
        self.clusters: List[TemplateCluster] = []
        self._cluster_counter = 0
    
    def cluster(self, templates: List[LogTemplate]) -> List[TemplateCluster]:
        """对模板列表进行聚类
        
        使用简单的贪心聚类算法：
        1. 按频率排序模板
        2. 依次为每个模板找到最相似的聚类
        3. 如果相似度高于阈值，则加入该聚类，否则创建新聚类
        """
        self._cluster_counter = 0
        self.clusters = []
        
        # 按频率降序排序
        sorted_templates = sorted(templates, key=lambda t: t.stats.count, reverse=True)
        
        for template in sorted_templates:
            best_cluster = None
            best_dist = float("inf")
            
            # 找到最相似的聚类
            for cluster in self.clusters:
                dist = _normalize_edit_distance(template.tokens, cluster.representative.tokens)
                if dist < best_dist and dist < self.config.merge_threshold:
                    best_dist = dist
                    best_cluster = cluster
            
            if best_cluster is not None:
                best_cluster.templates.append(template)
                template.cluster_id = best_cluster.cluster_id
                best_cluster._calculate_stats()
            else:
                # 创建新聚类
                self._cluster_counter += 1
                cluster_id = f"C{self._cluster_counter:04d}"
                new_cluster = TemplateCluster(
                    cluster_id=cluster_id,
                    representative=template,
                    templates=[template],
                )
                template.cluster_id = cluster_id
                self.clusters.append(new_cluster)
        
        # 按总频率排序聚类
        self.clusters.sort(key=lambda c: c.total_count, reverse=True)
        
        return self.clusters
    
    def get_clusters_sorted(self) -> List[TemplateCluster]:
        """获取排序后的聚类列表"""
        return sorted(self.clusters, key=lambda c: c.total_count, reverse=True)
