"""Run the required counter example from the command line."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from backend.agents.graph import ChipFlowWorkflow
from backend.models import state_to_snapshot


SPEC = (
    "Design a 4-bit synchronous up-counter with active-high enable, "
    "asynchronous active-low reset, and an overflow flag. Keep the design "
    "under 40 standard cells if possible."
)


async def main() -> None:
    base = Path(__file__).resolve().parents[1]
    workflow = ChipFlowWorkflow(data_dir=base / "data", work_dir=base / "workdir")
    state = workflow.initial_state(
        run_id=uuid4().hex,
        specification=SPEC,
        max_iterations=12,
        seed_demo_bug=True,
    )
    async for state in workflow.run_stream(state):
        snapshot = state_to_snapshot(state)
        last = snapshot.events[-1]
        print(f"[{last.step:02d}] {last.role}: {last.title} - {last.summary}")
    snapshot = state_to_snapshot(state)
    print()
    print(f"status={snapshot.status}")
    print(f"module={snapshot.artifacts.module_name}")
    print(f"simulation={snapshot.simulation.passed if snapshot.simulation else None}")
    print(f"cells={snapshot.synthesis.cell_count if snapshot.synthesis else None}")


if __name__ == "__main__":
    asyncio.run(main())

