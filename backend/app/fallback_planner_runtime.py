from __future__ import annotations

"""Deep Agent planning 실패 시 사용할 최소 planner fallback 구현.

planner 축이 완전히 중단되지 않도록, bridge 텍스트 호출과 템플릿 기반 규칙으로
최소 실행 가능한 plan을 보장한다.
"""

import os
from typing import Any, Dict, List, Optional

from app.agent_runtime import PlannerRuntime, RuntimePlan
from app.classic_runtime import (
    build_planner_prompt,
    fallback_plan,
    normalize_plan_steps,
    parse_runtime_tasks,
    validate_runtime_plan_mcp_ids,
)
from app.llm_bridge import extract_json_object, invoke_bridge_text
from app.trace_logger import add_trace


class FallbackPlannerRuntime(PlannerRuntime):
    """Bridge 텍스트 호출 기반의 최소 planner fallback."""

    async def build_plan(
        self,
        command: str,
        soul: str,
        mcp_catalog: List[Dict[str, Any]],
        detailed: bool = False,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuntimePlan:
        """Deep Agent가 실패했을 때 최소 실행 가능한 MCP-aware plan을 만든다."""
        add_trace(
            context,
            "planner.fallback_attempted",
            stage="planning",
            planner_type="fallback_planner",
        )
        try:
            prompt = build_planner_prompt(command=command, soul=soul, mcp_catalog=mcp_catalog, detailed=detailed)
            raw, _ = await invoke_bridge_text(model=os.getenv("CODEX_CHAT_MODEL", "default"), prompt=prompt)
            payload = extract_json_object(raw)
            items = payload.get("plan")
            if isinstance(items, list) and items:
                add_trace(
                    context,
                    "planner.fallback_completed",
                    stage="planning",
                    planner_type="fallback_planner",
                    result="bridge_success",
                    task_count=len(items),
                )
                return RuntimePlan(
                    objective=command.strip(),
                    summary="요청된 목표를 수행하기 위한 승인 전 실행 계획입니다.",
                    proposed_tasks=normalize_plan_steps(
                        validate_runtime_plan_mcp_ids(parse_runtime_tasks(items), mcp_catalog)
                    ),
                )
        except Exception as exc:
            add_trace(
                context,
                "planner.fallback_bridge_failed",
                stage="planning",
                planner_type="fallback_planner",
                reason=str(exc),
            )
            pass

        add_trace(
            context,
            "planner.fallback_completed",
            stage="planning",
            planner_type="fallback_planner",
            result="template_fallback",
        )
        return RuntimePlan(
            objective=command.strip(),
            summary="요청된 목표를 수행하기 위한 승인 전 실행 계획입니다.",
            proposed_tasks=normalize_plan_steps(fallback_plan(command, detailed)),
        )
