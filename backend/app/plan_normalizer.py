from __future__ import annotations

"""Planner 출력과 trace를 공통 normalized plan schema로 변환한다.

planner 구현체가 달라도 프론트와 executor가 같은 스키마만 보게 하기 위해
중간 표준화 계층을 담당한다.
"""

from typing import Any, Dict, List

from app.agent_runtime import RuntimePlan
from app.plan_schema import NormalizedPlan, NormalizedTaskDraft, PlannerMetadata


def _collect_required_capabilities(plan: RuntimePlan) -> List[str]:
    """RuntimePlan에서 요구 capability/MCP id를 추출한다."""
    values: List[str] = []
    for task in plan.proposed_tasks:
        for item in task.recommended_mcp_ids:
            if item and item not in values:
                values.append(item)
        if task.selected_mcp_id and task.selected_mcp_id not in values:
            values.append(task.selected_mcp_id)
    return values


def _build_planner_metadata(trace: List[Dict[str, Any]], strategy_applied: bool) -> PlannerMetadata:
    """trace 이벤트를 읽어 planner 메타데이터를 구성한다."""
    planner_type = "unknown"
    fallback_used = False

    for item in trace:
        event = str(item.get("event", "")).strip()
        if event == "planner.deepagent_completed":
            planner_type = "deep_agent_planner"
        elif event == "planner.fallback_triggered":
            planner_type = "fallback_planner"
            fallback_used = True
        elif event == "planner.deepagent_failed" and planner_type == "unknown":
            planner_type = "deep_agent_planner"

    if planner_type == "unknown" and fallback_used:
        planner_type = "fallback_planner"

    return PlannerMetadata(
        planner_type=planner_type,
        planner_version="v1",
        fallback_used=fallback_used,
        sequential_thinking_applied=strategy_applied,
    )


def normalize_runtime_plan(plan: RuntimePlan, trace: List[Dict[str, Any]] | None = None) -> NormalizedPlan:
    """RuntimePlan을 planner-independent normalized plan으로 변환한다."""
    trace = trace or []
    expected_outputs = [task.expected_result for task in plan.proposed_tasks if task.expected_result.strip()]
    return NormalizedPlan(
        goal=plan.objective,
        intent="command",
        summary=plan.summary,
        assumptions=[],
        constraints=[],
        required_capabilities=_collect_required_capabilities(plan),
        approval_required=True,
        risks=list(plan.strategy.risks) if plan.strategy else [],
        expected_outputs=expected_outputs,
        tasks_draft=[
            NormalizedTaskDraft(
                title=task.title,
                rationale=task.rationale,
                recommended_mcp_ids=list(task.recommended_mcp_ids),
                selected_mcp_id=task.selected_mcp_id,
                tool_name=task.tool_name,
                tool_arguments=dict(task.tool_arguments),
                expected_result=task.expected_result,
            )
            for task in plan.proposed_tasks
        ],
        planner_metadata=_build_planner_metadata(trace, bool(plan.strategy and plan.strategy.applied)),
        strategy=plan.strategy,
    )
