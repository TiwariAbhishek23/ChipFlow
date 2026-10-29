"""Shared typed models for ChipFlow Agent.

The LangGraph workflow uses ``ChipFlowState`` as the explicit state schema.
The API, tools, knowledge graph, and persistence layer use the Pydantic
models below so each boundary has a stable JSON contract.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


class AgentRole(StrEnum):
    ORCHESTRATOR = "orchestrator"
    RTL_DESIGNER = "rtl_designer"
    VERIFICATION = "verification"
    SYNTHESIS = "synthesis"
    KNOWLEDGE_GRAPH = "knowledge_graph"
    CRITIC = "critic"
    SYSTEM = "system"


class EventKind(StrEnum):
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CODE = "code"
    TESTBENCH = "testbench"
    GRAPH = "graph"
    REPORT = "report"
    STATUS = "status"
    ERROR = "error"


class ToolStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"


class PortDirection(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    INOUT = "inout"


class PortModel(BaseModel):
    name: str
    direction: PortDirection
    width: int = 1
    kind: Literal["wire", "reg", "logic"] | None = None


class ConstraintModel(BaseModel):
    name: str
    description: str
    target_value: str | int | float | bool | None = None


class DesignRuleModel(BaseModel):
    id: str
    title: str
    description: str
    severity: Literal["info", "warning", "error"] = "warning"
    tags: list[str] = Field(default_factory=list)


class FailureModel(BaseModel):
    id: str
    tool: str
    summary: str
    details: str = ""
    category: str = "unknown"
    iteration: int = 0
    related_rules: list[str] = Field(default_factory=list)


class FixModel(BaseModel):
    id: str
    summary: str
    details: str = ""
    iteration: int = 0
    fixes_failure_id: str | None = None


class ToolOutputModel(BaseModel):
    id: str
    tool: str
    status: ToolStatus
    command: list[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    return_code: int | None = None
    summary: str = ""


class SimulationResult(BaseModel):
    passed: bool
    tool: str
    command: list[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    return_code: int | None = None
    failure_reason: str | None = None
    assertions: list[str] = Field(default_factory=list)


class SynthesisResult(BaseModel):
    passed: bool
    tool: str
    command: list[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    return_code: int | None = None
    cell_count: int | None = None
    estimated_area: float | None = None
    cells: dict[str, int] = Field(default_factory=dict)
    warning_count: int = 0
    summary: str = ""


class DesignArtifacts(BaseModel):
    module_name: str = "sync_counter_4bit"
    verilog: str = ""
    testbench: str = ""
    synthesis_script: str = ""


class KGNode(BaseModel):
    id: str
    type: str
    label: str
    data: dict[str, Any] = Field(default_factory=dict)


class KGEdge(BaseModel):
    source: str
    target: str
    type: str
    label: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class KGSnapshot(BaseModel):
    nodes: list[KGNode] = Field(default_factory=list)
    edges: list[KGEdge] = Field(default_factory=list)


class AIRuntimeStatus(BaseModel):
    provider: str = "deterministic"
    model: str | None = None
    sdk: str = "none"
    enabled: bool = False
    reason: str = "Deterministic templates are active."


class AgentEvent(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    run_id: str
    step: int
    iteration: int
    role: AgentRole
    kind: EventKind
    title: str
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class StepRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    step: int
    iteration: int
    role: AgentRole
    action: str
    status: ToolStatus = ToolStatus.OK
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class RunRequest(BaseModel):
    specification: str
    max_iterations: int = Field(default=12, ge=3, le=20)
    seed_demo_bug: bool = True


class RunSnapshot(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    run_id: str
    status: RunStatus = RunStatus.CREATED
    specification: str
    iteration: int = 0
    step: int = 0
    artifacts: DesignArtifacts = Field(default_factory=DesignArtifacts)
    simulation: SimulationResult | None = None
    synthesis: SynthesisResult | None = None
    knowledge_graph: KGSnapshot = Field(default_factory=KGSnapshot)
    history: list[StepRecord] = Field(default_factory=list)
    events: list[AgentEvent] = Field(default_factory=list)
    failures: list[FailureModel] = Field(default_factory=list)
    fixes: list[FixModel] = Field(default_factory=list)
    design_rules: list[DesignRuleModel] = Field(default_factory=list)
    ai_runtime: AIRuntimeStatus = Field(default_factory=AIRuntimeStatus)


class ChipFlowState(TypedDict, total=False):
    """LangGraph state schema.

    State is intentionally JSON-serializable because every node snapshot can be
    persisted or streamed to the UI. Fields are cumulative, and nodes update
    only the keys they own.
    """

    run_id: str
    specification: str
    max_iterations: int
    seed_demo_bug: bool
    bug_seeded: bool
    rtl_attempts: int
    iteration: int
    step: int
    status: str
    next_action: str
    artifacts: dict[str, Any]
    simulation: NotRequired[dict[str, Any] | None]
    synthesis: NotRequired[dict[str, Any] | None]
    knowledge_graph: dict[str, Any]
    history: list[dict[str, Any]]
    events: list[dict[str, Any]]
    failures: list[dict[str, Any]]
    fixes: list[dict[str, Any]]
    design_rules: list[dict[str, Any]]
    ai_runtime: dict[str, Any]
    pending_failure: NotRequired[dict[str, Any] | None]
    pending_fix: NotRequired[dict[str, Any] | None]
    semantic_patterns: list[dict[str, Any]]


def state_to_snapshot(state: ChipFlowState) -> RunSnapshot:
    """Convert a workflow state dictionary into an API snapshot."""

    return RunSnapshot(
        run_id=state["run_id"],
        status=RunStatus(state.get("status", RunStatus.RUNNING)),
        specification=state["specification"],
        iteration=state.get("iteration", 0),
        step=state.get("step", 0),
        artifacts=DesignArtifacts.model_validate(state.get("artifacts", {})),
        simulation=(
            SimulationResult.model_validate(state["simulation"])
            if state.get("simulation")
            else None
        ),
        synthesis=(
            SynthesisResult.model_validate(state["synthesis"])
            if state.get("synthesis")
            else None
        ),
        knowledge_graph=KGSnapshot.model_validate(state.get("knowledge_graph", {})),
        history=[StepRecord.model_validate(item) for item in state.get("history", [])],
        events=[AgentEvent.model_validate(item) for item in state.get("events", [])],
        failures=[FailureModel.model_validate(item) for item in state.get("failures", [])],
        fixes=[FixModel.model_validate(item) for item in state.get("fixes", [])],
        design_rules=[
            DesignRuleModel.model_validate(item)
            for item in state.get("design_rules", [])
        ],
        ai_runtime=AIRuntimeStatus.model_validate(state.get("ai_runtime", {})),
    )


def append_event(
    state: ChipFlowState,
    *,
    role: AgentRole,
    kind: EventKind,
    title: str,
    summary: str = "",
    payload: dict[str, Any] | None = None,
) -> ChipFlowState:
    """Return a partial state update with a new event and history step."""

    step = state.get("step", 0) + 1
    event = AgentEvent(
        run_id=state["run_id"],
        step=step,
        iteration=state.get("iteration", 0),
        role=role,
        kind=kind,
        title=title,
        summary=summary,
        payload=payload or {},
    )
    history_item = StepRecord(
        step=step,
        iteration=state.get("iteration", 0),
        role=role,
        action=title,
        status=ToolStatus.OK,
        summary=summary,
        payload=payload or {},
    )
    return {
        "step": step,
        "events": [*state.get("events", []), event.model_dump(mode="json")],
        "history": [
            *state.get("history", []),
            history_item.model_dump(mode="json"),
        ],
    }
