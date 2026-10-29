"""Optional Neo4j persistence for the ChipFlow knowledge graph."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import networkx as nx

from backend.models import KGEdge, KGNode, KGSnapshot


@dataclass(frozen=True)
class Neo4jStatus:
    requested: bool
    available: bool
    uri: str | None
    database: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend": "neo4j" if self.requested else "networkx",
            "requested": self.requested,
            "available": self.available,
            "uri": self.uri,
            "database": self.database,
            "message": self.message,
        }


class Neo4jGraphStore:
    """Persists NetworkX graph snapshots into Neo4j with Cypher MERGE queries."""

    def __init__(self) -> None:
        self.requested = os.getenv("CHIPFLOW_KG_BACKEND", "networkx").lower() == "neo4j"
        self.uri = os.getenv("NEO4J_URI")
        self.user = os.getenv("NEO4J_USER", "neo4j")
        self.password = os.getenv("NEO4J_PASSWORD", "chipflow-neo4j")
        self.database = os.getenv("NEO4J_DATABASE", "neo4j")
        self._driver: Any | None = None
        self._last_error: str | None = None
        if self.requested:
            self._connect()

    @property
    def available(self) -> bool:
        return self._driver is not None

    def status(self) -> Neo4jStatus:
        if not self.requested:
            return Neo4jStatus(
                requested=False,
                available=False,
                uri=None,
                database=self.database,
                message="Using NetworkX/JSON knowledge graph backend.",
            )
        if self.available:
            return Neo4jStatus(
                requested=True,
                available=True,
                uri=self.uri,
                database=self.database,
                message="Neo4j knowledge graph backend is connected.",
            )
        return Neo4jStatus(
            requested=True,
            available=False,
            uri=self.uri,
            database=self.database,
            message=self._last_error or "Neo4j backend requested but not connected.",
        )

    def sync_graph(self, graph: nx.DiGraph) -> None:
        driver = self._driver_or_reconnect()
        if driver is None:
            return
        self._ensure_constraints(driver)
        for node_id, attrs in graph.nodes(data=True):
            self.upsert_node(
                KGNode(
                    id=str(node_id),
                    type=str(attrs.get("type", "Unknown")),
                    label=str(attrs.get("label", node_id)),
                    data=dict(attrs.get("data", {})),
                )
            )
        for source, target, attrs in graph.edges(data=True):
            self.upsert_edge(
                KGEdge(
                    source=str(source),
                    target=str(target),
                    type=str(attrs.get("type", "related")),
                    label=attrs.get("label"),
                    data=dict(attrs.get("data", {})),
                )
            )

    def upsert_node(self, node: KGNode) -> None:
        driver = self._driver_or_reconnect()
        if driver is None:
            return
        type_label = _safe_label(node.type)
        driver.execute_query(
            f"""
            MERGE (n:ChipFlowNode {{id: $id}})
            SET n:{type_label},
                n.type = $type,
                n.label = $label,
                n.data_json = $data_json,
                n.updated_at = datetime()
            """,
            id=node.id,
            type=node.type,
            label=node.label,
            data_json=json.dumps(node.data, sort_keys=True),
            database_=self.database,
        )

    def upsert_edge(self, edge: KGEdge) -> None:
        driver = self._driver_or_reconnect()
        if driver is None:
            return
        rel_type = _safe_relationship(edge.type)
        driver.execute_query(
            f"""
            MATCH (source:ChipFlowNode {{id: $source}})
            MATCH (target:ChipFlowNode {{id: $target}})
            MERGE (source)-[rel:{rel_type}]->(target)
            SET rel.type = $type,
                rel.label = $label,
                rel.data_json = $data_json,
                rel.updated_at = datetime()
            """,
            source=edge.source,
            target=edge.target,
            type=edge.type,
            label=edge.label or edge.type,
            data_json=json.dumps(edge.data, sort_keys=True),
            database_=self.database,
        )

    def snapshot(self, limit: int = 200) -> KGSnapshot | None:
        driver = self._driver_or_reconnect()
        if driver is None:
            return None
        records, _, _ = driver.execute_query(
            """
            MATCH (n:ChipFlowNode)
            OPTIONAL MATCH (n)-[r]->(m:ChipFlowNode)
            RETURN n, r, m
            LIMIT $limit
            """,
            limit=limit,
            database_=self.database,
        )
        nodes_by_id: dict[str, KGNode] = {}
        edges: list[KGEdge] = []
        for record in records:
            for key in ("n", "m"):
                node = record.get(key)
                if node is None:
                    continue
                data = _loads_json(node.get("data_json"))
                nodes_by_id[node["id"]] = KGNode(
                    id=node["id"],
                    type=node.get("type", "Unknown"),
                    label=node.get("label", node["id"]),
                    data=data,
                )
            rel = record.get("r")
            source = record.get("n")
            target = record.get("m")
            if rel is not None and source is not None and target is not None:
                edges.append(
                    KGEdge(
                        source=source["id"],
                        target=target["id"],
                        type=rel.get("type", rel.type),
                        label=rel.get("label"),
                        data=_loads_json(rel.get("data_json")),
                    )
                )
        return KGSnapshot(nodes=list(nodes_by_id.values()), edges=edges)

    def run_read_query(self, cypher: str, limit: int = 50) -> list[dict[str, Any]]:
        driver = self._driver_or_reconnect()
        if driver is None:
            raise RuntimeError(self.status().message)
        if not _is_read_only(cypher):
            raise ValueError("Only read-only MATCH/RETURN Cypher queries are allowed.")
        from neo4j import RoutingControl

        # READ routing runs the query in a read-access transaction, so the server
        # rejects writes even if a query slips past the keyword check above.
        records, _, _ = driver.execute_query(
            cypher,
            limit=limit,
            database_=self.database,
            routing_=RoutingControl.READ,
        )
        return [_record_to_plain_dict(record.data()) for record in records[:limit]]

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def _connect(self) -> None:
        if not self.uri:
            self._last_error = "NEO4J_URI is not set."
            return
        try:
            from neo4j import GraphDatabase

            self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            self._driver.verify_connectivity()
            self._last_error = None
        except Exception as exc:
            self._driver = None
            self._last_error = f"Neo4j connection failed: {exc}"

    def _driver_or_reconnect(self) -> Any | None:
        if not self.requested:
            return None
        if self._driver is None:
            self._connect()
        return self._driver

    def _ensure_constraints(self, driver: Any) -> None:
        driver.execute_query(
            "CREATE CONSTRAINT chipflow_node_id IF NOT EXISTS FOR (n:ChipFlowNode) REQUIRE n.id IS UNIQUE",
            database_=self.database,
        )


def _safe_label(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_") or "Unknown"
    if cleaned[0].isdigit():
        cleaned = f"N_{cleaned}"
    return cleaned


def _safe_relationship(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_").upper() or "RELATED"
    if cleaned[0].isdigit():
        cleaned = f"R_{cleaned}"
    return cleaned


def _loads_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _is_read_only(cypher: str) -> bool:
    normalized = " ".join(cypher.lower().split())
    if not normalized.startswith(("match ", "optional match ", "with ")):
        return False
    blocked = [
        " create ",
        " merge ",
        " delete ",
        " detach ",
        " set ",
        " remove ",
        " call dbms",
        " load csv",
        " drop ",
    ]
    padded = f" {normalized} "
    return not any(term in padded for term in blocked)


def _record_to_plain_dict(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _record_to_plain_dict(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_record_to_plain_dict(item) for item in value]
    if hasattr(value, "items"):
        return {str(key): _record_to_plain_dict(item) for key, item in value.items()}
    if hasattr(value, "element_id"):
        return dict(value)
    return value

