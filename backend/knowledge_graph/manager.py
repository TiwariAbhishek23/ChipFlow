"""Living knowledge graph for ChipFlow Agent.

NetworkX is the fast in-process representation used by agents. When
``CHIPFLOW_KG_BACKEND=neo4j`` is set, every update is also persisted into Neo4j.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import networkx as nx

from backend.knowledge_graph.neo4j_store import Neo4jGraphStore
from backend.knowledge_graph.rules import seed_design_rules
from backend.models import (
    DesignRuleModel,
    FailureModel,
    FixModel,
    KGEdge,
    KGNode,
    KGSnapshot,
    PortDirection,
    PortModel,
    SynthesisResult,
    ToolOutputModel,
)


class KnowledgeGraphManager:
    """Owns the design knowledge graph and JSON persistence."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.graph = nx.DiGraph()
        self.neo4j = Neo4jGraphStore()
        if self.path.exists():
            self._load()
        self.seed_rules()

    def backend_status(self) -> dict[str, Any]:
        status = self.neo4j.status().as_dict()
        status["networkx_nodes"] = self.graph.number_of_nodes()
        status["networkx_edges"] = self.graph.number_of_edges()
        return status

    def seed_rules(self) -> list[DesignRuleModel]:
        rules = seed_design_rules()
        for rule in rules:
            self.graph.add_node(
                rule.id,
                type="DesignRule",
                label=rule.title,
                data=rule.model_dump(mode="json"),
            )
        self.save()
        return rules

    def add_module_from_verilog(self, verilog: str, iteration: int) -> str | None:
        module_match = re.search(r"\bmodule\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*?)\);", verilog, re.S)
        if not module_match:
            return None
        module_name = module_match.group(1)
        module_id = f"module:{module_name}"
        self.graph.add_node(
            module_id,
            type="Module",
            label=module_name,
            data={"name": module_name, "iteration": iteration},
        )
        for port in self._parse_ports(verilog):
            port_id = f"port:{module_name}.{port.name}"
            self.graph.add_node(
                port_id,
                type="Port",
                label=port.name,
                data=port.model_dump(mode="json"),
            )
            self.graph.add_edge(module_id, port_id, type="contains", label="contains")
        self.save()
        return module_name

    def add_constraint(self, name: str, description: str, target: Any | None = None) -> None:
        node_id = f"constraint:{self._slug(name)}"
        self.graph.add_node(
            node_id,
            type="Constraint",
            label=name,
            data={"name": name, "description": description, "target_value": target},
        )
        if "cell" in name.lower() or "cell" in description.lower():
            self.graph.add_edge("DR_CELL_BUDGET", node_id, type="applies_to", label="applies_to")
        self.save()

    def record_tool_output(self, output: ToolOutputModel, produced_by: str) -> None:
        self.graph.add_node(
            output.id,
            type="ToolOutput",
            label=f"{output.tool}: {output.status}",
            data=output.model_dump(mode="json"),
        )
        self.graph.add_edge(produced_by, output.id, type="produced_by", label="produced_by")
        self.save()

    def record_failure(self, failure: FailureModel) -> None:
        self.graph.add_node(
            failure.id,
            type="Failure",
            label=failure.summary,
            data=failure.model_dump(mode="json"),
        )
        for rule_id in failure.related_rules:
            if self.graph.has_node(rule_id):
                self.graph.add_edge(failure.id, rule_id, type="violates", label="violates")
        self.save()

    def record_fix(self, fix: FixModel) -> None:
        self.graph.add_node(
            fix.id,
            type="Fix",
            label=fix.summary,
            data=fix.model_dump(mode="json"),
        )
        if fix.fixes_failure_id and self.graph.has_node(fix.fixes_failure_id):
            self.graph.add_edge(fix.fixes_failure_id, fix.id, type="fixed_by", label="fixed_by")
        for rule_id in self.find_rule_ids_for_text(fix.summary + " " + fix.details):
            self.graph.add_edge(fix.id, rule_id, type="applies_to", label="applies_to")
        self.save()

    def record_synthesis(self, result: SynthesisResult, module_name: str) -> None:
        output = ToolOutputModel(
            id=f"tool:synthesis:{result.tool}:{len(self.graph.nodes)}",
            tool=result.tool,
            status="ok" if result.passed else "failed",
            command=result.command,
            stdout=result.stdout[-4000:],
            stderr=result.stderr[-4000:],
            return_code=result.return_code,
            summary=result.summary,
        )
        self.record_tool_output(output, f"module:{module_name}")
        if result.cell_count is not None:
            self.add_constraint(
                "cell_count",
                f"Latest synthesized design uses {result.cell_count} cells.",
                result.cell_count,
            )

    def query(self, text: str = "", type_filter: str | None = None, limit: int = 20) -> KGSnapshot:
        text_l = text.lower().strip()
        nodes: list[KGNode] = []
        selected_ids: set[str] = set()
        for node_id, attrs in self.graph.nodes(data=True):
            if type_filter and attrs.get("type") != type_filter:
                continue
            searchable = json.dumps(attrs, sort_keys=True).lower()
            if text_l and text_l not in searchable:
                continue
            nodes.append(
                KGNode(
                    id=str(node_id),
                    type=str(attrs.get("type", "Unknown")),
                    label=str(attrs.get("label", node_id)),
                    data=dict(attrs.get("data", {})),
                )
            )
            selected_ids.add(str(node_id))
            if len(nodes) >= limit:
                break
        edges = [
            KGEdge(
                source=str(source),
                target=str(target),
                type=str(attrs.get("type", "related")),
                label=attrs.get("label"),
                data=dict(attrs.get("data", {})),
            )
            for source, target, attrs in self.graph.edges(data=True)
            if str(source) in selected_ids or str(target) in selected_ids
        ]
        return KGSnapshot(nodes=nodes, edges=edges)

    def get_design_rules(self, tags: list[str] | None = None) -> list[DesignRuleModel]:
        rules: list[DesignRuleModel] = []
        tag_set = set(tags or [])
        for _, attrs in self.graph.nodes(data=True):
            if attrs.get("type") != "DesignRule":
                continue
            rule = DesignRuleModel.model_validate(attrs.get("data", {}))
            if tag_set and not tag_set.intersection(rule.tags):
                continue
            rules.append(rule)
        return rules

    def get_past_failures_and_fixes(self, query: str) -> list[dict[str, Any]]:
        query_l = query.lower()
        records: list[dict[str, Any]] = []
        for node_id, attrs in self.graph.nodes(data=True):
            if attrs.get("type") != "Failure":
                continue
            blob = json.dumps(attrs, sort_keys=True).lower()
            if query_l and query_l not in blob:
                continue
            fixes = []
            for _, fix_id, edge_attrs in self.graph.out_edges(node_id, data=True):
                if edge_attrs.get("type") == "fixed_by":
                    fixes.append(self.graph.nodes[fix_id].get("data", {}))
            records.append({"failure": attrs.get("data", {}), "fixes": fixes})
        return records

    def find_rule_ids_for_text(self, text: str) -> list[str]:
        text_l = text.lower()
        hits: list[str] = []
        for rule in self.get_design_rules():
            terms = set(rule.tags + rule.title.lower().split())
            if any(term and term in text_l for term in terms):
                hits.append(rule.id)
        return hits

    def snapshot(self) -> KGSnapshot:
        nodes = [
            KGNode(
                id=str(node_id),
                type=str(attrs.get("type", "Unknown")),
                label=str(attrs.get("label", node_id)),
                data=dict(attrs.get("data", {})),
            )
            for node_id, attrs in self.graph.nodes(data=True)
        ]
        edges = [
            KGEdge(
                source=str(source),
                target=str(target),
                type=str(attrs.get("type", "related")),
                label=attrs.get("label"),
                data=dict(attrs.get("data", {})),
            )
            for source, target, attrs in self.graph.edges(data=True)
        ]
        return KGSnapshot(nodes=nodes, edges=edges)

    def neo4j_snapshot(self, limit: int = 200) -> KGSnapshot | None:
        return self.neo4j.snapshot(limit)

    def run_neo4j_read_query(self, cypher: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.neo4j.run_read_query(cypher, limit)

    def save(self) -> None:
        data = nx.node_link_data(self.graph)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.neo4j.sync_graph(self.graph)

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.graph = nx.node_link_graph(data)
        except (json.JSONDecodeError, FileNotFoundError, TypeError):
            self.graph = nx.DiGraph()

    @staticmethod
    def _parse_ports(verilog: str) -> list[PortModel]:
        ports: list[PortModel] = []
        pattern = re.compile(
            r"\b(input|output|inout)\s+(?:(wire|reg|logic)\s+)?(?:\[(\d+)\s*:\s*(\d+)\]\s+)?([a-zA-Z_][a-zA-Z0-9_]*)",
            re.M,
        )
        for match in pattern.finditer(verilog):
            direction, kind, msb, lsb, name = match.groups()
            width = abs(int(msb) - int(lsb)) + 1 if msb and lsb else 1
            ports.append(
                PortModel(
                    name=name,
                    direction=PortDirection(direction),
                    width=width,
                    kind=kind,
                )
            )
        return ports

    @staticmethod
    def _slug(text: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_")
