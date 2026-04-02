from __future__ import annotations

"""판단축(adjudication)을 생성하는 최상위 해석 계층."""

import json
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.capability_router import CAPABILITY_ALIASES, derive_available_capabilities
from app.llm_bridge import invoke_bridge_json
from app.prompt_store import get_prompt_content, render_prompt_template


TaskNature = Literal["informational", "retrieval", "action", "unsupported"]
StakesLevel = Literal["low", "medium", "high"]
PreferredHandler = Literal["auto", "llm", "mcp"]


class IntentAdjudication(BaseModel):
    """라우팅 전에 먼저 만드는 판단 메모."""

    user_goal: str = ""
    preferred_handler: PreferredHandler = "auto"
    task_nature: TaskNature = "informational"
    requires_live_verification: bool = False
    stakes: StakesLevel = "low"
    llm_answer_sufficient: bool = True
    tool_answer_preferred: bool = False
    tool_answer_required: bool = False
    state_change_required: bool = False
    approval_required: bool = False
    required_capabilities: List[str] = Field(default_factory=list)
    reasoning: str = ""
    safety_allowed: bool = True
    safety_reason: Optional[str] = None


def _format_conversation(conversation: List[Dict[str, str]]) -> str:
    if not conversation:
        return "(대화 히스토리 없음)"
    return "\n".join(
        f"- {str(item.get('role', 'user')).upper()}: {str(item.get('content', '')).strip()}"
        for item in conversation[-10:]
        if str(item.get("content", "")).strip()
    )


def _serialize_mcps(mcp_catalog: List[Dict[str, Any]]) -> str:
    simplified: List[Dict[str, Any]] = []
    for item in mcp_catalog:
        simplified.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "scope": item.get("scope"),
                "capabilities": item.get("capabilities"),
                "derived_capabilities": sorted(CAPABILITY_ALIASES.get(str(item.get("id") or "").strip(), set())),
                "enabled": item.get("enabled", True),
            }
        )
    return json.dumps(simplified, ensure_ascii=False, indent=2)


def _fallback_adjudication(message: str) -> IntentAdjudication:
    """LLM 라우팅 실패 시 최소 휴리스틱으로 판단 메모를 만든다."""
    normalized = message.strip().lower()
    if "llm" in normalized or "모델" in normalized:
        return IntentAdjudication(
            user_goal=message.strip(),
            preferred_handler="llm",
            task_nature="informational",
            requires_live_verification=False,
            stakes="low",
            llm_answer_sufficient=True,
            tool_answer_preferred=False,
            tool_answer_required=False,
            state_change_required=False,
            approval_required=False,
            required_capabilities=[],
            reasoning="사용자가 LLM 직접 답변을 명시적으로 요청했습니다.",
        )

    action_markers = ["수정", "구현", "추가", "삭제", "작성", "만들", "고쳐", "실행", "열어", "띄워", "클릭", "이동"]
    law_markers = ["법", "법령", "조문", "판례", "시행령", "행정규칙", "자치법규", "법령해석"]
    weather_markers = ["날씨", "기온", "강수", "미세먼지"]
    finance_markers = ["환율", "주가", "코스피", "코스닥", "금리", "cpi", "물가"]
    freshness_markers = ["오늘", "지금", "현재", "실시간", "최신", "방금", "기준", "확인"]

    if any(marker in normalized for marker in action_markers):
        return IntentAdjudication(
            user_goal=message.strip(),
            preferred_handler="auto",
            task_nature="action",
            llm_answer_sufficient=False,
            tool_answer_preferred=True,
            tool_answer_required=True,
            state_change_required=True,
            approval_required=True,
            reasoning="실제 시스템 변경 또는 도구 실행이 필요한 요청으로 보입니다.",
        )

    if any(marker in normalized for marker in law_markers):
        return IntentAdjudication(
            user_goal=message.strip(),
            preferred_handler="auto",
            task_nature="retrieval",
            requires_live_verification=False,
            stakes="medium",
            llm_answer_sufficient=False,
            tool_answer_preferred=True,
            tool_answer_required=True,
            required_capabilities=["law.lookup"],
            reasoning="법령/판례와 같이 전용 조회 근거가 더 적절한 질문으로 보입니다.",
        )

    if any(marker in normalized for marker in weather_markers):
        return IntentAdjudication(
            user_goal=message.strip(),
            preferred_handler="auto",
            task_nature="retrieval",
            requires_live_verification=True,
            stakes="medium",
            llm_answer_sufficient=False,
            tool_answer_preferred=True,
            tool_answer_required=True,
            required_capabilities=["weather.read"],
            reasoning="실시간 날씨 조회가 필요한 질문으로 보입니다.",
        )

    if any(marker in normalized for marker in finance_markers):
        return IntentAdjudication(
            user_goal=message.strip(),
            preferred_handler="auto",
            task_nature="retrieval",
            requires_live_verification=True,
            stakes="medium",
            llm_answer_sufficient=False,
            tool_answer_preferred=True,
            tool_answer_required=True,
            required_capabilities=["finance.read"],
            reasoning="최신 금융/경제 조회가 필요한 질문으로 보입니다.",
        )

    return IntentAdjudication(
        user_goal=message.strip(),
        preferred_handler="llm" if ("llm" in normalized or "모델" in normalized) else "auto",
        task_nature="informational",
        requires_live_verification=any(marker in normalized for marker in freshness_markers),
        stakes="low",
        llm_answer_sufficient=True,
        tool_answer_preferred=False,
        tool_answer_required=False,
        reasoning="일반 설명 또는 상식형 질문으로 보입니다.",
    )


async def adjudicate_intent(
    model: str,
    message: str,
    mcp_catalog: List[Dict[str, Any]],
    conversation: Optional[List[Dict[str, str]]] = None,
) -> IntentAdjudication:
    """LLM으로 판단 메모를 만들고 실패 시 fallback한다."""
    available = derive_available_capabilities(mcp_catalog)
    template = get_prompt_content(
        "intent_adjudicator",
        fallback=(
            "너는 JARVIS의 intent adjudicator다.\n"
            "바로 route를 정하지 말고, 아래 판단축을 구조화해 JSON으로만 출력한다.\n"
            "판단축:\n"
            "- user_goal: 사용자가 실제로 얻고 싶은 결과를 한 문장으로 요약\n"
            "- preferred_handler: auto | llm | mcp (사용자가 처리 수단을 명시했다면 반영)\n"
            "- task_nature: informational | retrieval | action | unsupported\n"
            "- requires_live_verification: 최신 검증이 실질적으로 필요한지\n"
            "- stakes: low | medium | high\n"
            "- llm_answer_sufficient: LLM 단독 답변이 충분히 좋은지\n"
            "- tool_answer_preferred: 도구 답변이 더 낫지만 필수는 아닌지\n"
            "- tool_answer_required: 도구 없이는 답 품질이 부족한지\n"
            "- state_change_required: 파일/브라우저/프로세스/외부 상태 변경이 필요한지\n"
            "- approval_required: 사용자 승인 단계가 필요한지\n"
            "- required_capabilities: 필요한 capability 배열\n"
            "- reasoning: 위 판단의 근거를 짧게 설명\n"
            "- safety_allowed: 정책상 허용 여부\n"
            "- safety_reason: 금지라면 이유\n\n"
            "중요 규칙:\n"
            "- 사용자가 'LLM한테 물어봐라', 'LLM으로 답해라'처럼 처리 수단을 명시하면 preferred_handler를 llm 으로 둔다.\n"
            "- 사용자가 MCP/도구 사용을 명시하면 preferred_handler를 mcp 로 둘 수 있다.\n"
            "- 명령형 말투라도 상식형 질문이면 llm_answer_sufficient=true 가능하다.\n"
            "- 최신성이 잠재적으로 중요하다는 이유만으로 tool_answer_required=true 로 두지 말라.\n"
            "- 사용자가 명시적으로 최신 확인을 요구하거나, 틀리면 곤란한 조회면 requires_live_verification 또는 tool_answer_required를 높인다.\n"
            "- 실제 변경/실행은 task_nature=action, state_change_required=true 이다.\n"
            "- 로컬 파일/폴더의 현재 상태를 직접 확인해야 하는 요청은 retrieval 이며, filesystem MCP가 있으면 required_capabilities에 filesystem.read 를 넣는다.\n"
            "- 법령/판례/조문 조회는 retrieval 이며, korean_law MCP가 있으면 required_capabilities에 law.lookup 을 넣는다.\n"
            "- capability가 없다는 사실 자체로 판단을 왜곡하지 말고, 먼저 ideal handling 기준으로 adjudication 하라.\n"
            "- 반드시 JSON 객체 하나만 출력한다.\n\n"
            "[예시]\n"
            "입력: 대한민국은 어떤 나라인가?\n"
            '출력: {"user_goal":"대한민국의 성격을 설명받고 싶다","preferred_handler":"auto","task_nature":"informational","requires_live_verification":false,"stakes":"low","llm_answer_sufficient":true,"tool_answer_preferred":false,"tool_answer_required":false,"state_change_required":false,"approval_required":false,"required_capabilities":[],"reasoning":"일반 설명형 질문이다.","safety_allowed":true,"safety_reason":null}\n\n'
            "입력: 지금 내 다운로드 폴더에 하위 폴더는 몇개일까요?\n"
            '출력: {"user_goal":"다운로드 폴더의 현재 하위 폴더 개수를 알고 싶다","preferred_handler":"auto","task_nature":"retrieval","requires_live_verification":false,"stakes":"low","llm_answer_sufficient":false,"tool_answer_preferred":true,"tool_answer_required":true,"state_change_required":false,"approval_required":false,"required_capabilities":["filesystem.read"],"reasoning":"현재 로컬 파일시스템 상태를 직접 조회해야 하는 질문이다.","safety_allowed":true,"safety_reason":null}\n\n'
            "입력: 민법 제1조를 찾아서 알려줘라.\n"
            '출력: {"user_goal":"민법 제1조 내용을 조회하고 싶다","preferred_handler":"auto","task_nature":"retrieval","requires_live_verification":false,"stakes":"medium","llm_answer_sufficient":false,"tool_answer_preferred":true,"tool_answer_required":true,"state_change_required":false,"approval_required":false,"required_capabilities":["law.lookup"],"reasoning":"법령 본문은 전용 조회가 더 적절하다.","safety_allowed":true,"safety_reason":null}\n\n'
            "입력: 오늘 서울의 오후 3시 날씨는 어떤지 LLM한테 물어봐라\n"
            '출력: {"user_goal":"오늘 서울 오후 3시 날씨를 알고 싶다","preferred_handler":"llm","task_nature":"retrieval","requires_live_verification":true,"stakes":"low","llm_answer_sufficient":true,"tool_answer_preferred":false,"tool_answer_required":false,"state_change_required":false,"approval_required":false,"required_capabilities":[],"reasoning":"사용자가 LLM 답변을 명시적으로 요구했다.","safety_allowed":true,"safety_reason":null}\n\n'
            "입력: 로그인 화면 문구를 수정해라.\n"
            '출력: {"user_goal":"로그인 화면 문구를 변경하고 싶다","preferred_handler":"auto","task_nature":"action","requires_live_verification":false,"stakes":"medium","llm_answer_sufficient":false,"tool_answer_preferred":true,"tool_answer_required":true,"state_change_required":true,"approval_required":true,"required_capabilities":["code.modify"],"reasoning":"실제 상태 변경이 필요한 작업이다.","safety_allowed":true,"safety_reason":null}\n\n'
            "[사용 가능한 derived capabilities]\n{{available_capabilities}}\n\n"
            "[현재 MCP REGISTRY]\n{{mcps}}\n\n"
            "[대화 히스토리]\n{{conversation}}\n\n"
            "[사용자 입력]\n{{message}}"
        ),
    )
    prompt = render_prompt_template(
        template,
        {
            "available_capabilities": ", ".join(sorted(available)) or "(없음)",
            "mcps": _serialize_mcps(mcp_catalog),
            "conversation": _format_conversation(conversation or []),
            "message": message.strip(),
        },
    )
    try:
        raw = await invoke_bridge_json(model=model, prompt=prompt)
        result = IntentAdjudication(**raw)
        normalized = message.strip().lower()
        if "llm" in normalized or "모델" in normalized:
            result.preferred_handler = "llm"
            result.llm_answer_sufficient = True
            result.tool_answer_preferred = False
            result.tool_answer_required = False
            result.required_capabilities = []
            result.reasoning = "사용자가 LLM 직접 답변을 명시적으로 요청했습니다."
        return result
    except Exception:
        return _fallback_adjudication(message)
