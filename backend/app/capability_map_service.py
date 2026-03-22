from __future__ import annotations

"""MCP registry를 planner 친화적인 capability layer로 압축하는 서비스.

raw MCP 메타데이터를 planner가 그대로 읽지 않도록, 행동 수준 capability로
요약해 planner/ST/classifier 입력으로 넘기는 역할을 맡는다.
"""

from typing import Any, Dict, List


CAPABILITY_KEYWORDS = {
    "filesystem": ["filesystem", "파일", "폴더", "디렉터리", "경로", "read", "write"],
    "search_read": ["fetch", "docs", "문서", "조회", "검색", "참조"],
    "analyze_summarize": ["분석", "요약", "정리", "판단", "계획"],
    "notify_send": ["알림", "전송", "notify", "send"],
    "schedule_calendar": ["일정", "calendar", "schedule"],
    "code_execution": ["코드", "실행", "exec", "terminal", "browser", "검증"],
    "memory_context": ["memory", "맥락", "세션", "이력", "기억"],
    "external_api": ["github", "api", "외부", "원격", "협업"],
}


def infer_capability_labels(mcp: Dict[str, Any]) -> List[str]:
    """MCP 메타데이터를 기반으로 capability 라벨을 추론한다."""
    haystack = " ".join(
        [
            str(mcp.get("id", "")),
            str(mcp.get("name", "")),
            str(mcp.get("scope", "")),
            str(mcp.get("description", "")),
            " ".join(str(item) for item in mcp.get("capabilities", []) if str(item).strip()),
        ]
    ).lower()
    labels: List[str] = []
    for label, keywords in CAPABILITY_KEYWORDS.items():
        if any(keyword.lower() in haystack for keyword in keywords):
            labels.append(label)
    if not labels:
        labels.append("general_planning")
    return labels


def build_capability_map(mcp_catalog: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """raw MCP registry를 planner와 ST가 보기 좋은 capability map으로 변환한다."""
    capability_map: List[Dict[str, Any]] = []
    for item in mcp_catalog:
        capability_map.append(
            {
                "mcp_id": str(item.get("id", "")),
                "mcp_name": str(item.get("name", "")),
                "capability_labels": infer_capability_labels(item),
                "description": str(item.get("description", "")),
                "available": bool(item.get("enabled", True)),
                "risk_level": str(item.get("risk_level", "low")),
                "auth_required": bool(item.get("auth_required", False)),
                "transport": item.get("transport"),
                "expected_input": str(item.get("expected_input", "")),
                "expected_output": str(item.get("expected_output", "")),
                "tool_hints": [str(value) for value in item.get("capabilities", []) if str(value).strip()],
                "fallback_candidates": [],
            }
        )
    return capability_map
