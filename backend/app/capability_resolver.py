from __future__ import annotations

"""판단 메모 위에 MCP-aware capability resolution을 한 번 더 수행한다."""

import json
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field

from app.capability_router import choose_retrieval_mcp, derive_available_capabilities, split_capabilities
from app.capability_map_service import CAPABILITY_KEYWORDS, build_capability_map
from app.intent_router import IntentAdjudication
from app.llm_bridge import invoke_bridge_json
from app.prompt_store import get_prompt_content, render_prompt_template


ResolvedMode = Literal["mcp_action", "mcp_retrieval", "llm_fallback", "decline"]


class CapabilityProbe(BaseModel):
    """현재 MCP registry가 이 요청을 직접 해결할 수 있는지에 대한 보조 판단."""

    prefer_mcp: bool = False
    selected_mcp_id: str | None = None
    required_capabilities: List[str] = Field(default_factory=list)
    reason: str = ""


class CapabilityResolution(BaseModel):
    """판단 메모와 현재 capability 상태를 합쳐 만든 실행 계약."""

    mode: ResolvedMode
    reason: str = ""
    required_capabilities: List[str] = Field(default_factory=list)
    missing_capabilities: List[str] = Field(default_factory=list)
    selected_mcp_id: str | None = None
    safety: Dict[str, Any] = Field(default_factory=lambda: {"allowed": True, "policy_reason": None})


def _serialize_mcps(mcp_catalog: List[Dict[str, Any]]) -> str:
    rows: List[Dict[str, Any]] = []
    for item in mcp_catalog:
        rows.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "scope": item.get("scope"),
                "capabilities": item.get("capabilities"),
                "enabled": item.get("enabled", True),
            }
        )
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _fallback_probe(message: str, mcp_catalog: List[Dict[str, Any]]) -> CapabilityProbe:
    """LLM probe 실패 시 conservative fallback."""
    normalized = message.strip().lower()
    capability_map = build_capability_map(mcp_catalog)
    best_mcp_id = None
    best_label = None
    best_score = 0

    for item in capability_map:
        if not item.get("available"):
            continue
        for label in item.get("capability_labels", []):
            keywords = CAPABILITY_KEYWORDS.get(str(label), [])
            score = sum(1 for keyword in keywords if keyword.lower() in normalized)
            if score > best_score:
                best_score = score
                best_mcp_id = str(item.get("mcp_id") or "").strip() or None
                best_label = str(label)

    if best_mcp_id == "filesystem":
        return CapabilityProbe(
            prefer_mcp=True,
            selected_mcp_id="filesystem",
            required_capabilities=["filesystem.read"],
            reason="질문이 로컬 파일시스템 조회와 의미적으로 가장 가깝습니다.",
        )
    if best_mcp_id == "korean_law":
        return CapabilityProbe(
            prefer_mcp=True,
            selected_mcp_id="korean_law",
            required_capabilities=["law.lookup"],
            reason="질문이 법령/판례 조회와 의미적으로 가장 가깝습니다.",
        )
    return CapabilityProbe(
        prefer_mcp=False,
        selected_mcp_id=None,
        required_capabilities=[],
        reason=f"MCP-aware capability probe를 수행하지 못해 기본 fallback으로 처리합니다. best_label={best_label or 'none'}",
    )


async def probe_mcp_affinity(
    model: str,
    message: str,
    adjudication: IntentAdjudication,
    mcp_catalog: List[Dict[str, Any]],
) -> CapabilityProbe:
    """현재 MCP 중 이 질문을 더 직접적으로 해결할 수 있는 항목이 있는지 판단한다."""
    if adjudication.state_change_required or adjudication.task_nature == "action":
        return CapabilityProbe(
            prefer_mcp=True,
            selected_mcp_id=None,
            required_capabilities=adjudication.required_capabilities,
            reason=adjudication.reasoning,
        )

    template = get_prompt_content(
        "capability_resolver",
        fallback=(
            "너는 JARVIS의 capability resolver다.\n"
            "사용자 질문과 현재 MCP registry를 보고, 일반 LLM 답변보다 특정 MCP가 더 직접적으로 정답을 만들 수 있는지 판단한다.\n"
            "규칙:\n"
            "- 로컬 파일/폴더의 현재 상태를 직접 확인해야 하면 filesystem MCP를 우선한다.\n"
            "- 법령/조문/판례 조회는 korean_law MCP를 우선한다.\n"
            "- 일반 상식, 설명, 의견은 MCP보다 LLM을 우선한다.\n"
            "- 브라우저 자동화가 꼭 필요한 읽기 요청이 아니면 playwright를 함부로 선택하지 않는다.\n"
            "- 반드시 JSON 객체 하나만 출력한다.\n"
            '형식: {"prefer_mcp":true,"selected_mcp_id":"filesystem","required_capabilities":["filesystem.read"],"reason":"..."}\n'
            '또는 {"prefer_mcp":false,"selected_mcp_id":null,"required_capabilities":[],"reason":"..."}\n\n'
            "[예시]\n"
            "입력: 지금 내 다운로드 폴더에 하위 폴더는 몇개일까요?\n"
            '출력: {"prefer_mcp":true,"selected_mcp_id":"filesystem","required_capabilities":["filesystem.read"],"reason":"로컬 파일시스템의 현재 상태를 직접 조회해야 하므로 filesystem MCP가 더 직접적이다."}\n\n'
            "입력: 민법 제1조를 찾아서 알려줘라.\n"
            '출력: {"prefer_mcp":true,"selected_mcp_id":"korean_law","required_capabilities":["law.lookup"],"reason":"법령 본문은 korean_law MCP로 직접 조회하는 것이 가장 적절하다."}\n\n'
            "입력: 미국 대통령이 누구인가?\n"
            '출력: {"prefer_mcp":false,"selected_mcp_id":null,"required_capabilities":[],"reason":"현재 연결된 MCP보다 일반 LLM 답변이 더 적절하다."}\n\n'
            "[현재 adjudication]\n{{adjudication}}\n\n"
            "[현재 MCP REGISTRY]\n{{mcps}}\n\n"
            "[사용자 입력]\n{{message}}"
        ),
    )
    prompt = render_prompt_template(
        template,
        {
            "adjudication": json.dumps(adjudication.model_dump(), ensure_ascii=False, indent=2),
            "mcps": _serialize_mcps(mcp_catalog),
            "message": message.strip(),
        },
    )
    try:
        payload = await invoke_bridge_json(model=model, prompt=prompt)
        return CapabilityProbe(**payload)
    except Exception:
        return _fallback_probe(message, mcp_catalog)


async def resolve_capability(
    *,
    model: str,
    message: str,
    adjudication: IntentAdjudication,
    mcp_catalog: List[Dict[str, Any]],
) -> CapabilityResolution:
    """구조화된 판단 메모를 실제 실행 가능한 계약으로 해석한다."""
    available = derive_available_capabilities(mcp_catalog)
    probe = await probe_mcp_affinity(model=model, message=message, adjudication=adjudication, mcp_catalog=mcp_catalog)
    requested_capabilities = probe.required_capabilities or adjudication.required_capabilities
    capability_split = split_capabilities(requested_capabilities, available)
    missing = capability_split["missing"]
    selected_mcp_id = probe.selected_mcp_id or choose_retrieval_mcp(requested_capabilities, mcp_catalog)

    if not adjudication.safety_allowed:
        return CapabilityResolution(
            mode="decline",
            reason=adjudication.safety_reason or adjudication.reasoning or "정책상 처리할 수 없습니다.",
            required_capabilities=requested_capabilities,
            missing_capabilities=missing,
            safety={"allowed": False, "policy_reason": adjudication.safety_reason},
        )

    if adjudication.preferred_handler == "llm":
        return CapabilityResolution(
            mode="llm_fallback",
            reason=adjudication.reasoning or "사용자가 LLM 직접 답변을 명시적으로 요청했습니다.",
            required_capabilities=[],
            missing_capabilities=[],
            selected_mcp_id=None,
            safety={"allowed": True, "policy_reason": None},
        )

    if adjudication.state_change_required or adjudication.task_nature == "action":
        return CapabilityResolution(
            mode="mcp_action",
            reason=adjudication.reasoning or "실행형 요청으로 판단했습니다.",
            required_capabilities=requested_capabilities,
            missing_capabilities=missing,
            safety={"allowed": True, "policy_reason": None},
        )

    if (probe.prefer_mcp or (adjudication.task_nature == "retrieval" and selected_mcp_id is not None)) and selected_mcp_id and not missing:
        return CapabilityResolution(
            mode="mcp_retrieval",
            reason=probe.reason or adjudication.reasoning or "MCP가 더 직접적으로 답변할 수 있습니다.",
            required_capabilities=requested_capabilities,
            missing_capabilities=[],
            selected_mcp_id=selected_mcp_id,
            safety={"allowed": True, "policy_reason": None},
        )

    if adjudication.llm_answer_sufficient:
        return CapabilityResolution(
            mode="llm_fallback",
            reason=probe.reason or adjudication.reasoning or "LLM 직접 답변으로 충분합니다.",
            required_capabilities=requested_capabilities,
            missing_capabilities=missing,
            selected_mcp_id=selected_mcp_id,
            safety={"allowed": True, "policy_reason": None},
        )

    return CapabilityResolution(
        mode="decline",
        reason=probe.reason or adjudication.reasoning or "현재 capability 범위에서 처리 계약을 만족하기 어렵습니다.",
        required_capabilities=requested_capabilities,
        missing_capabilities=missing,
        selected_mcp_id=selected_mcp_id,
        safety={"allowed": True, "policy_reason": None},
    )
