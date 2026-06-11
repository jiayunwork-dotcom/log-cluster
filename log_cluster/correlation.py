"""日志异常关联分析模块 - 关联规则挖掘与因果链发现"""
from __future__ import annotations

import json
import math
import os
import sys
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class CorrelateConfig:
    """关联分析配置"""
    window_size: int = 60
    min_support: float = 0.01
    min_confidence: float = 0.5
    min_lift: float = 2.0
    burst_threshold: int = 100
    min_count: int = 10


@dataclass
class AssociationRule:
    """关联规则"""
    source_template_id: str
    target_template_id: str
    support: float = 0.0
    confidence: float = 0.0
    lift: float = 0.0
    chi2: float = 0.0
    jaccard: float = 0.0
    co_count: int = 0
    a_count: int = 0
    b_count: int = 0
    is_strong_correlation: bool = False
    is_burst: bool = False
    is_simultaneous: bool = False
    is_significant: bool = False

    def to_dict(self) -> dict:
        return {
            "source_template_id": self.source_template_id,
            "target_template_id": self.target_template_id,
            "support": round(self.support, 6),
            "confidence": round(self.confidence, 6),
            "lift": round(self.lift, 6),
            "chi2": round(self.chi2, 6),
            "jaccard": round(self.jaccard, 6),
            "co_count": self.co_count,
            "a_count": self.a_count,
            "b_count": self.b_count,
            "is_strong_correlation": self.is_strong_correlation,
            "is_burst": self.is_burst,
            "is_simultaneous": self.is_simultaneous,
            "is_significant": self.is_significant,
        }


@dataclass
class CausalChain:
    """因果链"""
    path: List[str] = field(default_factory=list)
    edge_weights: List[float] = field(default_factory=list)
    total_confidence: float = 0.0
    length: int = 0

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "edge_weights": [round(w, 6) for w in self.edge_weights],
            "total_confidence": round(self.total_confidence, 6),
            "length": self.length,
        }


@dataclass
class CorrelationResult:
    """关联分析结果"""
    rules: List[AssociationRule] = field(default_factory=list)
    chains: List[CausalChain] = field(default_factory=list)
    burst_templates: Set[str] = field(default_factory=set)
    simultaneous_pairs: Set[Tuple[str, str]] = field(default_factory=set)
    total_events: int = 0
    template_count: int = 0
    filtered_template_count: int = 0


@dataclass
class CorrelationState:
    """关联分析增量状态"""
    co_occurrence: Dict[Tuple[str, str], Tuple[int, int]] = field(default_factory=dict)
    template_counts: Dict[str, int] = field(default_factory=dict)
    total_events: int = 0
    last_analyzed_timestamp: Optional[str] = None

    def to_dict(self) -> dict:
        co_occurrence_serialized: Dict[str, List[int]] = {}
        for (a, b), (cnt, sim_cnt) in self.co_occurrence.items():
            key = f"{a}|{b}"
            co_occurrence_serialized[key] = [cnt, sim_cnt]
        return {
            "version": "1.0",
            "co_occurrence": co_occurrence_serialized,
            "template_counts": dict(self.template_counts),
            "total_events": self.total_events,
            "last_analyzed_timestamp": self.last_analyzed_timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CorrelationState:
        co_occurrence: Dict[Tuple[str, str], Tuple[int, int]] = {}
        for key, counts in data.get("co_occurrence", {}).items():
            parts = key.split("|", 1)
            if len(parts) == 2 and len(counts) == 2:
                co_occurrence[(parts[0], parts[1])] = (int(counts[0]), int(counts[1]))
        return cls(
            co_occurrence=co_occurrence,
            template_counts={k: int(v) for k, v in data.get("template_counts", {}).items()},
            total_events=int(data.get("total_events", 0)),
            last_analyzed_timestamp=data.get("last_analyzed_timestamp"),
        )


def save_correlation_state(state: CorrelationState, path: str):
    """保存关联分析状态到JSON文件"""
    data = {"correlation_state": state.to_dict()}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_correlation_state(path: str) -> Optional[CorrelationState]:
    """加载关联分析状态，文件不存在返回None"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        state_data = data.get("correlation_state", {})
        if not state_data:
            return None
        return CorrelationState.from_dict(state_data)
    except (json.JSONDecodeError, OSError, KeyError):
        return None


class _TarjanSCC:
    """Tarjan算法找强连通分量"""

    def __init__(self, graph: Dict[str, List[str]]):
        self.graph = graph
        self.index_counter = 0
        self.stack: List[str] = []
        self.on_stack: Set[str] = set()
        self.index: Dict[str, int] = {}
        self.lowlink: Dict[str, int] = {}
        self.sccs: List[List[str]] = []

    def find_sccs(self) -> List[List[str]]:
        for node in self.graph:
            if node not in self.index:
                self._strongconnect(node)
        return self.sccs

    def _strongconnect(self, v: str):
        self.index[v] = self.index_counter
        self.lowlink[v] = self.index_counter
        self.index_counter += 1
        self.stack.append(v)
        self.on_stack.add(v)

        for w in self.graph.get(v, []):
            if w not in self.index:
                self._strongconnect(w)
                self.lowlink[v] = min(self.lowlink[v], self.lowlink[w])
            elif w in self.on_stack:
                self.lowlink[v] = min(self.lowlink[v], self.index[w])

        if self.lowlink[v] == self.index[v]:
            scc = []
            while True:
                w = self.stack.pop()
                self.on_stack.discard(w)
                scc.append(w)
                if w == v:
                    break
            self.sccs.append(scc)


class CorrelationAnalyzer:
    """日志异常关联分析器"""

    CHI2_THRESHOLD = 3.841
    STRONG_JACCARD = 0.8
    SIMULTANEOUS_THRESHOLD_SEC = 1.0

    def __init__(self, config: CorrelateConfig):
        if config.window_size < 5 or config.window_size > 3600:
            print(
                f"错误: window_size={config.window_size} 超出有效范围 [5, 3600]",
                file=sys.stderr,
            )
            sys.exit(1)
        self.config = config
        self._last_state: Optional[CorrelationState] = None

    def analyze(
        self,
        template_timestamps: Dict[str, List[datetime]],
        template_ids: Optional[List[str]] = None,
        loaded_state: Optional[CorrelationState] = None,
    ) -> CorrelationResult:
        """执行关联分析

        Args:
            template_timestamps: 模板ID到时间戳列表的映射
            template_ids: 可选的模板ID列表（用于从状态文件加载）
            loaded_state: 可选的已有关联状态（增量合并）

        Returns:
            CorrelationResult 分析结果
        """
        result = CorrelationResult()

        if template_ids is None:
            all_template_ids = list(template_timestamps.keys())
        else:
            all_template_ids = list(template_ids)

        if loaded_state is not None:
            for tid in loaded_state.template_counts:
                if tid not in all_template_ids:
                    all_template_ids.append(tid)

        result.template_count = len(all_template_ids)

        new_template_counts = {
            tid: len(template_timestamps.get(tid, [])) for tid in all_template_ids
        }
        new_total_events = sum(new_template_counts.values())

        template_counts = dict(new_template_counts)
        total_events = new_total_events
        if loaded_state is not None:
            for tid, count in loaded_state.template_counts.items():
                template_counts[tid] = template_counts.get(tid, 0) + count
            total_events += loaded_state.total_events

        result.total_events = total_events

        if total_events == 0:
            self._save_internal_state(
                co_occurrence={}, template_counts=template_counts,
                total_events=0, template_timestamps=template_timestamps,
                loaded_state=loaded_state,
            )
            return result

        filtered_template_ids = self._filter_rare_templates(
            all_template_ids, template_counts
        )
        result.filtered_template_count = len(filtered_template_ids)

        if len(filtered_template_ids) < 2:
            self._save_internal_state(
                co_occurrence={}, template_counts=template_counts,
                total_events=total_events, template_timestamps=template_timestamps,
                loaded_state=loaded_state,
            )
            return result

        sorted_timestamps, template_id_to_events = self._build_event_sequence(
            template_timestamps, filtered_template_ids
        )

        burst_templates: Set[str] = set()
        if sorted_timestamps:
            burst_templates = self._detect_burst_templates(
                template_timestamps, filtered_template_ids
            )
        result.burst_templates = burst_templates

        co_occurrence: Dict[Tuple[str, str], Tuple[int, int]] = defaultdict(
            lambda: (0, 0)
        )
        simultaneous_pairs: Set[Tuple[str, str]] = set()

        if sorted_timestamps:
            self._count_co_occurrences(
                sorted_timestamps,
                template_id_to_events,
                co_occurrence,
                simultaneous_pairs,
                burst_templates,
            )

        if loaded_state is not None:
            for key, (cnt, sim_cnt) in loaded_state.co_occurrence.items():
                if key in co_occurrence:
                    old_cnt, old_sim = co_occurrence[key]
                    co_occurrence[key] = (old_cnt + cnt, old_sim + sim_cnt)
                else:
                    co_occurrence[key] = (cnt, sim_cnt)

        result.simultaneous_pairs = simultaneous_pairs

        rules = self._compute_rules(
            co_occurrence,
            template_counts,
            total_events,
            burst_templates,
            simultaneous_pairs,
        )

        significant_rules = [r for r in rules if r.is_significant and not r.is_burst]
        burst_rules = [r for r in rules if r.is_burst and r.is_significant]

        dag_rules = [r for r in significant_rules if not r.is_simultaneous]

        chains = self._find_causal_chains(dag_rules, filtered_template_ids)

        result.rules = sorted(
            significant_rules + burst_rules, key=lambda r: r.lift, reverse=True
        )
        result.chains = chains

        self._save_internal_state(
            co_occurrence=dict(co_occurrence),
            template_counts=template_counts,
            total_events=total_events,
            template_timestamps=template_timestamps,
            loaded_state=loaded_state,
        )

        return result

    def _save_internal_state(
        self,
        co_occurrence: Dict[Tuple[str, str], Tuple[int, int]],
        template_counts: Dict[str, int],
        total_events: int,
        template_timestamps: Dict[str, List[datetime]],
        loaded_state: Optional[CorrelationState],
    ):
        latest_ts: Optional[datetime] = None
        for ts_list in template_timestamps.values():
            for ts in ts_list:
                if latest_ts is None or ts > latest_ts:
                    latest_ts = ts

        if latest_ts is not None:
            last_analyzed_ts = latest_ts.isoformat()
        elif loaded_state is not None and loaded_state.last_analyzed_timestamp:
            last_analyzed_ts = loaded_state.last_analyzed_timestamp
        else:
            last_analyzed_ts = None

        self._last_state = CorrelationState(
            co_occurrence=co_occurrence,
            template_counts=template_counts,
            total_events=total_events,
            last_analyzed_timestamp=last_analyzed_ts,
        )

    def get_state(self) -> Optional[CorrelationState]:
        return self._last_state

    def _filter_rare_templates(
        self, template_ids: List[str], template_counts: Dict[str, int]
    ) -> List[str]:
        """过滤稀有模板"""
        if len(template_ids) <= 100:
            return template_ids
        return [
            tid
            for tid in template_ids
            if template_counts.get(tid, 0) >= self.config.min_count
        ]

    def _build_event_sequence(
        self,
        template_timestamps: Dict[str, List[datetime]],
        template_ids: List[str],
    ) -> Tuple[List[datetime], Dict[datetime, List[str]]]:
        """构建按时间排序的事件序列"""
        time_to_templates: Dict[datetime, List[str]] = defaultdict(list)
        all_timestamps: Set[datetime] = set()

        for tid in template_ids:
            ts_list = template_timestamps.get(tid, [])
            for ts in ts_list:
                time_to_templates[ts].append(tid)
                all_timestamps.add(ts)

        sorted_timestamps = sorted(all_timestamps)
        return sorted_timestamps, time_to_templates

    def _detect_burst_templates(
        self,
        template_timestamps: Dict[str, List[datetime]],
        template_ids: List[str],
    ) -> Set[str]:
        """检测突发模板"""
        burst_templates: Set[str] = set()
        window_sec = self.config.window_size

        for tid in template_ids:
            ts_list = sorted(template_timestamps.get(tid, []))
            if len(ts_list) < 2:
                continue

            is_burst = False
            for i in range(len(ts_list)):
                start_ts = ts_list[i]
                start_epoch = start_ts.timestamp()
                end_epoch = start_epoch + window_sec

                left = i
                right = len(ts_list)
                while left < right:
                    mid = (left + right) // 2
                    if ts_list[mid].timestamp() <= end_epoch:
                        left = mid + 1
                    else:
                        right = mid
                count_in_window = left - i

                if count_in_window > self.config.burst_threshold:
                    is_burst = True
                    break

            if is_burst:
                burst_templates.add(tid)

        return burst_templates

    def _count_co_occurrences(
        self,
        sorted_timestamps: List[datetime],
        template_id_to_events: Dict[datetime, List[str]],
        co_occurrence: Dict[Tuple[str, str], Tuple[int, int]],
        simultaneous_pairs: Set[Tuple[str, str]],
        burst_templates: Set[str],
    ):
        """使用滑动窗口统计共现次数（二分查找定位边界）"""
        window_sec = self.config.window_size
        simultaneous_sec = self.SIMULTANEOUS_THRESHOLD_SEC
        n = len(sorted_timestamps)
        epoch_list = [ts.timestamp() for ts in sorted_timestamps]

        for i in range(n):
            current_epoch = epoch_list[i]
            window_end_epoch = current_epoch + window_sec

            templates_at_i = template_id_to_events[sorted_timestamps[i]]

            right_idx = bisect_right(epoch_list, window_end_epoch)

            for a_tid in templates_at_i:
                b_seen_sim: Set[str] = set()
                b_seen_window: Set[str] = set()

                for j in range(i, right_idx):
                    ts_j = sorted_timestamps[j]
                    is_sim = (epoch_list[j] - current_epoch) < simultaneous_sec
                    templates_at_j = template_id_to_events[ts_j]

                    for b_tid in templates_at_j:
                        if b_tid == a_tid:
                            continue
                        if is_sim:
                            if b_tid not in b_seen_sim:
                                b_seen_sim.add(b_tid)
                                pair = tuple(sorted([a_tid, b_tid]))
                                simultaneous_pairs.add(pair)
                        else:
                            if b_tid not in b_seen_sim and b_tid not in b_seen_window:
                                b_seen_window.add(b_tid)

                for b_tid in b_seen_sim:
                    key = (a_tid, b_tid)
                    cnt, sim_cnt = co_occurrence[key]
                    co_occurrence[key] = (cnt + 1, sim_cnt + 1)

                for b_tid in b_seen_window:
                    if b_tid in b_seen_sim:
                        continue
                    key = (a_tid, b_tid)
                    cnt, sim_cnt = co_occurrence[key]
                    co_occurrence[key] = (cnt + 1, sim_cnt)

        for (tid_a, tid_b) in simultaneous_pairs:
            key_ab = (tid_a, tid_b)
            key_ba = (tid_b, tid_a)
            cnt_ab, sim_ab = co_occurrence[key_ab]
            cnt_ba, sim_ba = co_occurrence[key_ba]
            max_cnt = max(cnt_ab, cnt_ba)
            if cnt_ab < max_cnt:
                co_occurrence[key_ab] = (max_cnt, max_cnt)
            if cnt_ba < max_cnt:
                co_occurrence[key_ba] = (max_cnt, max_cnt)

    def _compute_rules(
        self,
        co_occurrence: Dict[Tuple[str, str], Tuple[int, int]],
        template_counts: Dict[str, int],
        total_events: int,
        burst_templates: Set[str],
        simultaneous_pairs: Set[Tuple[str, str]],
    ) -> List[AssociationRule]:
        """计算关联规则指标"""
        rules: List[AssociationRule] = []

        for (a_id, b_id), (co_count, sim_count) in co_occurrence.items():
            a_count = template_counts.get(a_id, 0)
            b_count = template_counts.get(b_id, 0)

            if a_count == 0 or b_count == 0:
                continue

            effective_co_count = min(co_count, a_count, b_count)
            effective_sim = min(sim_count, effective_co_count)

            support = effective_co_count / total_events
            confidence = effective_co_count / a_count if a_count > 0 else 0.0
            b_prob = b_count / total_events
            lift = confidence / b_prob if b_prob > 0 else 0.0

            e_ab = (a_count * b_count) / total_events
            chi2 = ((effective_co_count - e_ab) ** 2) / e_ab if e_ab > 0 else 0.0

            union_count = a_count + b_count - effective_co_count
            jaccard = effective_co_count / union_count if union_count > 0 else 0.0

            is_simultaneous = (effective_sim == effective_co_count) and effective_co_count > 0

            is_burst = (a_id in burst_templates) or (b_id in burst_templates)

            is_significant = (
                support >= self.config.min_support
                and confidence >= self.config.min_confidence
                and lift >= self.config.min_lift
                and chi2 >= self.CHI2_THRESHOLD
            )

            is_strong = jaccard > self.STRONG_JACCARD

            rule = AssociationRule(
                source_template_id=a_id,
                target_template_id=b_id,
                support=support,
                confidence=confidence,
                lift=lift,
                chi2=chi2,
                jaccard=jaccard,
                co_count=effective_co_count,
                a_count=a_count,
                b_count=b_count,
                is_strong_correlation=is_strong,
                is_burst=is_burst,
                is_simultaneous=is_simultaneous,
                is_significant=is_significant,
            )
            rules.append(rule)

        return rules

    def _filter_bidirectional_edges(
        self, rules: List[AssociationRule]
    ) -> List[AssociationRule]:
        """过滤双向边：对双向边方向筛选，优先保留置信度高的方向"""
        if not rules:
            return []

        edge_map: Dict[Tuple[str, str], AssociationRule] = {}
        for r in rules:
            edge_map[(r.source_template_id, r.target_template_id)] = r

        kept_rules: List[AssociationRule] = []
        processed: Set[Tuple[str, str]] = set()

        for (a, b), rule_ab in edge_map.items():
            pair_key = tuple(sorted([a, b]))
            if pair_key in processed:
                continue
            processed.add(pair_key)

            rule_ba = edge_map.get((b, a))
            if rule_ba is None:
                kept_rules.append(rule_ab)
                continue

            conf_ab = rule_ab.confidence
            conf_ba = rule_ba.confidence
            co_ab = rule_ab.co_count
            co_ba = rule_ba.co_count

            conf_diff = abs(conf_ab - conf_ba)
            co_diff_ratio = abs(co_ab - co_ba) / max(co_ab, co_ba, 1)

            if conf_diff >= 0.05 or co_diff_ratio >= 0.05:
                if conf_ab > conf_ba or (conf_ab == conf_ba and co_ab >= co_ba):
                    kept_rules.append(rule_ab)
                else:
                    kept_rules.append(rule_ba)
            else:
                if rule_ab.is_simultaneous and rule_ba.is_simultaneous:
                    pass
                else:
                    if conf_ab >= conf_ba:
                        kept_rules.append(rule_ab)
                    else:
                        kept_rules.append(rule_ba)

        return kept_rules

    def _find_causal_chains(
        self, rules: List[AssociationRule], template_ids: List[str]
    ) -> List[CausalChain]:
        """发现因果链（有向图最长路径）"""
        if not rules:
            return []

        filtered_rules = self._filter_bidirectional_edges(rules)

        graph: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        nodes: Set[str] = set()

        for rule in filtered_rules:
            graph[rule.source_template_id].append(
                (rule.target_template_id, rule.confidence)
            )
            nodes.add(rule.source_template_id)
            nodes.add(rule.target_template_id)

        simple_graph: Dict[str, List[str]] = defaultdict(list)
        for src, edges in graph.items():
            simple_graph[src] = [dst for dst, _ in edges]
        for node in nodes:
            if node not in simple_graph:
                simple_graph[node] = []

        if not simple_graph:
            return []

        tarjan = _TarjanSCC(simple_graph)
        sccs = tarjan.find_sccs()

        node_to_group: Dict[str, str] = {}
        group_to_nodes: Dict[str, List[str]] = {}
        for idx, scc in enumerate(sccs):
            group_id = f"GROUP_{idx}"
            for node in scc:
                node_to_group[node] = group_id
            group_to_nodes[group_id] = scc

        condensed_graph: Dict[str, Dict[str, float]] = defaultdict(dict)
        group_set: Set[str] = set(group_to_nodes.keys())

        for src_group in group_set:
            src_nodes = group_to_nodes[src_group]
            for src_node in src_nodes:
                for dst_node, weight in graph.get(src_node, []):
                    dst_group = node_to_group.get(dst_node, dst_node)
                    if dst_group == src_group:
                        continue
                    if (
                        dst_group not in condensed_graph[src_group]
                        or condensed_graph[src_group][dst_group] < weight
                    ):
                        condensed_graph[src_group][dst_group] = weight

        for group in group_set:
            if group not in condensed_graph:
                condensed_graph[group] = {}

        in_degree: Dict[str, int] = {g: 0 for g in group_set}
        for src in condensed_graph:
            for dst in condensed_graph[src]:
                in_degree[dst] = in_degree.get(dst, 0) + 1

        topo_order: List[str] = []
        queue: List[str] = [g for g in group_set if in_degree[g] == 0]
        temp_in_degree = dict(in_degree)

        while queue:
            node = queue.pop(0)
            topo_order.append(node)
            for neighbor in condensed_graph[node]:
                temp_in_degree[neighbor] -= 1
                if temp_in_degree[neighbor] == 0:
                    queue.append(neighbor)

        dist: Dict[str, float] = {g: 0.0 for g in group_set}
        prev: Dict[str, Optional[Tuple[str, float]]] = {g: None for g in group_set}

        for node in topo_order:
            for neighbor, weight in condensed_graph[node].items():
                new_dist = dist[node] + weight
                if new_dist > dist[neighbor]:
                    dist[neighbor] = new_dist
                    prev[neighbor] = (node, weight)

        chains: List[CausalChain] = []
        sink_candidates = sorted(
            group_set, key=lambda g: dist[g], reverse=True
        )
        for end_group in sink_candidates:
            chain = self._reconstruct_chain(
                end_group, prev, group_to_nodes, condensed_graph
            )
            if chain.length >= 2:
                chains.append(chain)

        unique_chains: List[CausalChain] = []
        seen_paths = set()
        for c in chains:
            path_key = tuple(c.path)
            if path_key not in seen_paths:
                seen_paths.add(path_key)
                unique_chains.append(c)

        return sorted(unique_chains, key=lambda c: (c.length, c.total_confidence), reverse=True)

    def _reconstruct_chain(
        self,
        end_group: str,
        prev: Dict[str, Optional[Tuple[str, float]]],
        group_to_nodes: Dict[str, List[str]],
        condensed_graph: Dict[str, Dict[str, float]],
    ) -> CausalChain:
        """重建因果链路径"""
        group_path: List[str] = []
        weights: List[float] = []
        current: Optional[str] = end_group

        while current is not None:
            group_path.append(current)
            prev_info = prev[current]
            if prev_info is None:
                break
            prev_group, weight = prev_info
            weights.append(weight)
            current = prev_group

        group_path.reverse()
        weights.reverse()

        full_path: List[str] = []
        for i, group in enumerate(group_path):
            nodes = group_to_nodes[group]
            if len(nodes) == 1:
                full_path.append(nodes[0])
            else:
                full_path.append("[" + ", ".join(nodes) + "]")

        chain = CausalChain(
            path=full_path,
            edge_weights=weights,
            total_confidence=sum(weights),
            length=len(full_path),
        )
        return chain
