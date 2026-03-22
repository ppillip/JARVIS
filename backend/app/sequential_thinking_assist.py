from __future__ import annotations

"""Sequential Thinking을 planner 앞단 보조로만 사용하는 assist 계층.

최종 task를 확정하지 않고, 모호한 요청의 전략 정리와 리스크 점검,
분기 비교만 수행한 뒤 Deep Agent planner에 handoff한다.
"""

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List

from app.agent_runtime import RuntimeStrategy, RuntimeStrategyOption
from app.llm_bridge import invoke_bridge_json
from app.prompt_store import get_prompt_content, render_prompt_template


@dataclass
class SequentialThinkingAssistConfig:
    """Sequential Thinking assist 실행 설정."""

    model: str
    enabled: bool = True


def load_sequential_thinking_assist_config() -> SequentialThinkingAssistConfig:
    """환경변수 기준으로 Sequential Thinking assist 설정을 만든다."""
    return SequentialThinkingAssistConfig(
        model=os.getenv("JARVIS_SEQUENTIAL_MODEL", os.getenv("CODEX_CHAT_MODEL", "default")),
        enabled=os.getenv("JARVIS_SEQUENTIAL_ENABLED", "true").strip().lower() != "false",
    )


def serialize_capabilities(mcp_catalog: List[Dict[str, Any]]) -> str:
    """전략 판단 프롬프트에 넣을 capability 요약을 직렬화한다."""
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


class SequentialThinkingAssist:
    """필요할 때만 전략 요약을 만들어 planner에 handoff하는 보조 계층."""

    def __init__(self, config: SequentialThinkingAssistConfig) -> None:
        """모델과 활성화 여부를 저장한다."""
        self.config = config

    @staticmethod
    def _looks_simple_request(command: str) -> bool:
        """명확한 단일 도구 요청은 ST 없이 바로 planner로 넘기기 위한 휴리스틱."""
        text = command.strip().lower()
        simple_keywords = [
            "리스트",
            "목록",
            "보여줘",
            "찾아줘",
            "읽어줘",
            "개수",
            "몇개",
            "몇 개",
            "최근 파일",
            "최신 파일",
            "폴더",
            "downloads",
            "desktop",
        ]
        branching_keywords = [
            "혹은",
            "또는",
            "비교",
            "전략",
            "옵션",
            "분기",
            "우선순위",
            "리스크",
            "여러 방법",
            "복수",
        ]
        if any(keyword in text for keyword in branching_keywords):
            return False
        return any(keyword in text for keyword in simple_keywords)

    async def prepare(self, command: str, soul: str, mcp_catalog: List[Dict[str, Any]]) -> Dict[str, Any]:
        """필요할 때만 전략 정리를 수행하고 planner용 handoff brief를 만든다."""
        if not self.config.enabled:
            return {"delegated_command": command.strip(), "strategy": None}

        if self._looks_simple_request(command):
            return {"delegated_command": command.strip(), "strategy": None}

        router = await self._route(command=command, soul=soul, mcp_catalog=mcp_catalog)
        if not bool(router.get("use_sequential_thinking")):
            return {"delegated_command": command.strip(), "strategy": None}

        reason = str(router.get("reason", "")).strip()
        strategy_payload = await self._think(command=command, soul=soul, mcp_catalog=mcp_catalog, reason=reason)
        strategy = self._build_strategy(strategy_payload, reason)
        handoff_brief = str(strategy_payload.get("handoff_brief", "")).strip()
        delegated_command = command.strip()
        if handoff_brief:
            delegated_command = f"{command.strip()}\n\n[Sequential Thinking 정리]\n{handoff_brief}".strip()
        return {"delegated_command": delegated_command, "strategy": strategy}

    async def _route(self, command: str, soul: str, mcp_catalog: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Sequential Thinking 개입 필요 여부를 판단한다."""
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
                "[CAPABILITY REGISTRY]\n{{mcps}}\n\n"
                "[사용자 지령]\n{{command}}"
            ),
        )
        prompt = render_prompt_template(
            template,
            {"soul": soul, "mcps": serialize_capabilities(mcp_catalog), "command": command.strip()},
        )
        try:
            return await invoke_bridge_json(model=self.config.model, prompt=prompt)
        except Exception:
            return {"use_sequential_thinking": False, "reason": "Sequential Thinking 라우팅 실패", "signals": []}

    async def _think(self, command: str, soul: str, mcp_catalog: List[Dict[str, Any]], reason: str) -> Dict[str, Any]:
        """전략 옵션과 planner handoff brief를 생성한다."""
        template = get_prompt_content(
            "sequential_thinking_system",
            fallback=(
                "너는 JARVIS의 Sequential Thinking 보조 엔진이다.\n"
                "최종 플랜을 확정하지 말고, 전략과 리스크만 정리해 Deep Agent planner에 넘길 briefing을 만든다.\n\n"
                "반드시 JSON 객체 하나만 출력한다.\n"
                '형식: {"summary":"...","recommended_strategy":"...","options":[{"name":"...","approach":"...","tradeoffs":"..."}],"risks":["..."],"handoff_brief":"..."}\n'
                "규칙:\n"
                "- 최종 task 확정 금지\n"
                "- MCP 선택의 최종 책임은 Deep Agent planner에 있다\n"
                "- handoff_brief는 planner가 capability 기반으로 바로 plan을 만들 수 있게 구체적으로 쓴다\n"
                "- 한국어로 출력한다.\n\n"
                "[SOUL]\n{{soul}}\n\n"
                "[Sequential Thinking 필요 이유]\n{{reason}}\n\n"
                "[CAPABILITY REGISTRY]\n{{mcps}}\n\n"
                "[사용자 지령]\n{{command}}"
            ),
        )
        prompt = render_prompt_template(
            template,
            {"soul": soul, "reason": reason, "mcps": serialize_capabilities(mcp_catalog), "command": command.strip()},
        )
        try:
            return await invoke_bridge_json(model=self.config.model, prompt=prompt)
        except Exception:
            return {
                "summary": reason or "전략 정리를 생성하지 못했습니다.",
                "recommended_strategy": "현재 활성 capability를 기준으로 가장 직접적인 실행 경로를 우선합니다.",
                "options": [],
                "risks": [],
                "handoff_brief": command.strip(),
            }

    @staticmethod
    def _build_strategy(payload: Dict[str, Any], reason: str) -> RuntimeStrategy:
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
