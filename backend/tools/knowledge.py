"""Typed knowledge graph tool wrappers used by agents."""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.knowledge_graph.manager import KnowledgeGraphManager
from backend.models import DesignRuleModel, KGSnapshot


class KnowledgeQueryInput(BaseModel):
    query: str = ""
    type_filter: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


def query_knowledge_graph(manager: KnowledgeGraphManager, payload: KnowledgeQueryInput) -> KGSnapshot:
    return manager.query(payload.query, payload.type_filter, payload.limit)


def get_relevant_design_rules(
    manager: KnowledgeGraphManager,
    *,
    tags: list[str] | None = None,
) -> list[DesignRuleModel]:
    return manager.get_design_rules(tags)


def get_past_failures_and_fixes(
    manager: KnowledgeGraphManager,
    query: str,
) -> list[dict]:
    return manager.get_past_failures_and_fixes(query)

