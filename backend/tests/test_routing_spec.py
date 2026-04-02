from __future__ import annotations

import asyncio

from app.capability_resolver import resolve_capability
from app.intent_router import adjudicate_intent


def sample_mcp_catalog() -> list[dict]:
    return [
        {
            "id": "filesystem",
            "name": "Filesystem MCP",
            "scope": "파일",
            "description": "로컬 파일시스템 조회 및 수정",
            "capabilities": ["파일 탐색", "코드 수정", "산출물 생성"],
            "enabled": True,
        },
        {
            "id": "korean_law",
            "name": "Korean Law MCP",
            "scope": "법령",
            "description": "법령, 조문, 판례 조회",
            "capabilities": ["법령 검색", "조문 조회", "판례 검색"],
            "enabled": True,
        },
        {
            "id": "playwright",
            "name": "Playwright MCP",
            "scope": "브라우저",
            "description": "브라우저 자동화",
            "capabilities": ["브라우저 자동화", "클릭/입력", "스냅샷/스크린샷"],
            "enabled": True,
        },
    ]


def run(coro):
    return asyncio.run(coro)


def test_informational_question_falls_back_to_llm(monkeypatch):
    async def fake_invoke_bridge_json(*args, **kwargs):
        return {
            "user_goal": "대한민국의 성격을 설명받고 싶다",
            "preferred_handler": "auto",
            "task_nature": "informational",
            "requires_live_verification": False,
            "stakes": "low",
            "llm_answer_sufficient": True,
            "tool_answer_preferred": False,
            "tool_answer_required": False,
            "state_change_required": False,
            "approval_required": False,
            "required_capabilities": [],
            "reasoning": "일반 설명형 질문이다.",
            "safety_allowed": True,
            "safety_reason": None,
        }

    monkeypatch.setattr("app.intent_router.invoke_bridge_json", fake_invoke_bridge_json)

    adjudication = run(adjudicate_intent("default", "대한민국은 어떤 나라인가?", sample_mcp_catalog(), []))
    resolution = run(resolve_capability(model="default", message="대한민국은 어떤 나라인가?", adjudication=adjudication, mcp_catalog=sample_mcp_catalog()))

    assert adjudication.task_nature == "informational"
    assert resolution.mode == "llm_fallback"


def test_common_knowledge_current_person_question_should_not_decline():
    # bridge failure fallback 경로에서도 상식형 질문은 LLM fallback 이어야 한다.
    adjudication = run(adjudicate_intent("default", "미국 대통령이 누구인가?", sample_mcp_catalog(), []))
    resolution = run(resolve_capability(model="default", message="미국 대통령이 누구인가?", adjudication=adjudication, mcp_catalog=sample_mcp_catalog()))

    assert resolution.mode == "llm_fallback"


def test_law_lookup_should_use_korean_law_mcp(monkeypatch):
    async def fake_invoke_bridge_json(*args, **kwargs):
        return {
            "user_goal": "민법 제1조 내용을 조회하고 싶다",
            "preferred_handler": "auto",
            "task_nature": "retrieval",
            "requires_live_verification": False,
            "stakes": "medium",
            "llm_answer_sufficient": False,
            "tool_answer_preferred": True,
            "tool_answer_required": True,
            "state_change_required": False,
            "approval_required": False,
            "required_capabilities": ["law.lookup"],
            "reasoning": "법령 본문은 전용 조회가 더 적절하다.",
            "safety_allowed": True,
            "safety_reason": None,
        }

    monkeypatch.setattr("app.intent_router.invoke_bridge_json", fake_invoke_bridge_json)
    monkeypatch.setattr("app.capability_resolver.invoke_bridge_json", fake_invoke_bridge_json)

    adjudication = run(adjudicate_intent("default", "민법 제1조를 찾아서 알려줘라.", sample_mcp_catalog(), []))
    resolution = run(resolve_capability(model="default", message="민법 제1조를 찾아서 알려줘라.", adjudication=adjudication, mcp_catalog=sample_mcp_catalog()))

    assert adjudication.task_nature == "retrieval"
    assert "law.lookup" in resolution.required_capabilities
    assert resolution.mode == "mcp_retrieval"
    assert resolution.selected_mcp_id == "korean_law"


def test_local_filesystem_directory_count_should_use_filesystem_mcp(monkeypatch):
    async def fake_adjudicator(*args, **kwargs):
        # adjudicator가 놓쳐도 resolver가 MCP-aware resolution으로 filesystem을 올려야 한다.
        return {
            "user_goal": "다운로드 폴더 하위 폴더 개수를 알고 싶다",
            "preferred_handler": "auto",
            "task_nature": "informational",
            "requires_live_verification": False,
            "stakes": "low",
            "llm_answer_sufficient": True,
            "tool_answer_preferred": False,
            "tool_answer_required": False,
            "state_change_required": False,
            "approval_required": False,
            "required_capabilities": [],
            "reasoning": "질문형 요청이다.",
            "safety_allowed": True,
            "safety_reason": None,
        }

    async def fake_resolver_probe(*args, **kwargs):
        return {
            "prefer_mcp": True,
            "selected_mcp_id": "filesystem",
            "required_capabilities": ["filesystem.read"],
            "reason": "로컬 파일시스템 상태를 직접 조회해야 한다.",
        }

    monkeypatch.setattr("app.intent_router.invoke_bridge_json", fake_adjudicator)
    monkeypatch.setattr("app.capability_resolver.invoke_bridge_json", fake_resolver_probe)

    question = "지금 내 다운로드 폴더에 하위 폴더는 몇개일까요?"
    adjudication = run(adjudicate_intent("default", question, sample_mcp_catalog(), []))
    resolution = run(resolve_capability(model="default", message=question, adjudication=adjudication, mcp_catalog=sample_mcp_catalog()))

    assert resolution.mode == "mcp_retrieval"
    assert resolution.selected_mcp_id == "filesystem"
    assert "filesystem.read" in resolution.required_capabilities


def test_local_filesystem_file_count_should_use_filesystem_mcp(monkeypatch):
    async def fake_adjudicator(*args, **kwargs):
        return {
            "user_goal": "다운로드 폴더 파일 개수를 알고 싶다",
            "preferred_handler": "auto",
            "task_nature": "informational",
            "requires_live_verification": False,
            "stakes": "low",
            "llm_answer_sufficient": True,
            "tool_answer_preferred": False,
            "tool_answer_required": False,
            "state_change_required": False,
            "approval_required": False,
            "required_capabilities": [],
            "reasoning": "질문형 요청이다.",
            "safety_allowed": True,
            "safety_reason": None,
        }

    async def fake_resolver_probe(*args, **kwargs):
        return {
            "prefer_mcp": True,
            "selected_mcp_id": "filesystem",
            "required_capabilities": ["filesystem.read"],
            "reason": "로컬 파일시스템 파일 개수는 filesystem MCP가 직접 조회해야 한다.",
        }

    monkeypatch.setattr("app.intent_router.invoke_bridge_json", fake_adjudicator)
    monkeypatch.setattr("app.capability_resolver.invoke_bridge_json", fake_resolver_probe)

    question = "내 다운로드 폴더에 있는 파일이 총 몇개인지 보고하라"
    adjudication = run(adjudicate_intent("default", question, sample_mcp_catalog(), []))
    resolution = run(resolve_capability(model="default", message=question, adjudication=adjudication, mcp_catalog=sample_mcp_catalog()))

    assert resolution.mode == "mcp_retrieval"
    assert resolution.selected_mcp_id == "filesystem"


def test_user_explicitly_requests_llm_handler():
    question = "오늘 서울의 오후 3시 날씨는 어떤지 LLM한테 물어봐라"

    adjudication = run(adjudicate_intent("default", question, sample_mcp_catalog(), []))
    resolution = run(resolve_capability(model="default", message=question, adjudication=adjudication, mcp_catalog=sample_mcp_catalog()))

    assert adjudication.preferred_handler == "llm"
    assert resolution.mode == "llm_fallback"


def test_weather_without_capability_may_decline(monkeypatch):
    async def fake_invoke_bridge_json(*args, **kwargs):
        return {
            "user_goal": "서울 날씨를 알고 싶다",
            "preferred_handler": "auto",
            "task_nature": "retrieval",
            "requires_live_verification": True,
            "stakes": "medium",
            "llm_answer_sufficient": False,
            "tool_answer_preferred": True,
            "tool_answer_required": True,
            "state_change_required": False,
            "approval_required": False,
            "required_capabilities": ["weather.read"],
            "reasoning": "실시간 날씨 조회가 필요하다.",
            "safety_allowed": True,
            "safety_reason": None,
        }

    monkeypatch.setattr("app.intent_router.invoke_bridge_json", fake_invoke_bridge_json)
    monkeypatch.setattr("app.capability_resolver.invoke_bridge_json", fake_invoke_bridge_json)

    adjudication = run(adjudicate_intent("default", "서울 날씨를 알려줘라", sample_mcp_catalog(), []))
    resolution = run(resolve_capability(model="default", message="서울 날씨를 알려줘라", adjudication=adjudication, mcp_catalog=sample_mcp_catalog()))

    assert resolution.mode == "decline"
    assert "weather.read" in resolution.required_capabilities


def test_real_modification_request_should_go_to_action_path(monkeypatch):
    async def fake_invoke_bridge_json(*args, **kwargs):
        return {
            "user_goal": "로그인 화면 문구를 변경하고 싶다",
            "preferred_handler": "auto",
            "task_nature": "action",
            "requires_live_verification": False,
            "stakes": "medium",
            "llm_answer_sufficient": False,
            "tool_answer_preferred": True,
            "tool_answer_required": True,
            "state_change_required": True,
            "approval_required": True,
            "required_capabilities": ["code.modify"],
            "reasoning": "실제 상태 변경이 필요한 작업이다.",
            "safety_allowed": True,
            "safety_reason": None,
        }

    monkeypatch.setattr("app.intent_router.invoke_bridge_json", fake_invoke_bridge_json)

    adjudication = run(adjudicate_intent("default", "로그인 화면 문구를 수정해라", sample_mcp_catalog(), []))
    resolution = run(resolve_capability(model="default", message="로그인 화면 문구를 수정해라", adjudication=adjudication, mcp_catalog=sample_mcp_catalog()))

    assert adjudication.task_nature == "action"
    assert resolution.mode == "mcp_action"
