"""Yosys synthesis tool adapter with stats parsing and fallback estimates."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel

from backend.models import SynthesisResult


class SynthesisInput(BaseModel):
    run_dir: Path
    module_name: str
    verilog: str
    cell_budget: int | None = 40


def run_yosys_synthesis(payload: SynthesisInput) -> SynthesisResult:
    payload.run_dir.mkdir(parents=True, exist_ok=True)
    design_path = payload.run_dir / f"{payload.module_name}.v"
    script_path = payload.run_dir / "synth.ys"
    design_path.write_text(payload.verilog, encoding="utf-8")
    script = f"""read_verilog {design_path}
hierarchy -check -top {payload.module_name}
proc
opt
fsm
opt
memory
opt
techmap
opt
stat
"""
    script_path.write_text(script, encoding="utf-8")

    yosys = shutil.which("yosys")
    if yosys:
        # No -q: quiet mode suppresses the `stat` report that _parse_cells reads.
        command = [yosys, "-s", str(script_path)]
        proc = subprocess.run(command, capture_output=True, text=True, timeout=60)
        cell_count, cells = _parse_cells(proc.stdout + "\n" + proc.stderr)
        passed = proc.returncode == 0 and (
            payload.cell_budget is None or cell_count is None or cell_count <= payload.cell_budget
        )
        return SynthesisResult(
            passed=passed,
            tool="yosys",
            command=command,
            stdout=proc.stdout,
            stderr=proc.stderr,
            return_code=proc.returncode,
            cell_count=cell_count,
            estimated_area=float(cell_count) if cell_count is not None else None,
            cells=cells,
            warning_count=(proc.stdout + proc.stderr).lower().count("warning"),
            summary=_synthesis_summary(cell_count, payload.cell_budget, proc.returncode == 0),
        )

    cell_count, cells = _estimate_cells(payload.verilog)
    passed = payload.cell_budget is None or cell_count <= payload.cell_budget
    return SynthesisResult(
        passed=passed,
        tool="yosys-fallback-estimator",
        cell_count=cell_count,
        estimated_area=float(cell_count),
        cells=cells,
        stdout="NOTE: yosys not found; used a conservative structural estimator.",
        summary=_synthesis_summary(cell_count, payload.cell_budget, True),
    )


def _parse_cells(text: str) -> tuple[int | None, dict[str, int]]:
    total_match = re.search(r"Number of cells:\s+(\d+)", text)
    cell_count = int(total_match.group(1)) if total_match else None
    cells: dict[str, int] = {}
    cell_table = False
    for line in text.splitlines():
        if "Number of cells:" in line:
            cell_table = True
            continue
        if cell_table:
            match = re.match(r"\s+(\S+)\s+(\d+)\s*$", line)
            if match:
                cells[match.group(1)] = int(match.group(2))
            elif line.strip() == "":
                cell_table = False
    return cell_count, cells


def _estimate_cells(verilog: str) -> tuple[int, dict[str, int]]:
    width_match = re.search(r"output\s+reg\s+\[(\d+):0\]\s+count", verilog)
    width = int(width_match.group(1)) + 1 if width_match else 4
    cells = {
        "$adff": width,
        "$add": width,
        "$mux": width,
        "$eq": 1,
        "$and": 1,
    }
    return sum(cells.values()), cells


def _synthesis_summary(cell_count: int | None, budget: int | None, tool_ok: bool) -> str:
    if not tool_ok:
        return "Synthesis failed before statistics were produced."
    if cell_count is None:
        return "Synthesis completed but no cell count was parsed."
    if budget is None:
        return f"Synthesis completed with {cell_count} cells."
    status = "within" if cell_count <= budget else "over"
    return f"Synthesis completed with {cell_count} cells, {status} the {budget}-cell budget."

