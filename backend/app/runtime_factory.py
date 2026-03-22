from __future__ import annotations

"""환경변수에 따라 classic 또는 deepagents runtime을 선택한다."""

import os

from app.agent_runtime import AgentRuntime
from app.classic_runtime import ClassicAgentRuntime, load_classic_runtime_config
from app.deepagents_runtime import DeepAgentsRuntime, load_deepagents_config


def get_agent_runtime(system_prompt: str) -> AgentRuntime:
    """환경변수에 따라 실제 런타임 구현체를 반환한다."""
    classic_runtime = ClassicAgentRuntime(load_classic_runtime_config())
    runtime_kind = os.getenv("JARVIS_AGENT_RUNTIME", "classic").strip().lower()

    if runtime_kind == "deepagents":
        return DeepAgentsRuntime(
            config=load_deepagents_config(system_prompt),
            fallback_runtime=classic_runtime,
        )

    return classic_runtime
