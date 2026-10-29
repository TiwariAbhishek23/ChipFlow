"""Local JSON and ZIP persistence for demo runs."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from backend.models import RunSnapshot


class RunStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, snapshot: RunSnapshot) -> Path:
        path = self.root / f"{snapshot.run_id}.json"
        path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load(self, run_id: str) -> RunSnapshot:
        path = self.root / f"{run_id}.json"
        if not path.exists():
            raise FileNotFoundError(run_id)
        return RunSnapshot.model_validate_json(path.read_text(encoding="utf-8"))

    def export_zip(self, snapshot: RunSnapshot, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "run_id": snapshot.run_id,
            "status": snapshot.status,
            "specification": snapshot.specification,
            "simulation": snapshot.simulation.model_dump(mode="json") if snapshot.simulation else None,
            "synthesis": snapshot.synthesis.model_dump(mode="json") if snapshot.synthesis else None,
            "history": [item.model_dump(mode="json") for item in snapshot.history],
        }
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("design.v", snapshot.artifacts.verilog)
            zf.writestr("testbench.v", snapshot.artifacts.testbench)
            zf.writestr("report.json", json.dumps(report, indent=2, default=str))
            zf.writestr(
                "knowledge_graph.json",
                snapshot.knowledge_graph.model_dump_json(indent=2),
            )
            zf.writestr("events.json", json.dumps([event.model_dump(mode="json") for event in snapshot.events], indent=2))
        return destination

