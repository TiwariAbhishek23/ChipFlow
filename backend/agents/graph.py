"""LangGraph definition for ChipFlow Agent.

The workflow is hierarchical: an orchestrator chooses the next specialist node,
specialists call typed tools, the knowledge graph manager records state after
each step, and an optional critic performs final reflection before the run ends.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from backend.agents.openai_agents_sdk import OpenAIAgentsSDKClient
from backend.knowledge_graph.manager import KnowledgeGraphManager
from backend.knowledge_graph.semantic_memory import SemanticMemory
from backend.models import (
    AgentRole,
    ChipFlowState,
    EventKind,
    FailureModel,
    FixModel,
    AIRuntimeStatus,
    RunStatus,
    SynthesisResult,
    ToolOutputModel,
    ToolStatus,
    append_event,
)
from backend.tools.knowledge import get_past_failures_and_fixes, get_relevant_design_rules
from backend.tools.simulation import SimulationInput, run_simulation
from backend.tools.synthesis import SynthesisInput, run_yosys_synthesis
from backend.tools.testbench import TestbenchInput, write_testbench
from backend.tools.verilog import VerilogEditInput, generate_or_edit_verilog

try:  # pragma: no cover - exercised when langgraph is installed
    from langgraph.graph import END, START, StateGraph
except Exception:  # pragma: no cover - local fallback path
    END = "__end__"
    START = "__start__"
    StateGraph = None  # type: ignore[assignment]


class ChipFlowWorkflow:
    """Compiled multi-agent graph plus a dependency bundle for nodes."""

    def __init__(self, *, data_dir: Path, work_dir: Path) -> None:
        self.data_dir = data_dir
        self.work_dir = work_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.kg = KnowledgeGraphManager(self.data_dir / "knowledge_graph.json")
        self.memory = SemanticMemory(self.data_dir / "semantic_memory.json")
        self.openai_agents = OpenAIAgentsSDKClient()
        self.app = self._compile_graph()

    def initial_state(
        self,
        *,
        run_id: str,
        specification: str,
        max_iterations: int,
        seed_demo_bug: bool,
    ) -> ChipFlowState:
        rules = [rule.model_dump(mode="json") for rule in self.kg.get_design_rules()]
        return {
            "run_id": run_id,
            "specification": specification,
            "max_iterations": max_iterations,
            "seed_demo_bug": seed_demo_bug,
            "bug_seeded": False,
            "rtl_attempts": 0,
            "iteration": 0,
            "step": 0,
            "status": RunStatus.RUNNING.value,
            "next_action": "rtl",
            "artifacts": {"module_name": "sync_counter_4bit", "verilog": "", "testbench": "", "synthesis_script": ""},
            "simulation": None,
            "synthesis": None,
            "knowledge_graph": self.kg.snapshot().model_dump(mode="json"),
            "history": [],
            "events": [],
            "failures": [],
            "fixes": [],
            "design_rules": rules,
            "ai_runtime": self.model_status().model_dump(mode="json"),
            "pending_failure": None,
            "pending_fix": None,
            "semantic_patterns": [],
        }

    def model_status(self) -> AIRuntimeStatus:
        return self.openai_agents.status()

    async def run_stream(self, initial_state: ChipFlowState) -> AsyncIterator[ChipFlowState]:
        """Yield full state snapshots after each graph transition."""

        # Worst case per RTL attempt is ~9 node visits (planner/rtl/kg, planner/verify/kg,
        # planner/synthesis/kg), so size the step limit from the iteration budget.
        step_limit = 10 * initial_state.get("max_iterations", 12) + 10

        if self.app is not None:
            previous_event_count = 0
            async for state in self.app.astream(
                initial_state, stream_mode="values", config={"recursion_limit": step_limit}
            ):
                if not isinstance(state, dict):
                    continue
                event_count = len(state.get("events", []))
                if event_count != previous_event_count:
                    previous_event_count = event_count
                    yield state
            return

        state = initial_state
        for _ in range(step_limit):
            state = self._planner_node(state)
            yield state
            action = state.get("next_action")
            if action == "finish":
                break
            node = self._fallback_node(action)
            state = node(state)
            yield state
            if action != "kg":
                state = self._knowledge_graph_node(state)
                yield state
            await asyncio.sleep(0)

    def _compile_graph(self) -> Any | None:
        if StateGraph is None:
            return None
        graph = StateGraph(ChipFlowState)
        graph.add_node("planner", self._planner_node)
        graph.add_node("rtl_designer", self._rtl_designer_node)
        graph.add_node("verification", self._verification_node)
        graph.add_node("synthesis", self._synthesis_node)
        graph.add_node("knowledge_graph", self._knowledge_graph_node)
        graph.add_node("critic", self._critic_node)
        graph.add_edge(START, "planner")
        graph.add_conditional_edges(
            "planner",
            self._route_from_planner,
            {
                "rtl": "rtl_designer",
                "verify": "verification",
                "synthesize": "synthesis",
                "reflect": "critic",
                "finish": END,
            },
        )
        graph.add_edge("rtl_designer", "knowledge_graph")
        graph.add_edge("verification", "knowledge_graph")
        graph.add_edge("synthesis", "knowledge_graph")
        graph.add_edge("critic", "knowledge_graph")
        graph.add_conditional_edges(
            "knowledge_graph",
            self._route_after_kg,
            {"planner": "planner", "finish": END},
        )
        return graph.compile()

    def _planner_node(self, state: ChipFlowState) -> ChipFlowState:
        iteration = state.get("iteration", 0)
        max_iterations = state.get("max_iterations", 12)
        artifacts = state.get("artifacts", {})
        simulation = state.get("simulation")
        synthesis = state.get("synthesis")
        failures = state.get("failures", [])
        cell_budget = _extract_cell_budget(state["specification"])

        # RTL attempts count against the budget too: a repair that never yields a
        # fix (or a synthesis gate that never passes) must not loop forever.
        if iteration >= max_iterations or state.get("rtl_attempts", 0) >= max_iterations:
            update = append_event(
                state,
                role=AgentRole.ORCHESTRATOR,
                kind=EventKind.ERROR,
                title="Stopped at iteration budget",
                summary=f"Reached {max_iterations} iterations before all quality gates passed.",
            )
            return {**update, "status": RunStatus.FAILED.value, "next_action": "finish"}

        if not artifacts.get("verilog"):
            action = "rtl"
            summary = "No RTL exists yet; delegate to the RTL designer."
        elif failures and _latest_failure_is_unfixed(state):
            action = "rtl"
            summary = "Latest verification failure is unresolved; request an RTL edit informed by the graph."
        elif not artifacts.get("testbench"):
            action = "verify"
            summary = "RTL exists; create a self-checking testbench and run simulation."
        elif not simulation or not simulation.get("passed"):
            action = "verify"
            summary = "Simulation has not passed; run verification before synthesis."
        elif not synthesis:
            action = "synthesize"
            summary = "Simulation passed; run logic synthesis and quality checks."
        elif not synthesis.get("passed"):
            action = "rtl"
            summary = "Synthesis did not meet gates; ask the RTL designer for a repair."
        elif cell_budget is not None and synthesis.get("cell_count") and synthesis["cell_count"] > cell_budget:
            action = "rtl"
            summary = f"Cell count exceeds the {cell_budget}-cell budget; request optimization."
        elif not state.get("next_action") == "reflect_done":
            action = "reflect"
            summary = "All core gates passed; run critic reflection before finishing."
        else:
            action = "finish"
            summary = "Quality gates passed: simulation clean, synthesis clean, and graph updated."

        update = append_event(
            state,
            role=AgentRole.ORCHESTRATOR,
            kind=EventKind.THOUGHT,
            title="Planner decision",
            summary=summary,
            payload={
                "next_action": action,
                "ai_runtime": self.model_status().model_dump(mode="json"),
            },
        )
        return {
            **update,
            "next_action": action,
            "ai_runtime": self.model_status().model_dump(mode="json"),
        }

    def _rtl_designer_node(self, state: ChipFlowState) -> ChipFlowState:
        rules = get_relevant_design_rules(self.kg, tags=["rtl", "reset", "enable", "synthesis"])
        past = get_past_failures_and_fixes(self.kg, "reset")
        patterns = [match.__dict__ for match in self.memory.query(state["specification"])]
        payload = VerilogEditInput(
            specification=state["specification"],
            iteration=state.get("iteration", 0),
            seed_demo_bug=state.get("seed_demo_bug", True),
            bug_seeded=state.get("bug_seeded", False),
            past_failures=[FailureModel.model_validate(item) for item in state.get("failures", [])],
            design_rules=rules,
            relevant_patterns=patterns,
        )
        result = generate_or_edit_verilog(payload)
        sdk_review = self.openai_agents.review_rtl(
            specification=state["specification"],
            verilog=result.verilog,
            design_rules=rules,
            failures=state.get("failures", []),
        )
        artifacts = {
            **state.get("artifacts", {}),
            "module_name": result.module_name,
            "verilog": result.verilog,
        }
        fixes = state.get("fixes", [])
        pending_fix = None
        if result.fix:
            pending_fix = result.fix.model_dump(mode="json")
            fixes = [*fixes, pending_fix]

        update = append_event(
            state,
            role=AgentRole.RTL_DESIGNER,
            kind=EventKind.CODE,
            title="RTL generated",
            summary=(
                f"{result.summary} OpenAI Agents SDK review: {sdk_review.summary}"
                if sdk_review.used
                else result.summary
            ),
            payload={
                "module_name": result.module_name,
                "verilog": result.verilog,
                "rules_used": [rule.id for rule in rules],
                "past_failure_matches": past[:3],
                "openai_agents_review": {
                    "used": sdk_review.used,
                    "model": sdk_review.model,
                    "summary": sdk_review.summary,
                    "raw_output": sdk_review.raw_output,
                },
            },
        )
        return {
            **update,
            "artifacts": artifacts,
            "bug_seeded": result.bug_seeded,
            "rtl_attempts": state.get("rtl_attempts", 0) + 1,
            "fixes": fixes,
            "pending_fix": pending_fix,
            "simulation": None,
            "synthesis": None,
        }

    def _verification_node(self, state: ChipFlowState) -> ChipFlowState:
        artifacts = state.get("artifacts", {})
        width = _extract_width(state["specification"]) or 4
        tb_result = write_testbench(
            TestbenchInput(
                specification=state["specification"],
                module_name=artifacts.get("module_name", "sync_counter_4bit"),
                width=width,
            )
        )
        artifacts = {**artifacts, "testbench": tb_result.testbench}
        simulation, failure = run_simulation(
            SimulationInput(
                run_dir=self.work_dir / state["run_id"],
                module_name=artifacts["module_name"],
                verilog=artifacts["verilog"],
                testbench=tb_result.testbench,
                iteration=state.get("iteration", 0),
            )
        )
        failures = state.get("failures", [])
        pending_failure = None
        if failure:
            pending_failure = failure.model_dump(mode="json")
            if not any(item.get("id") == failure.id for item in failures):
                failures = [*failures, pending_failure]

        status = ToolStatus.OK if simulation.passed else ToolStatus.FAILED
        update = append_event(
            state,
            role=AgentRole.VERIFICATION,
            kind=EventKind.TOOL_RESULT,
            title="Simulation complete",
            summary=simulation.failure_reason or tb_result.summary,
            payload={
                "status": status.value,
                "testbench": tb_result.testbench,
                "simulation": simulation.model_dump(mode="json"),
            },
        )
        next_iteration = state.get("iteration", 0) + (0 if simulation.passed else 1)
        return {
            **update,
            "iteration": next_iteration,
            "artifacts": artifacts,
            "simulation": simulation.model_dump(mode="json"),
            "failures": failures,
            "pending_failure": pending_failure,
        }

    def _synthesis_node(self, state: ChipFlowState) -> ChipFlowState:
        artifacts = state.get("artifacts", {})
        budget = _extract_cell_budget(state["specification"])
        result = run_yosys_synthesis(
            SynthesisInput(
                run_dir=self.work_dir / state["run_id"],
                module_name=artifacts.get("module_name", "sync_counter_4bit"),
                verilog=artifacts.get("verilog", ""),
                cell_budget=budget,
            )
        )
        update = append_event(
            state,
            role=AgentRole.SYNTHESIS,
            kind=EventKind.REPORT,
            title="Synthesis complete",
            summary=result.summary,
            payload={"synthesis": result.model_dump(mode="json")},
        )
        return {**update, "synthesis": result.model_dump(mode="json")}

    def _knowledge_graph_node(self, state: ChipFlowState) -> ChipFlowState:
        artifacts = state.get("artifacts", {})
        module_name = None
        if artifacts.get("verilog"):
            module_name = self.kg.add_module_from_verilog(artifacts["verilog"], state.get("iteration", 0))

        if state.get("pending_failure"):
            self.kg.record_failure(FailureModel.model_validate(state["pending_failure"]))
        if state.get("pending_fix"):
            self.kg.record_fix(FixModel.model_validate(state["pending_fix"]))
        if state.get("simulation"):
            sim = state["simulation"]
            output = ToolOutputModel(
                id=f"tool:simulation:{sim.get('tool')}:{state.get('step', 0)}",
                tool=sim.get("tool", "simulation"),
                status=ToolStatus.OK if sim.get("passed") else ToolStatus.FAILED,
                command=sim.get("command", []),
                stdout=sim.get("stdout", "")[-4000:],
                stderr=sim.get("stderr", "")[-4000:],
                return_code=sim.get("return_code"),
                summary=sim.get("failure_reason") or "Simulation passed.",
            )
            self.kg.record_tool_output(output, f"module:{artifacts.get('module_name', 'unknown')}")
        if state.get("synthesis") and module_name:
            self.kg.record_synthesis(SynthesisResult.model_validate(state["synthesis"]), module_name)

        simulation_state = state.get("simulation") or {}
        if simulation_state.get("passed") and artifacts.get("verilog"):
            self.memory.add_successful_pattern(
                description=f"Successful RTL for: {state['specification']}",
                code=artifacts["verilog"],
            )

        snapshot = self.kg.snapshot()
        update = append_event(
            state,
            role=AgentRole.KNOWLEDGE_GRAPH,
            kind=EventKind.GRAPH,
            title="Knowledge graph updated",
            summary=f"Graph now has {len(snapshot.nodes)} nodes and {len(snapshot.edges)} edges.",
            payload={"knowledge_graph": snapshot.model_dump(mode="json")},
        )
        return {
            **update,
            "knowledge_graph": snapshot.model_dump(mode="json"),
            "design_rules": [rule.model_dump(mode="json") for rule in self.kg.get_design_rules()],
            "pending_failure": None,
            "pending_fix": None,
        }

    def _critic_node(self, state: ChipFlowState) -> ChipFlowState:
        synthesis = state.get("synthesis") or {}
        simulation = state.get("simulation") or {}
        summary = (
            "Critic accepted the trajectory: the reset failure was captured, an RTL fix was applied, "
            f"simulation passed with {simulation.get('tool')}, and synthesis reports "
            f"{synthesis.get('cell_count', 'unknown')} cells."
        )
        update = append_event(
            state,
            role=AgentRole.CRITIC,
            kind=EventKind.THOUGHT,
            title="Trajectory reflection",
            summary=summary,
            payload={"quality_gates": {"simulation": simulation.get("passed"), "synthesis": synthesis.get("passed")}},
        )
        return {**update, "next_action": "reflect_done", "status": RunStatus.PASSED.value}

    @staticmethod
    def _route_from_planner(state: ChipFlowState) -> str:
        return state.get("next_action", "finish")

    @staticmethod
    def _route_after_kg(state: ChipFlowState) -> str:
        if state.get("status") == RunStatus.PASSED.value and state.get("next_action") == "reflect_done":
            return "finish"
        return "planner"

    def _fallback_node(self, action: str) -> Callable[[ChipFlowState], ChipFlowState]:
        return {
            "rtl": self._rtl_designer_node,
            "verify": self._verification_node,
            "synthesize": self._synthesis_node,
            "reflect": self._critic_node,
        }.get(action, self._critic_node)


def _extract_width(specification: str) -> int | None:
    match = re.search(r"(\d+)\s*[- ]?\s*bit", specification, re.I)
    return int(match.group(1)) if match else None


def _extract_cell_budget(specification: str) -> int | None:
    match = re.search(r"(?:under|below|less than|<=?)\s*(\d+)\s*(?:standard\s*)?cells?", specification, re.I)
    return int(match.group(1)) if match else None


def _latest_failure_is_unfixed(state: ChipFlowState) -> bool:
    failures = state.get("failures", [])
    if not failures:
        return False
    fixes = state.get("fixes", [])
    latest = failures[-1]
    latest_id = latest.get("id")
    return not any(fix.get("fixes_failure_id") == latest_id for fix in fixes)
