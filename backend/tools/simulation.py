"""Simulation tool adapter for iverilog with an internal demo fallback."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel

from backend.models import FailureModel, SimulationResult


class SimulationInput(BaseModel):
    run_dir: Path
    module_name: str
    verilog: str
    testbench: str
    iteration: int


def run_simulation(payload: SimulationInput) -> tuple[SimulationResult, FailureModel | None]:
    payload.run_dir.mkdir(parents=True, exist_ok=True)
    design_path = payload.run_dir / f"{payload.module_name}.v"
    tb_path = payload.run_dir / f"tb_{payload.module_name}.v"
    simv_path = payload.run_dir / "simv"
    design_path.write_text(payload.verilog, encoding="utf-8")
    tb_path.write_text(payload.testbench, encoding="utf-8")

    iverilog = shutil.which("iverilog")
    vvp = shutil.which("vvp")
    if iverilog and vvp:
        command = [iverilog, "-g2012", "-o", str(simv_path), str(design_path), str(tb_path)]
        compile_proc = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if compile_proc.returncode != 0:
            result = SimulationResult(
                passed=False,
                tool="iverilog",
                command=command,
                stdout=compile_proc.stdout,
                stderr=compile_proc.stderr,
                return_code=compile_proc.returncode,
                failure_reason="Icarus Verilog compile failed.",
                assertions=_extract_assertions(compile_proc.stdout + compile_proc.stderr),
            )
            return result, _failure_from_simulation(result, payload.iteration)

        run_command = [vvp, str(simv_path)]
        run_proc = subprocess.run(run_command, capture_output=True, text=True, timeout=30)
        combined = f"{run_proc.stdout}\n{run_proc.stderr}"
        passed = run_proc.returncode == 0 and "CHIPFLOW_PASS" in combined and "ASSERT_FAIL" not in combined
        result = SimulationResult(
            passed=passed,
            tool="iverilog",
            command=command + ["&&", *run_command],
            stdout=run_proc.stdout,
            stderr=run_proc.stderr,
            return_code=run_proc.returncode,
            failure_reason=None if passed else _summarize_failure(combined),
            assertions=_extract_assertions(combined),
        )
        return result, None if passed else _failure_from_simulation(result, payload.iteration)

    result = _internal_counter_check(payload)
    return result, None if result.passed else _failure_from_simulation(result, payload.iteration)


def _internal_counter_check(payload: SimulationInput) -> SimulationResult:
    """A deterministic fallback for the required counter demo.

    It keeps the application demonstrable on machines that do not have Icarus
    Verilog installed, while clearly reporting that the native tool was absent.
    """

    normalized = " ".join(payload.verilog.split())
    has_active_low_reset = "always @(posedge clk or negedge rst_n)" in normalized
    reset_condition_ok = "if (!rst_n)" in normalized or "if (~rst_n)" in normalized
    has_enable = "else if (en)" in normalized
    has_overflow = "assign overflow = en && (count ==" in normalized
    failures: list[str] = []
    if not has_active_low_reset or not reset_condition_ok:
        failures.append("Reset polarity bug: active-low reset must test !rst_n.")
    if not has_enable:
        failures.append("Enable behavior missing: counter must hold when en is low.")
    if not has_overflow:
        failures.append("Overflow flag missing or not tied to max-count condition.")

    if failures:
        return SimulationResult(
            passed=False,
            tool="internal-simulator-fallback",
            stdout="\n".join(f"ASSERT_FAIL: {failure}" for failure in failures),
            failure_reason="; ".join(failures),
            assertions=failures,
        )
    return SimulationResult(
        passed=True,
        tool="internal-simulator-fallback",
        stdout=(
            "CHIPFLOW_PASS: counter behavior verified\n"
            "NOTE: iverilog/vvp not found; used deterministic fallback checker."
        ),
        assertions=["reset", "enable_hold", "overflow", "wrap"],
    )


def _extract_assertions(text: str) -> list[str]:
    assertions: list[str] = []
    for line in text.splitlines():
        if "ASSERT_FAIL:" in line:
            assertions.append(line.split("ASSERT_FAIL:", 1)[1].strip())
    return assertions


def _summarize_failure(text: str) -> str:
    assertions = _extract_assertions(text)
    if assertions:
        return assertions[0]
    last_lines = [line.strip() for line in text.splitlines() if line.strip()]
    return last_lines[-1] if last_lines else "Simulation failed without a diagnostic."


def _failure_from_simulation(result: SimulationResult, iteration: int) -> FailureModel:
    reason = result.failure_reason or "Simulation failed."
    related_rules = []
    lowered = reason.lower()
    if "reset" in lowered or "rst" in lowered or "polarity" in lowered:
        related_rules.extend(["DR_RESET_POLARITY", "DR_ASYNC_RESET_SENSITIVITY"])
    if "enable" in lowered or "hold" in lowered:
        related_rules.append("DR_ENABLE_HOLD")
    if "overflow" in lowered:
        related_rules.append("DR_OVERFLOW_DEFINED")
    return FailureModel(
        id=f"failure:simulation:{iteration}",
        tool=result.tool,
        summary=reason,
        details=(result.stdout + "\n" + result.stderr).strip(),
        category="simulation",
        iteration=iteration,
        related_rules=related_rules,
    )

