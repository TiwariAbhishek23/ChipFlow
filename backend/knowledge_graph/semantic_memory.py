"""Small local semantic memory for successful RTL patterns.

This deliberately avoids a hard dependency on a vector database. It stores
successful pattern summaries as JSON and ranks them with a simple token-overlap
score. Chroma can be added behind this interface later without changing agents.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PatternMatch:
    id: str
    description: str
    code: str
    score: float


class SemanticMemory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def add_successful_pattern(self, *, description: str, code: str) -> None:
        records = self._read()
        pattern_id = f"pattern_{len(records) + 1:04d}"
        if any(item.get("code") == code for item in records):
            return
        records.append({"id": pattern_id, "description": description, "code": code})
        self.path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    def query(self, text: str, limit: int = 3) -> list[PatternMatch]:
        query_tokens = self._tokens(text)
        matches: list[PatternMatch] = []
        for item in self._read():
            haystack = f"{item.get('description', '')} {item.get('code', '')}"
            tokens = self._tokens(haystack)
            score = self._jaccard(query_tokens, tokens)
            if score > 0:
                matches.append(
                    PatternMatch(
                        id=item["id"],
                        description=item.get("description", ""),
                        code=item.get("code", ""),
                        score=score,
                    )
                )
        return sorted(matches, key=lambda item: item.score, reverse=True)[:limit]

    def _read(self) -> list[dict[str, str]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*|\d+", text.lower()))

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)

