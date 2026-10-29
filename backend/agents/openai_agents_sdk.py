"""Optional OpenAI Agents SDK integration.

ChipFlow's hardware loop remains deterministic by default. When
``CHIPFLOW_AGENT_PROVIDER=openai_agents`` and ``OPENAI_API_KEY`` are set, this
module adds a lightweight SDK-backed reviewer that can use local function tools
without taking over the EDA quality gates.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from backend.models import AIRuntimeStatus, DesignRuleModel


@dataclass(frozen=True)
class OpenAIAgentReview:
    used: bool
    summary: str
    provider: str = "openai_agents"
    model: str | None = None
    raw_output: str = ""


class OpenAIAgentsSDKClient:
    """Small facade around the OpenAI Agents SDK.

    The import is intentionally lazy. This lets users run the deterministic demo
    even before installing or configuring the SDK.
    """

    def __init__(self) -> None:
        self.provider_env = os.getenv("CHIPFLOW_AGENT_PROVIDER", "deterministic").strip().lower()
        self.model = (
            os.getenv("CHIPFLOW_OPENAI_AGENT_MODEL")
            or os.getenv("OPENAI_DEFAULT_MODEL")
            or None
        )

    def status(self) -> AIRuntimeStatus:
        if self.provider_env != "openai_agents":
            return AIRuntimeStatus(
                provider="deterministic",
                model=None,
                sdk="none",
                enabled=False,
                reason=(
                    "Core workflow uses deterministic RTL/testbench tools. "
                    "Set CHIPFLOW_AGENT_PROVIDER=openai_agents to enable OpenAI Agents SDK review."
                ),
            )
        if not os.getenv("OPENAI_API_KEY"):
            return AIRuntimeStatus(
                provider="openai_agents",
                model=self.model,
                sdk="openai-agents",
                enabled=False,
                reason="OPENAI_API_KEY is not set.",
            )
        try:
            import agents  # noqa: F401
        except Exception as exc:
            return AIRuntimeStatus(
                provider="openai_agents",
                model=self.model,
                sdk="openai-agents",
                enabled=False,
                reason=f"OpenAI Agents SDK import failed: {exc}",
            )
        return AIRuntimeStatus(
            provider="openai_agents",
            model=self.model,
            sdk="openai-agents",
            enabled=True,
            reason="OpenAI Agents SDK review is enabled.",
        )

    def review_rtl(
        self,
        *,
        specification: str,
        verilog: str,
        design_rules: list[DesignRuleModel],
        failures: list[dict[str, Any]],
    ) -> OpenAIAgentReview:
        status = self.status()
        if not status.enabled:
            return OpenAIAgentReview(
                used=False,
                model=status.model,
                summary=status.reason,
            )

        try:
            from agents import Agent, Runner, function_tool
        except Exception as exc:
            return OpenAIAgentReview(
                used=False,
                model=status.model,
                summary=f"OpenAI Agents SDK import failed: {exc}",
            )

        rules_payload = [
            {
                "id": rule.id,
                "title": rule.title,
                "severity": rule.severity,
                "tags": rule.tags,
            }
            for rule in design_rules
        ]

        @function_tool
        def get_design_rules() -> str:
            """Return the design rules relevant to this RTL review."""

            return json.dumps(rules_payload)

        @function_tool
        def get_recent_failures() -> str:
            """Return recent verification or synthesis failures as JSON."""

            return json.dumps(failures[-5:])

        agent_kwargs: dict[str, Any] = {}
        if self.model:
            agent_kwargs["model"] = self.model

        agent = Agent(
            name="ChipFlow RTL Reviewer",
            instructions=(
                "You review small synthesizable Verilog for simple digital designs. "
                "Use the provided tools to inspect design rules and recent failures. "
                "Return a concise JSON object with keys: verdict, summary, risks. "
                "Do not include hidden reasoning."
            ),
            tools=[get_design_rules, get_recent_failures],
            **agent_kwargs,
        )
        prompt = (
            "Review this RTL candidate against the specification. "
            "Focus on reset polarity, enable hold behavior, explicit widths, "
            "overflow behavior, and synthesizability.\n\n"
            f"SPECIFICATION:\n{specification}\n\nVERILOG:\n{verilog}"
        )
        try:
            result = Runner.run_sync(agent, prompt)
        except Exception as exc:  # pragma: no cover - remote/provider dependent
            return OpenAIAgentReview(
                used=False,
                model=status.model,
                summary=f"OpenAI Agents SDK run failed: {exc}",
            )

        output = str(getattr(result, "final_output", "")).strip()
        return OpenAIAgentReview(
            used=True,
            model=status.model,
            summary=_summarize_review(output),
            raw_output=output,
        )


def _summarize_review(output: str) -> str:
    if not output:
        return "SDK review completed without textual output."
    try:
        parsed = json.loads(output)
        summary = parsed.get("summary") or parsed.get("verdict")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
    except json.JSONDecodeError:
        pass
    first_line = output.splitlines()[0].strip()
    return first_line[:240]

