from __future__ import annotations

"""Korean Law MCP 결과를 JARVIS 보고 형식으로 정리하는 skill layer.

법제처 Open API 기반 `korean-law-mcp` stdio 서버의 raw 응답을
evidence/findings/result_items 구조로 변환한다. 법률적 판단은 planner가 맡고,
이 파일은 검색/조회 결과를 사람이 읽기 쉬운 형태로 정리하는 최소 어댑터 역할에
집중한다.
"""

import json
from typing import Any, Dict, List


SUPPORTED_KOREAN_LAW_TOOLS = {
    "search_law",
    "get_law_text",
    "search_precedents",
    "get_precedent_text",
    "search_admin_rule",
    "get_admin_rule",
    "search_ordinance",
    "get_ordinance",
    "search_interpretations",
    "get_interpretation_text",
    "search_all",
}


def _truncate_lines(text: str, limit: int = 8) -> List[str]:
    """긴 결과 텍스트는 상위 몇 줄만 보고용 result item으로 남긴다."""
    return [line.strip() for line in text.splitlines()[:limit] if line.strip()]


async def execute_korean_law_task(
    task: Any,
    call_tool,
    extract_tool_text,
) -> Dict[str, Any]:
    """Korean Law MCP tool 결과를 evidence/findings/result_items로 정리한다."""
    tool_name = str(task.tool_name or "").strip()
    tool_arguments = task.tool_arguments or {}

    if tool_name not in SUPPORTED_KOREAN_LAW_TOOLS:
        return {
            "status": "unsupported",
            "evidence": [f"Korean Law MCP 미지원 tool: {tool_name or '없음'}"],
            "findings": [f"{task.title}: Korean Law MCP에서 아직 지원하지 않는 tool 입니다."],
            "result_items": [],
            "log": f"korean_law.{tool_name or 'unknown'} 미지원: {task.title}",
        }

    result = await call_tool(tool_name, tool_arguments)
    text = extract_tool_text(result).strip()
    preview_lines = _truncate_lines(text)

    if tool_name.startswith("search_"):
        findings = [f"{task.title}: 국가 법령 MCP로 검색을 수행했습니다."]
    else:
        findings = [f"{task.title}: 국가 법령 MCP로 본문 조회를 수행했습니다."]

    if preview_lines:
        result_items = preview_lines
    else:
        result_items = [json.dumps(result, ensure_ascii=False)]

    return {
        "status": "completed",
        "evidence": [f"Korean Law MCP {tool_name} 성공: {text or json.dumps(result, ensure_ascii=False)}"],
        "findings": findings,
        "result_items": result_items,
        "log": f"korean_law.{tool_name} 호출 완료: {task.title}",
    }
