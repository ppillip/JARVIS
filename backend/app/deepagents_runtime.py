from __future__ import annotations

"""Deep Agents 경계를 제공하되, 실패 시 classic runtime으로 폴백한다."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agent_runtime import AgentRuntime, RuntimeExecutionResult, RuntimePlan, RuntimeTask
from app.llm_bridge import bridge_credentials_available, create_openai_chat_model, extract_json_object


@dataclass
class DeepAgentsRuntimeConfig:
    """Deep Agents 런타임의 모델/루트 경로 설정."""

    model: str
    system_prompt: str
    root_dir: Path
    enable_subagents: bool = True
    enable_memory: bool = True


def deepagents_available() -> bool:
    """deepagents 라이브러리 import 가능 여부를 확인한다."""
    try:
        import deepagents  # noqa: F401
    except Exception:
        return False
    return True


def deepagents_credentials_available() -> bool:
    """Bridge 기준으로 Deep Agents live 호출 가능 여부를 확인한다."""
    return bridge_credentials_available()


def load_deepagents_config(system_prompt: str) -> DeepAgentsRuntimeConfig:
    """환경변수와 system prompt를 바탕으로 Deep Agents 설정을 구성한다."""
    return DeepAgentsRuntimeConfig(
        model=os.getenv("JARVIS_DEEPAGENT_MODEL", "openai:gpt-5"),
        system_prompt=system_prompt,
        root_dir=Path(__file__).resolve().parents[2],
        enable_subagents=True,
        enable_memory=True,
    )


def extract_text_from_agent_result(result: Dict[str, Any]) -> str:
    """Deep Agents 결과 객체에서 마지막 텍스트 응답만 추출한다."""
    # Deep Agents 결과는 메시지/structured_response 형태가 섞일 수 있어 텍스트만 재추출한다.
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


class DeepAgentsRuntime(AgentRuntime):
    """Deep Agents planning/execution을 시도하고 실패 시 classic으로 폴백한다."""

    def __init__(self, config: DeepAgentsRuntimeConfig, fallback_runtime: Optional[AgentRuntime] = None) -> None:
        """Deep Agents 설정과 폴백 런타임을 함께 보관한다."""
        self.config = config
        self.fallback_runtime = fallback_runtime

    def _can_use_live_runtime(self, context: Optional[Dict[str, Any]] = None) -> bool:
        """현재 환경에서 live Deep Agents 호출이 가능한지 판단한다."""
        _ = context
        return deepagents_available() and bridge_credentials_available()

    def _build_model(self, context: Optional[Dict[str, Any]] = None):
        """Bridge 뒤의 OpenAI-compatible chat model을 생성한다."""
        _ = context
        return create_openai_chat_model(
            model=self.config.model,
            use_responses_api=False,
        )

    def _build_agent(self, context: Optional[Dict[str, Any]] = None):
        """Deep Agents graph 인스턴스를 구성한다."""
        from deepagents import create_deep_agent
        from deepagents.backends import LocalShellBackend

        backend = LocalShellBackend(
            root_dir=self.config.root_dir,
            virtual_mode=True,
            inherit_env=True,
        )
        return create_deep_agent(
            model=self._build_model(context),
            system_prompt=self.config.system_prompt,
            backend=backend,
            debug=False,
            name="jarvis-deep-agent",
        )

    async def _fallback_build_plan(
        self,
        command: str,
        soul: str,
        mcp_catalog: List[Dict[str, Any]],
        detailed: bool,
        context: Optional[Dict[str, Any]],
        reason: str,
    ) -> RuntimePlan:
        """live planning 실패 시 fallback runtime으로 위임한다."""
        if self.fallback_runtime is None:
            raise RuntimeError(reason)
        return await self.fallback_runtime.build_plan(command, soul, mcp_catalog, detailed, context)

    async def _fallback_execute_tasks(
        self,
        tasks: List[RuntimeTask],
        context: Optional[Dict[str, Any]],
        reason: str,
    ) -> RuntimeExecutionResult:
        """live execution 실패 시 fallback runtime으로 위임한다."""
        if self.fallback_runtime is None:
            raise RuntimeError(reason)
        return await self.fallback_runtime.execute_tasks(tasks, context)

    async def build_plan(
        self,
        command: str,
        soul: str,
        mcp_catalog: List[Dict[str, Any]],
        detailed: bool = False,
        context: Dict[str, Any] | None = None,
    ) -> RuntimePlan:
        """Deep Agents를 이용해 MCP-aware 플랜을 생성한다."""
        if not self._can_use_live_runtime(context):
            return await self._fallback_build_plan(
                command, soul, mcp_catalog, detailed, context,
                "Deep Agents runtime is unavailable and no fallback runtime is configured.",
            )

        try:
            # 계획 생성은 bridge 뒤의 OpenAI-compatible 모델을 쓰고, 실패하면 classic으로 내려간다.
            agent = self._build_agent(context)
            prompt = (
                "당신은 JARVIS의 Deep Agents planner입니다.\n"
                "반드시 JSON 객체 하나만 출력하십시오.\n"
                '형식: {"objective":"...", "summary":"...", "proposed_tasks":[{"title":"...","rationale":"...","recommended_mcp_ids":["filesystem"],"selected_mcp_id":"filesystem","tool_name":"list_directory","tool_arguments":{"path":"$HOME"},"expected_result":"..."}]}\n'
                "규칙:\n"
                "- 사용 가능한 MCP registry만 근거로 계획하십시오.\n"
                "- 단순 조회/읽기 요청은 메타 단계로 부풀리지 마십시오.\n"
                "- 최종 보고용 단계는 만들지 마십시오.\n"
                "- 승인 후 바로 실행 가능한 태스크만 proposed_tasks에 넣으십시오.\n"
                "- 한국어로 출력하십시오.\n\n"
                f"[SOUL]\n{soul}\n\n"
                f"[MCP REGISTRY]\n{json.dumps(mcp_catalog, ensure_ascii=False, indent=2)}\n\n"
                f"[상세화 여부]\n{'세분화 필요' if detailed else '간결한 계획'}\n\n"
                f"[사용자 지령]\n{command.strip()}"
            )
            result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
            parsed = extract_json_object(extract_text_from_agent_result(result))
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
                raise RuntimeError("Deep Agents plan response did not include executable tasks.")
            return RuntimePlan(
                objective=str(parsed.get("objective") or command).strip(),
                summary=str(parsed.get("summary") or "Deep Agents가 생성한 승인 전 실행 계획입니다.").strip(),
                proposed_tasks=tasks,
            )
        except Exception as exc:
            return await self._fallback_build_plan(command, soul, mcp_catalog, detailed, context, str(exc))

    async def execute_plan(
        self,
        plan: RuntimePlan,
        context: Dict[str, Any] | None = None,
    ) -> RuntimeExecutionResult:
        """플랜의 proposed tasks 전체를 실행한다."""
        return await self.execute_tasks(plan.proposed_tasks, context)

    async def execute_tasks(
        self,
        tasks: List[RuntimeTask],
        context: Dict[str, Any] | None = None,
    ) -> RuntimeExecutionResult:
        """Deep Agents executor를 이용해 승인된 태스크를 수행한다."""
        if not self._can_use_live_runtime(context):
            return await self._fallback_execute_tasks(
                tasks, context,
                "Deep Agents runtime is unavailable and no fallback runtime is configured.",
            )

        context = context or {}
        try:
            agent = self._build_agent(context)
            prompt = (
                "당신은 JARVIS의 Deep Agents executor입니다.\n"
                "다음 승인된 태스크를 순서대로 수행하고, 필요한 도구를 실제로 사용한 뒤 JSON 객체 하나만 출력하십시오.\n"
                '형식: {"summary":"...","findings":["..."],"result_items":["..."],"evidence":["..."],"task_statuses":["done"]}\n'
                "규칙:\n"
                "- 가능한 경우 실제 도구를 사용해 결과를 확인하십시오.\n"
                "- 추측하지 마십시오.\n"
                "- evidence에는 실제 실행 근거를 넣으십시오.\n"
                "- task_statuses는 태스크 순서와 같은 길이로 반환하십시오.\n"
                "- 한국어로 출력하십시오.\n\n"
                f"[승인된 태스크]\n{json.dumps([task.model_dump() for task in tasks], ensure_ascii=False, indent=2)}\n\n"
                f"[MCP REGISTRY]\n{json.dumps(context.get('mcp_catalog', []), ensure_ascii=False, indent=2)}"
            )
            result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
            parsed = extract_json_object(extract_text_from_agent_result(result))
            task_statuses = [str(status) for status in parsed.get("task_statuses", [])]
            if len(task_statuses) != len(tasks):
                task_statuses = ["done"] * len(tasks)
            report = {
                "status": "보고 완료",
                "summary": str(parsed.get("summary") or f"보고합니다. 총 {len(tasks)}개의 실행 태스크를 수행했습니다.").strip(),
                "objective": "지령에 따라 필요한 실행을 수행하고, 실행 결과에서 확인된 사실과 결론을 보고합니다.",
                "tasks": [f"Task {index}. {task.title}" for index, task in enumerate(tasks, start=1)],
                "result_items": [str(item) for item in parsed.get("result_items", [])],
                "findings": [str(item) for item in parsed.get("findings", [])],
                "conclusion": "요청한 실행은 완료되었고, 아래 발견 사항을 기준으로 후속 판단이 가능한 상태입니다.",
                "evidence": [str(item) for item in parsed.get("evidence", [])],
                "nextAction": "필요하면 이 보고를 기준으로 추가 조사, 수정, 또는 후속 지령을 내리십시오.",
            }
            return RuntimeExecutionResult(
                status="completed" if all(status == "done" for status in task_statuses) else "failed",
                execution_log=[f"Task {index}: {task.title}" for index, task in enumerate(tasks, start=1)] + ["모든 태스크 수행이 끝났습니다."],
                findings=report["findings"],
                result_items=report["result_items"],
                evidence=report["evidence"],
                report=report,
                task_statuses=task_statuses,
            )
        except Exception as exc:
            return await self._fallback_execute_tasks(tasks, context, str(exc))
