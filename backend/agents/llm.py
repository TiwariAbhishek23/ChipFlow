"""Optional LLM client facade.

The deterministic tool path keeps the demo reliable, but this facade shows
where Claude or OpenAI can be connected for broader RTL generation later.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from backend.agents.openai_agents_sdk import OpenAIAgentsSDKClient


@dataclass(frozen=True)
class LLMResponse:
    provider: str
    text: str


class LLMClient:
    def __init__(self) -> None:
        self.provider = "deterministic"
        self.openai_agents = OpenAIAgentsSDKClient()

    def available_provider(self) -> str:
        sdk_status = self.openai_agents.status()
        if sdk_status.enabled:
            return "openai_agents"
        if os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic"
        if os.getenv("OPENAI_API_KEY"):
            return "openai"
        return "deterministic"

    async def summarize(self, prompt: str) -> LLMResponse:
        provider = self.available_provider()
        if provider == "openai_agents":
            review = self.openai_agents.review_rtl(
                specification=prompt,
                verilog="",
                design_rules=[],
                failures=[],
            )
            return LLMResponse(provider=provider, text=review.raw_output or review.summary)
        if provider == "anthropic":
            try:
                from langchain_anthropic import ChatAnthropic

                llm = ChatAnthropic(model=os.getenv("CHIPFLOW_ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"))
                message = await llm.ainvoke(prompt)
                return LLMResponse(provider=provider, text=str(message.content))
            except Exception as exc:  # pragma: no cover - provider dependent
                return LLMResponse(provider="deterministic", text=f"LLM unavailable: {exc}")
        if provider == "openai":
            try:
                from langchain_openai import ChatOpenAI

                llm = ChatOpenAI(model=os.getenv("CHIPFLOW_OPENAI_MODEL", "gpt-4o"))
                message = await llm.ainvoke(prompt)
                return LLMResponse(provider=provider, text=str(message.content))
            except Exception as exc:  # pragma: no cover - provider dependent
                return LLMResponse(provider="deterministic", text=f"LLM unavailable: {exc}")
        return LLMResponse(provider="deterministic", text="Deterministic planner path selected.")
