"""Seeded digital design rules used by agents and the knowledge graph."""

from __future__ import annotations

from backend.models import DesignRuleModel


def seed_design_rules() -> list[DesignRuleModel]:
    return [
        DesignRuleModel(
            id="DR_RESET_POLARITY",
            title="Reset polarity must match the specification",
            description=(
                "Active-low resets must test the negated reset signal, while "
                "active-high resets must test the reset signal directly."
            ),
            severity="error",
            tags=["reset", "simulation", "rtl"],
        ),
        DesignRuleModel(
            id="DR_ASYNC_RESET_SENSITIVITY",
            title="Asynchronous reset belongs in the sensitivity list",
            description=(
                "A sequential block with asynchronous active-low reset should "
                "use `always @(posedge clk or negedge rst_n)`."
            ),
            severity="error",
            tags=["reset", "clocking", "rtl"],
        ),
        DesignRuleModel(
            id="DR_ENABLE_HOLD",
            title="Enable-low cycles must preserve state",
            description=(
                "Counters and registers with an enable input must hold the "
                "previous value when enable is deasserted."
            ),
            severity="error",
            tags=["enable", "state", "simulation"],
        ),
        DesignRuleModel(
            id="DR_WIDTH_MATCH",
            title="Arithmetic widths must be explicit",
            description=(
                "Use sized constants and declarations so additions, compares, "
                "and assignments do not silently truncate or extend."
            ),
            severity="warning",
            tags=["width", "lint", "synthesis"],
        ),
        DesignRuleModel(
            id="DR_NONBLOCKING_SEQ",
            title="Sequential state uses nonblocking assignments",
            description=(
                "Registers updated on a clock edge should use nonblocking "
                "assignments to avoid simulation/synthesis mismatches."
            ),
            severity="warning",
            tags=["sequential", "rtl"],
        ),
        DesignRuleModel(
            id="DR_SINGLE_CLOCK",
            title="Simple demo designs use one clock domain",
            description=(
                "Keep supported blocks in a single clock domain unless the "
                "request explicitly asks for clock crossing logic."
            ),
            severity="warning",
            tags=["clocking", "scope"],
        ),
        DesignRuleModel(
            id="DR_COMBINATIONAL_OUTPUTS",
            title="Combinational outputs must be fully defined",
            description=(
                "Flags such as overflow should be driven in all reachable "
                "states with continuous assigns or complete combinational logic."
            ),
            severity="warning",
            tags=["flags", "combinational"],
        ),
        DesignRuleModel(
            id="DR_SYNTHESIZABLE_SUBSET",
            title="RTL must stay in a synthesizable subset",
            description=(
                "Synthesizable modules must not contain delays, initial blocks, "
                "file I/O, assertions, or testbench-only constructs."
            ),
            severity="error",
            tags=["synthesis", "rtl"],
        ),
        DesignRuleModel(
            id="DR_OVERFLOW_DEFINED",
            title="Overflow behavior must be tested",
            description=(
                "Counter overflow or carry outputs must be checked at the wrap "
                "boundary so the flag meaning is unambiguous."
            ),
            severity="warning",
            tags=["overflow", "verification"],
        ),
        DesignRuleModel(
            id="DR_CELL_BUDGET",
            title="Respect simple cell budget constraints",
            description=(
                "If a cell budget is specified, the synthesis agent must report "
                "cell count and the planner must reject over-budget results."
            ),
            severity="warning",
            tags=["synthesis", "quality_gate"],
        ),
    ]

