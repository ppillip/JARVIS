from __future__ import annotations

"""요청 capability와 MCP registry를 연결하는 경량 라우팅 계층."""

from typing import Any, Dict, List, Set


CAPABILITY_ALIASES: Dict[str, Set[str]] = {
    "filesystem": {"filesystem.read", "filesystem.write", "code.modify"},
    "playwright": {"browser.read", "browser.action", "browser.navigate"},
    "korean_law": {"law.lookup"},
}


def derive_available_capabilities(mcp_catalog: List[Dict[str, Any]]) -> Set[str]:
    """현재 활성 MCP 목록으로부터 처리 가능한 capability 집합을 만든다."""
    available: Set[str] = set()
    for item in mcp_catalog:
        if not bool(item.get("enabled", True)):
            continue
        available.update(CAPABILITY_ALIASES.get(str(item.get("id") or "").strip(), set()))
    return available


def split_capabilities(required_capabilities: List[str], available_capabilities: Set[str]) -> Dict[str, List[str]]:
    """필요 capability를 충족/미충족 집합으로 분리한다."""
    required = [item for item in required_capabilities if item]
    missing = [item for item in required if item not in available_capabilities]
    satisfied = [item for item in required if item in available_capabilities]
    return {"required": required, "missing": missing, "satisfied": satisfied}


def choose_retrieval_mcp(required_capabilities: List[str], mcp_catalog: List[Dict[str, Any]]) -> str | None:
    """조회형 질문에 가장 적합한 MCP id 하나를 고른다."""
    required = set(required_capabilities)
    if "filesystem.read" in required:
        for item in mcp_catalog:
            if bool(item.get("enabled", True)) and str(item.get("id")) == "filesystem":
                return "filesystem"
    if "law.lookup" in required:
        for item in mcp_catalog:
            if bool(item.get("enabled", True)) and str(item.get("id")) == "korean_law":
                return "korean_law"
    return None
