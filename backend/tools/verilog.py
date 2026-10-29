"""Verilog generation and editing tools."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from backend.models import DesignRuleModel, FailureModel, FixModel


class VerilogEditInput(BaseModel):
    specification: str
    iteration: int
    seed_demo_bug: bool = True
    bug_seeded: bool = False
    past_failures: list[FailureModel] = Field(default_factory=list)
    design_rules: list[DesignRuleModel] = Field(default_factory=list)
    relevant_patterns: list[dict[str, Any]] = Field(default_factory=list)


class VerilogEditOutput(BaseModel):
    module_name: str
    verilog: str
    summary: str
    fix: FixModel | None = None
    bug_seeded: bool = False


def generate_or_edit_verilog(payload: VerilogEditInput) -> VerilogEditOutput:
    """Generate or repair synthesizable RTL.

    The demo intentionally seeds one reset-polarity bug on the first iteration
    when requested. Subsequent iterations use parsed failure summaries and graph
    rules to produce corrected RTL.
    """

    width = _extract_width(payload.specification) or 4
    module_name = f"sync_counter_{width}bit" if _looks_like_counter(payload.specification) else "chipflow_design"
    reset_signal = "rst_n" if "active-low" in payload.specification.lower() or "active low" in payload.specification.lower() else "rst"
    enable_signal = "en"
    active_low_reset = reset_signal.endswith("_n")

    failure_text = " ".join(f"{item.summary} {item.details}" for item in payload.past_failures).lower()
    needs_reset_fix = "reset" in failure_text or "rst" in failure_text or "polarity" in failure_text
    should_seed_bug = payload.seed_demo_bug and not payload.bug_seeded and payload.iteration == 0

    if _looks_like_counter(payload.specification):
        if should_seed_bug:
            verilog = _counter_verilog(
                width=width,
                module_name=module_name,
                reset_signal=reset_signal,
                enable_signal=enable_signal,
                active_low_reset=active_low_reset,
                invert_reset_condition=True,
            )
            return VerilogEditOutput(
                module_name=module_name,
                verilog=verilog,
                summary=(
                    "Generated first RTL candidate. Demo recovery is enabled, "
                    "so this version intentionally contains a reset-polarity bug."
                ),
                bug_seeded=True,
            )

        verilog = _counter_verilog(
            width=width,
            module_name=module_name,
            reset_signal=reset_signal,
            enable_signal=enable_signal,
            active_low_reset=active_low_reset,
            invert_reset_condition=False,
        )
        fix = None
        summary = "Generated synthesizable counter RTL using graph design rules."
        if needs_reset_fix:
            latest = payload.past_failures[-1] if payload.past_failures else None
            fix = FixModel(
                id=f"fix:reset:{payload.iteration}",
                summary="Corrected active-low reset polarity in sequential block.",
                details=(
                    "Changed reset branch to test `!rst_n` while preserving "
                    "`posedge clk or negedge rst_n` sensitivity."
                ),
                iteration=payload.iteration,
                fixes_failure_id=latest.id if latest else None,
            )
            summary = "Edited RTL to fix reset polarity and preserve enable hold behavior."
        return VerilogEditOutput(
            module_name=module_name,
            verilog=verilog,
            summary=summary,
            fix=fix,
            bug_seeded=payload.bug_seeded,
        )

    return VerilogEditOutput(
        module_name=module_name,
        verilog=_generic_register_verilog(module_name),
        summary=(
            "Generated a conservative register shell because the request did "
            "not match a supported simple design template."
        ),
        bug_seeded=payload.bug_seeded,
    )


def _counter_verilog(
    *,
    width: int,
    module_name: str,
    reset_signal: str,
    enable_signal: str,
    active_low_reset: bool,
    invert_reset_condition: bool,
) -> str:
    reset_edge = f"negedge {reset_signal}" if active_low_reset else f"posedge {reset_signal}"
    correct_condition = f"!{reset_signal}" if active_low_reset else reset_signal
    reset_condition = reset_signal if invert_reset_condition and active_low_reset else correct_condition
    max_value = (1 << width) - 1
    return f"""`timescale 1ns/1ps

module {module_name} (
    input wire clk,
    input wire {reset_signal},
    input wire {enable_signal},
    output reg [{width - 1}:0] count,
    output wire overflow
);

assign overflow = {enable_signal} && (count == {width}'d{max_value});

always @(posedge clk or {reset_edge}) begin
    if ({reset_condition}) begin
        count <= {width}'d0;
    end else if ({enable_signal}) begin
        count <= count + {width}'d1;
    end
end

endmodule
"""


def _generic_register_verilog(module_name: str) -> str:
    return f"""`timescale 1ns/1ps

module {module_name} (
    input wire clk,
    input wire rst_n,
    input wire en,
    input wire d,
    output reg q
);

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        q <= 1'b0;
    end else if (en) begin
        q <= d;
    end
end

endmodule
"""


def _extract_width(specification: str) -> int | None:
    match = re.search(r"(\d+)\s*[- ]?\s*bit", specification, re.I)
    if not match:
        return None
    width = int(match.group(1))
    return max(1, min(width, 32))


def _looks_like_counter(specification: str) -> bool:
    return "counter" in specification.lower()

