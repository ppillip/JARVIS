from __future__ import annotations

"""초기 Sequential Thinking runtime 실험 구현.

현재 아키텍처에서는 assist 계층으로 이동 중인 과거 경계이며,
전략 정리 후 delegate runtime으로 넘기던 흐름을 보존한다.
"""

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.agent_runtime import (
    AgentRuntime,
    RuntimeExecutionResult,
    RuntimePlan,
    RuntimeStrategy,
    RuntimeStrategyOption,
    RuntimeTask,
)
from app.llm_bridge import invoke_bridge_json
from app.prompt_store import get_prompt_content, render_prompt_template


@dataclass
class SequentialThinkingRuntimeConfig:
    """Sequential Thinking 라우팅과 요약 생성에 필요한 설정."""

    model: str
    enabled: bool = True


def load_sequential_thinking_config() -> SequentialThinkingRuntimeConfig:
    """환경변수 기준으로 Sequential Thinking 런타임 설정을 만든다."""
    return SequentialThinkingRuntimeConfig(
        model=os.getenv("JARVIS_SEQUENTIAL_MODEL", os.getenv("CODEX_CHAT_MODEL", "default")),
        enabled=os.getenv("JARVIS_SEQUENTIAL_ENABLED", "true").strip().lower() != "false",
    )


def serialize_mcps_for_prompt(mcp_catalog: List[Dict[str, Any]]) -> str:
    """전략 판단 프롬프트에 넣을 MCP 레지스트리 요약을 직렬화한다."""
    return json.dumps(
        [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "description": item.get("description"),
                "capabilities": item.get("capabilities"),
                "expected_input": item.get("expected_input"),
                "expected_output": item.get("expected_output"),
                "risk_level": item.get("risk_level"),
            }
            for item in mcp_catalog
        ],
        ensure_ascii=False,
        indent=2,
    )


def build_router_prompt(command: str, soul: str, mcp_catalog: List[Dict[str, Any]]) -> str:
    """Sequential Thinking 필요 여부를 판정하는 프롬프트를 렌더링한다."""
    template = get_prompt_content(
        "sequential_thinking_router",
        fallback=(
            "너는 JARVIS의 Sequential Thinking 라우터다.\n"
            "주어진 지령이 아래 조건 중 하나에 해당하면 sequential thinking이 필요하다고 판단한다.\n"
            "- 사용자 의도가 모호함\n"
            "- 해결 경로가 2개 이상으로 갈림\n"
            "- 승인 전에 전략 옵션을 비교해 보여줘야 함\n"
            "- 한 번 만든 plan이 자주 틀어질 가능성이 큼\n"
            "- high-stakes라서 한 번 더 사고 정리가 필요함\n\n"
            "반드시 JSON 객체 하나만 출력한다.\n"
            '형식: {"use_sequential_thinking":true,"reason":"...","signals":["..."]}\n'
            '또는 {"use_sequential_thinking":false,"reason":"...","signals":["..."]}\n'
            "규칙:\n"
            "- 단순 조회/단일 도구 호출/명확한 CRUD 요청은 false를 우선한다.\n"
            "- MCP registry를 보고 가능한 도구 조합이 하나로 명확하면 false를 우선한다.\n"
            "- 한국어로 출력한다.\n\n"
            "[SOUL]\n{{soul}}\n\n"
            "[MCP REGISTRY]\n{{mcps}}\n\n"
            "[사용자 지령]\n{{command}}"
        ),
    )
    return render_prompt_template(
        template,
        {
            "soul": soul,
            "mcps": serialize_mcps_for_prompt(mcp_catalog),
            "command": command.strip(),
        },
    )


def build_thinking_prompt(command: str, soul: str, mcp_catalog: List[Dict[str, Any]], reason: str) -> str:
    """전략 옵션과 권장 경로를 정리하는 Sequential Thinking 프롬프트를 만든다."""
    template = get_prompt_content(
        "sequential_thinking_system",
        fallback=(
            "너는 JARVIS의 Sequential Thinking 엔진이다.\n"
            "주어진 지령을 즉시 실행하지 말고, 전략적으로 생각을 정리해 Deep Agent에게 넘길 briefing을 만든다.\n\n"
            "반드시 JSON 객체 하나만 출력한다.\n"
            '형식: {"summary":"...","recommended_strategy":"...","options":[{"name":"...","approach":"...","tradeoffs":"..."}],"risks":["..."],"handoff_brief":"..."}\n'
            "규칙:\n"
            "- summary는 현재 문제를 한두 문장으로 요약한다.\n"
            "- recommended_strategy는 가장 적합한 경로를 한 문장으로 적는다.\n"
            "- options는 2~3개까지 제시하되, 실제 MCP registry에 근거한 대안만 넣는다.\n"
            "- handoff_brief는 Deep Agent가 바로 실행 계획을 만들 수 있도록 구체적으로 쓴다.\n"
            "- 한국어로 출력한다.\n\n"
            "[SOUL]\n{{soul}}\n\n"
            "[Sequential Thinking 필요 이유]\n{{reason}}\n\n"
            "[MCP REGISTRY]\n{{mcps}}\n\n"
            "[사용자 지령]\n{{command}}"
        ),
    )
    return render_prompt_template(
        template,
        {
            "soul": soul,
            "reason": reason,
            "mcps": serialize_mcps_for_prompt(mcp_catalog),
            "command": command.strip(),
        },
    )


def build_runtime_strategy(payload: Dict[str, Any], reason: str) -> RuntimeStrategy:
    """Sequential Thinking JSON 결과를 RuntimeStrategy로 정규화한다."""
    options: List[RuntimeStrategyOption] = []
    for option in payload.get("options", []) if isinstance(payload.get("options"), list) else []:
        if not isinstance(option, dict):
            continue
        name = str(option.get("name", "")).strip()
        if not name:
            continue
        options.append(
            RuntimeStrategyOption(
                name=name,
                approach=str(option.get("approach", "")).strip(),
                tradeoffs=str(option.get("tradeoffs", "")).strip(),
            )
        )
    return RuntimeStrategy(
        applied=True,
        summary=str(payload.get("summary", "")).strip(),
        recommended_strategy=str(payload.get("recommended_strategy", "")).strip(),
        options=options,
        risks=[str(item).strip() for item in payload.get("risks", []) if str(item).strip()] if isinstance(payload.get("risks"), list) else [],
        reason=reason.strip(),
    )


class SequentialThinkingRuntime(AgentRuntime):
    """필요할 때만 전략 정리를 수행하고 delegate runtime으로 위임한다."""

    def __init__(self, config: SequentialThinkingRuntimeConfig, delegate_runtime: AgentRuntime) -> None:
        """설정과 실제 planning/execution을 맡는 delegate runtime을 저장한다."""
        self.config = config
        self.delegate_runtime = delegate_runtime

    async def _should_use_sequential_thinking(
        self,
        command: str,
        soul: str,
        mcp_catalog: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """현재 지령에 Sequential Thinking이 필요한지 브리지로 판단한다."""
        if not self.config.enabled:
            return {"use_sequential_thinking": False, "reason": "Sequential Thinking이 비활성화되어 있습니다.", "signals": []}
        try:
            return await invoke_bridge_json(
                model=self.config.model,
                prompt=build_router_prompt(command=command, soul=soul, mcp_catalog=mcp_catalog),
            )
        except Exception:
            return {"use_sequential_thinking": False, "reason": "Sequential Thinking 라우팅 실패", "signals": []}

    async def _build_strategy_payload(
        self,
        command: str,
        soul: str,
        mcp_catalog: List[Dict[str, Any]],
        reason: str,
    ) -> Dict[str, Any]:
        """전략 옵션 비교와 handoff brief를 생성한다."""
        try:
            return await invoke_bridge_json(
                model=self.config.model,
                prompt=build_thinking_prompt(command=command, soul=soul, mcp_catalog=mcp_catalog, reason=reason),
            )
        except Exception:
            return {
                "summary": reason or "전략 정리를 생성하지 못했습니다.",
                "recommended_strategy": "현재 활성 MCP를 기준으로 가장 직접적인 실행 경로를 우선합니다.",
                "options": [],
                "risks": [],
                "handoff_brief": command.strip(),
            }

    async def build_plan(
        self,
        command: str,
        soul: str,
        mcp_catalog: List[Dict[str, Any]],
        detailed: bool = False,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuntimePlan:
        """필요 시 ST 요약을 만든 뒤 delegate runtime으로 실행 계획 생성을 위임한다."""
        router_payload = await self._should_use_sequential_thinking(command=command, soul=soul, mcp_catalog=mcp_catalog)
        if not bool(router_payload.get("use_sequential_thinking")):
            return await self.delegate_runtime.build_plan(command, soul, mcp_catalog, detailed, context)

        strategy_payload = await self._build_strategy_payload(
            command=command,
            soul=soul,
            mcp_catalog=mcp_catalog,
            reason=str(router_payload.get("reason", "")).strip(),
        )
        strategy = build_runtime_strategy(strategy_payload, str(router_payload.get("reason", "")).strip())
        handoff_brief = str(strategy_payload.get("handoff_brief", "")).strip()
        delegated_command = command.strip()
        if handoff_brief:
            delegated_command = (
                f"{command.strip()}\n\n"
                "[Sequential Thinking 정리]\n"
                f"{handoff_brief}"
            ).strip()

        plan = await self.delegate_runtime.build_plan(delegated_command, soul, mcp_catalog, detailed, context)
        plan.objective = command.strip()
        plan.strategy = strategy
        return plan

    async def execute_plan(
        self,
        plan: RuntimePlan,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuntimeExecutionResult:
        """실행 단계는 delegate runtime으로 그대로 넘긴다."""
        return await self.delegate_runtime.execute_plan(plan, context)

    async def execute_tasks(
        self,
        tasks: List[RuntimeTask],
        context: Optional[Dict[str, Any]] = None,
    ) -> RuntimeExecutionResult:
        """확정 태스크 실행도 delegate runtime에 맡긴다."""
        return await self.delegate_runtime.execute_tasks(tasks, context)
