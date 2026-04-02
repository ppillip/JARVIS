from __future__ import annotations

"""Normalized plan을 실행 가능한 task로 변환하는 task compiler.

planner가 만든 추상 계획을 executor가 실제로 처리할 수 있는 task 목록으로
번역하는 경계 계층이다.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.plan_schema import NormalizedPlan

PLANNING_ONLY_MCP_IDS = {"planner"}

PLAYWRIGHT_TOOL_ALIASES = {
    "open_page": "open",
    "goto": "open",
    "navigate": "open",
    "take_snapshot": "snapshot",
    "capture_snapshot": "snapshot",
    "take_screenshot": "screenshot",
    "capture_screenshot": "screenshot",
    "type": "fill",
    "input_text": "fill",
    "tap": "click",
}


def infer_playwright_tool_name(
    title: str,
    expected_result: str,
    tool_arguments: Dict[str, Any],
) -> Optional[str]:
    """Playwright task에 tool_name이 비어 있을 때 일반적인 브라우저 의도로 추정한다."""
    normalized = f"{title} {expected_result}".lower()

    if str(tool_arguments.get("url", "")).strip() or any(
        keyword in normalized for keyword in ["열기", "open", "접속", "이동", "navigate", "goto"]
    ):
        return "open"
    if str(tool_arguments.get("path", "")).strip() or any(
        keyword in normalized for keyword in ["스크린샷", "screenshot", "캡처"]
    ):
        return "screenshot"
    if str(tool_arguments.get("key", "")).strip() or any(
        keyword in normalized for keyword in ["press", "키 입력", "엔터", "esc", "tab"]
    ):
        return "press"
    if any(str(tool_arguments.get(key, "")).strip() for key in {"text", "label"}) and any(
        keyword in normalized for keyword in ["입력", "채워", "fill", "type"]
    ):
        return "fill"
    if any(keyword in normalized for keyword in ["텍스트", "읽", "문구", "본문", "제목", "타이틀"]):
        return "read_text"
    if any(keyword in normalized for keyword in ["클릭", "눌러", "버튼", "링크", "link"]):
        return "click"
    if any(keyword in normalized for keyword in ["스냅샷", "snapshot", "구조", "요소", "ref"]):
        return "snapshot"
    return None


class CompiledTask(BaseModel):
    """Executor가 바로 사용할 수 있는 compiled task."""

    id: int
    title: str
    mcp_ids: List[str] = Field(default_factory=list)
    selected_mcp_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_arguments: Dict[str, Any] = Field(default_factory=dict)
    expected_result: str = ""
    approval_required: bool = False
    dependencies: List[int] = Field(default_factory=list)


def normalize_task_tool(selected_mcp_id: Optional[str], tool_name: Optional[str], tool_arguments: Dict[str, Any]) -> tuple[Optional[str], Dict[str, Any]]:
    """planner가 반환한 tool 이름과 인자를 executor 계약에 맞게 정규화한다."""
    normalized_tool_name = tool_name
    normalized_tool_arguments = dict(tool_arguments)

    if selected_mcp_id == "playwright" and normalized_tool_name:
        normalized_tool_name = PLAYWRIGHT_TOOL_ALIASES.get(normalized_tool_name, normalized_tool_name)
        if "headless" in normalized_tool_arguments and "headed" not in normalized_tool_arguments:
            normalized_tool_arguments["headed"] = not bool(normalized_tool_arguments.get("headless"))

    return normalized_tool_name, normalized_tool_arguments


def validate_mcp_ids(ids: List[str], mcp_catalog: List[Dict[str, Any]]) -> List[str]:
    """catalog에 존재하는 MCP id만 남긴다."""
    known_ids = {str(item.get("id")) for item in mcp_catalog}
    return [mcp_id for mcp_id in ids if mcp_id in known_ids and mcp_id not in PLANNING_ONLY_MCP_IDS]


def map_task_to_mcps(task_title: str, index: int) -> List[str]:
    """구조화 정보가 없을 때 제목 기반으로 기본 MCP 후보를 추정한다."""
    normalized = task_title.lower()
    ids: List[str] = []

    def add(mcp_id: str) -> None:
        if mcp_id not in ids:
            ids.append(mcp_id)

    if any(keyword in normalized for keyword in ["파일", "폴더", "디렉터리", "코드", "프로젝트", "경로"]):
        add("filesystem")
    if any(keyword in normalized for keyword in ["브라우저", "화면", "ui", "렌더링", "검증", "클릭", "입력", "페이지", "스크린샷"]):
        add("playwright")
    if any(keyword in normalized for keyword in ["법령", "판례", "조문", "법률", "시행령", "시행규칙", "행정규칙", "자치법규", "법제처", "해석", "대법원", "헌법재판소"]):
        add("korean_law")
    if not ids:
        add("filesystem")
    return ids


def compile_tasks(plan: NormalizedPlan, mcp_catalog: List[Dict[str, Any]]) -> List[CompiledTask]:
    """Normalized plan을 executable task 배열로 컴파일한다."""
    known_ids = {str(item.get("id")) for item in mcp_catalog}
    tasks: List[CompiledTask] = []
    next_id = 1
    for index, item in enumerate(plan.tasks_draft, start=1):
        mcp_ids = validate_mcp_ids(item.recommended_mcp_ids, mcp_catalog) or map_task_to_mcps(item.title, index - 1)
        selected_mcp_id = (
            item.selected_mcp_id
            if item.selected_mcp_id in known_ids and item.selected_mcp_id not in PLANNING_ONLY_MCP_IDS
            else None
        )
        if not selected_mcp_id and mcp_ids:
            selected_mcp_id = mcp_ids[0]
        normalized_tool_name, normalized_tool_arguments = normalize_task_tool(
            selected_mcp_id,
            item.tool_name,
            item.tool_arguments,
        )
        if selected_mcp_id == "playwright" and not normalized_tool_name:
            normalized_tool_name = infer_playwright_tool_name(
                item.title,
                item.expected_result,
                normalized_tool_arguments,
            )

        if selected_mcp_id == "playwright" and normalized_tool_name == "fill":
            fields = normalized_tool_arguments.get("fields")
            if isinstance(fields, list) and fields:
                for field in fields:
                    if not isinstance(field, dict):
                        continue
                    target = str(field.get("target", "") or field.get("label", "")).strip()
                    value = str(field.get("value", "")).strip()
                    tasks.append(
                        CompiledTask(
                            id=next_id,
                            title=f"{item.title.rstrip('.')} - {target or '입력'}",
                            mcp_ids=mcp_ids,
                            selected_mcp_id=selected_mcp_id,
                            tool_name="fill",
                            tool_arguments={"target": target, "value": value},
                            expected_result=item.expected_result,
                            approval_required=item.approval_required,
                            dependencies=list(item.dependencies),
                        )
                    )
                    next_id += 1
                continue

        tasks.append(
            CompiledTask(
                id=next_id,
                title=item.title.rstrip("."),
                mcp_ids=mcp_ids,
                selected_mcp_id=selected_mcp_id,
                tool_name=normalized_tool_name,
                tool_arguments=normalized_tool_arguments,
                expected_result=item.expected_result,
                approval_required=item.approval_required,
                dependencies=list(item.dependencies),
            )
        )
        next_id += 1
    return tasks
