from __future__ import annotations

"""환경변수에 따라 planner/executor 구현체를 조합한다.

JARVIS의 실제 런타임 선택을 한 곳에 모아, main API가 구체 구현체를 직접
알지 않도록 planner/executor 조립 책임을 담당한다.
"""

import os

from app.agent_runtime import ExecutorRuntime, PlannerRuntime
from app.deepagent_planner_runtime import DeepAgentPlannerRuntime
from app.deepagents_runtime import load_deepagents_config
from app.fallback_planner_runtime import FallbackPlannerRuntime
from app.sequential_thinking_assist import SequentialThinkingAssist, load_sequential_thinking_assist_config
from app.stable_executor_runtime import StableExecutorRuntime


def get_planner_runtime(system_prompt: str) -> PlannerRuntime:
    """환경변수에 따라 planner runtime 조합을 반환한다."""
    fallback_planner = FallbackPlannerRuntime()
    runtime_kind = os.getenv("JARVIS_AGENT_RUNTIME", "classic").strip().lower()

    if runtime_kind == "classic":
        return fallback_planner

    assist = None
    if runtime_kind == "sequential":
        assist = SequentialThinkingAssist(load_sequential_thinking_assist_config())

    return DeepAgentPlannerRuntime(
        config=load_deepagents_config(system_prompt),
        fallback_planner=fallback_planner,
        assist=assist,
    )


def get_executor_runtime() -> ExecutorRuntime:
    """항상 안정적인 실행기를 반환한다."""
    return StableExecutorRuntime()
