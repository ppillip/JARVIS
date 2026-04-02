from __future__ import annotations

"""Deep Agent를 최종 planner / replanner로 사용하는 planner runtime.

Capability Map과 soul prompt, 필요 시 ST 전략 요약을 입력으로 받아
실행 가능한 MCP-aware plan을 생성하는 planner 축의 핵심 구현이다.
"""

import json
from typing import Any, Dict, List, Optional

from app.agent_runtime import PlannerRuntime, RuntimePlan, RuntimeTask
from app.deepagents_runtime import DeepAgentsRuntimeConfig, deepagents_available
from app.fallback_reasons import classify_fallback_reason
from app.llm_bridge import bridge_credentials_available, create_openai_chat_model, extract_json_object
from app.prompt_store import get_prompt_content, render_prompt_template
from app.trace_logger import add_trace


class DeepAgentPlannerRuntime(PlannerRuntime):
    """Capability registry를 보고 실행 가능한 MCP-aware plan을 만드는 최종 planner."""

    def __init__(
        self,
        config: DeepAgentsRuntimeConfig,
        fallback_planner: Optional[PlannerRuntime] = None,
        assist: Optional[Any] = None,
    ) -> None:
        """Deep Agents 설정, fallback planner, optional ST assist를 보관한다."""
        self.config = config
        self.fallback_planner = fallback_planner
        self.assist = assist

    def _can_use_live_runtime(self) -> bool:
        """현재 환경에서 live Deep Agent planning이 가능한지 판단한다."""
        return deepagents_available() and bridge_credentials_available()

    def _build_model(self):
        """Bridge 뒤의 OpenAI-compatible chat model을 생성한다."""
        return create_openai_chat_model(model=self.config.model, use_responses_api=False)

    def _build_agent(self):
        """Deep Agents graph 인스턴스를 구성한다."""
        from deepagents import create_deep_agent
        from deepagents.backends import LocalShellBackend

        backend = LocalShellBackend(
            root_dir=self.config.root_dir,
            virtual_mode=True,
            inherit_env=True,
        )
        return create_deep_agent(
            model=self._build_model(),
            system_prompt=self.config.system_prompt,
            backend=backend,
            debug=False,
            name="jarvis-deep-agent-planner",
        )

    async def _fallback(
        self,
        command: str,
        soul: str,
        mcp_catalog: List[Dict[str, Any]],
        detailed: bool,
        context: Optional[Dict[str, Any]],
        reason: str,
    ) -> RuntimePlan:
        """Deep Agent planning 실패 시 fallback planner로 위임한다."""
        if self.fallback_planner is None:
            raise RuntimeError(reason)
        reason_code, normalized_reason = classify_fallback_reason(reason)
        add_trace(
            context,
            "planner.fallback_triggered",
            stage="planning",
            planner_type="fallback_planner",
            source="deep_agent_planner",
            fallback_reason_code=reason_code,
            reason=normalized_reason,
        )
        return await self.fallback_planner.build_plan(command, soul, mcp_catalog, detailed, context)

    async def build_plan(
        self,
        command: str,
        soul: str,
        mcp_catalog: List[Dict[str, Any]],
        detailed: bool = False,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuntimePlan:
        """Sequential Thinking 보조를 optional로 반영한 뒤 Deep Agent planning을 수행한다."""
        context = context or {}
        delegated_command = command.strip()
        strategy = None

        if self.assist is not None:
            assist_result = await self.assist.prepare(command=command, soul=soul, mcp_catalog=mcp_catalog)
            strategy = assist_result.get("strategy")
            if assist_result.get("delegated_command"):
                delegated_command = str(assist_result["delegated_command"]).strip()
            if strategy and strategy.applied:
                add_trace(
                    context,
                    "planner.assist_applied",
                    stage="planning",
                    planner_type="deep_agent_planner",
                    assistant="sequential_thinking",
                    applied=True,
                )

        if not self._can_use_live_runtime():
            reason_code, normalized_reason = classify_fallback_reason("Deep Agent planner is unavailable.")
            add_trace(
                context,
                "planner.deepagent_attempted",
                stage="planning",
                planner_type="deep_agent_planner",
                attempted=True,
                result="unavailable",
                fallback_reason_code=reason_code,
                reason=normalized_reason,
            )
            plan = await self._fallback(
                delegated_command,
                soul,
                mcp_catalog,
                detailed,
                context,
                "Deep Agent planner is unavailable.",
            )
            plan.objective = command.strip()
            if strategy:
                plan.strategy = strategy
            return plan

        add_trace(
            context,
            "planner.deepagent_attempted",
            stage="planning",
            planner_type="deep_agent_planner",
            attempted=True,
            result="started",
        )
        try:
            agent = self._build_agent()
            template = get_prompt_content(
                "deepagent_planner_system",
                fallback=(
                    "당신은 JARVIS의 Deep Agent Planner입니다.\n"
                    "Capability registry를 보고 실행 가능한 MCP-aware plan만 만드십시오.\n"
                    "반드시 JSON 객체 하나만 출력하십시오.\n"
                    '형식: {"objective":"...","summary":"...","proposed_tasks":[{"title":"...","rationale":"...","recommended_mcp_ids":["filesystem"],"selected_mcp_id":"filesystem","tool_name":"list_directory","tool_arguments":{"path":"$HOME"},"expected_result":"..."}]}\n'
                    "규칙:\n"
                    "- 최종 planner 책임은 당신에게 있다.\n"
                    "- MCP capability를 근거로 selected_mcp_id, tool_name, tool_arguments를 구체화한다.\n"
                    "- 단순 조회는 메타 단계로 부풀리지 않는다.\n"
                    "- 최종 보고 단계는 만들지 않는다.\n"
                    "- '최종 보고가 하나'라는 이유로 실행 태스크 수를 1개로 줄이지 않는다.\n"
                    "- 사용자가 여러 결과값을 요구하면, 필요한 데이터 취득/필터링/정렬/비교/집계를 별도 task로 분해한다.\n"
                    "- 하나의 MCP만 쓰더라도 중간 계산이 필요하면 여러 task를 만들 수 있다.\n"
                    "- retrieval task와 derivation task를 구분한다. 조회와 계산/판단을 한 task에 뭉개지 않는다.\n"
                    "- 특정 tool 출력이 필요한 메타데이터를 보장하지 않으면 후속 정보 수집 task를 추가한다.\n"
                    "- 최소 task 수가 아니라 정확한 답을 만들 수 있는 최소한의 task 수를 선택한다.\n"
                    "- 승인 후 바로 실행 가능한 proposed_tasks만 반환한다.\n"
                    "- 각 task의 expected_result는 그 task가 직접 산출하는 결과만 적는다.\n"
                    "- Playwright MCP를 선택했으면 tool_name은 반드시 open, snapshot, click, fill, press, screenshot 중 하나만 사용한다.\n"
                    "- open_page, goto, navigate 같은 별칭은 사용하지 않는다. 반드시 open을 쓴다.\n"
                    "- Playwright open의 tool_arguments는 최소 {\"url\":\"https://...\"} 형태를 포함해야 한다.\n"
                    "- Playwright task 하나에는 브라우저 액션 하나만 담는다. click과 fill, fill과 submit을 한 task에 합치지 않는다.\n"
                    "- Playwright fill은 한 task에 한 입력란만 다룬다. 아이디/비밀번호처럼 여러 입력란이면 fill task를 여러 개로 분해한다.\n"
                    "- Playwright fill의 tool_arguments는 {\"target\":\"입력란 라벨 또는 의미\",\"value\":\"실제 입력값\"} 형태를 우선 사용한다.\n"
                    "- Playwright click/fill/read_text의 target은 CSS selector나 표현식이 아니라 사람이 읽을 수 있는 의미 라벨을 사용한다.\n"
                    "- Playwright click에서 ref를 모르면 target 또는 label 수준으로 남기고, executor가 snapshot으로 실제 ref를 찾을 수 있게 한다.\n"
                    "- 한국어로 출력한다.\n\n"
                    "[SOUL]\n{{soul}}\n\n"
                    "[CAPABILITY REGISTRY]\n{{mcps}}\n\n"
                    "[상세화 여부]\n{{detail_mode}}\n\n"
                    "[사용자 지령]\n{{command}}"
                ),
            )
            prompt = render_prompt_template(
                template,
                {
                    "soul": soul,
                    "mcps": json.dumps(mcp_catalog, ensure_ascii=False, indent=2),
                    "detail_mode": "세분화 필요" if detailed else "간결한 계획",
                    "command": delegated_command,
                },
            )
            result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
            parsed = extract_json_object(self._extract_text_from_agent_result(result))
            tasks = [
                RuntimeTask(
                    title=str(item.get("title") or item.get("step") or "").strip(),
                    rationale=str(item.get("rationale", "")).strip(),
                    recommended_mcp_ids=[str(v) for v in item.get("recommended_mcp_ids", []) if str(v).strip()],
                    selected_mcp_id=(str(item.get("selected_mcp_id", "")).strip() or None),
                    tool_name=(str(item.get("tool_name", "")).strip() or None),
                    tool_arguments=item.get("tool_arguments", {}) if isinstance(item.get("tool_arguments"), dict) else {},
                    expected_result=str(item.get("expected_result", "")).strip(),
                )
                for item in parsed.get("proposed_tasks", [])
                if isinstance(item, dict) and str(item.get("title") or item.get("step") or "").strip()
            ]
            if not tasks:
                raise RuntimeError("Deep Agent planner did not return executable tasks.")
            add_trace(
                context,
                "planner.deepagent_completed",
                stage="planning",
                planner_type="deep_agent_planner",
                result="success",
                task_count=len(tasks),
            )
            return RuntimePlan(
                objective=command.strip(),
                summary=str(parsed.get("summary") or "Deep Agent가 생성한 승인 전 실행 계획입니다.").strip(),
                strategy=strategy,
                proposed_tasks=tasks,
            )
        except Exception as exc:
            reason_code, normalized_reason = classify_fallback_reason(str(exc))
            add_trace(
                context,
                "planner.deepagent_failed",
                stage="planning",
                planner_type="deep_agent_planner",
                result="failed",
                fallback_reason_code=reason_code,
                reason=normalized_reason,
            )
            plan = await self._fallback(delegated_command, soul, mcp_catalog, detailed, context, str(exc))
            plan.objective = command.strip()
            if strategy:
                plan.strategy = strategy
            return plan

    @staticmethod
    def _extract_text_from_agent_result(result: Dict[str, Any]) -> str:
        """Deep Agents 결과 객체에서 마지막 텍스트 응답만 추출한다."""
        messages = result.get("messages") if isinstance(result, dict) else None
        if isinstance(messages, list):
            for message in reversed(messages):
                content = getattr(message, "content", None)
                if isinstance(content, str) and content.strip():
                    return content.strip()
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                            parts.append(str(item["text"]))
                    joined = "\n".join(part for part in parts if part.strip())
                    if joined.strip():
                        return joined.strip()
        structured = result.get("structured_response") if isinstance(result, dict) else None
        if structured is not None:
            if hasattr(structured, "model_dump"):
                return json.dumps(structured.model_dump(), ensure_ascii=False)
            if isinstance(structured, dict):
                return json.dumps(structured, ensure_ascii=False)
        return ""
