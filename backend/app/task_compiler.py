from __future__ import annotations

"""Normalized plan을 실행 가능한 task로 변환하는 task compiler.

planner가 만든 추상 계획을 executor가 실제로 처리할 수 있는 task 목록으로
번역하는 경계 계층이다.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.plan_schema import NormalizedPlan

PLANNING_ONLY_MCP_IDS = {"planner"}


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
    if any(keyword in normalized for keyword in ["git", "커밋", "브랜치", "diff", "pr"]):
        add("github")
    if any(keyword in normalized for keyword in ["브라우저", "화면", "ui", "렌더링", "검증"]):
        add("browser")
    if any(keyword in normalized for keyword in ["문서", "레퍼런스", "규격", "api"]):
        add("docs")
        add("fetch")
    if not ids:
        add("filesystem")
    return ids


def compile_tasks(plan: NormalizedPlan, mcp_catalog: List[Dict[str, Any]]) -> List[CompiledTask]:
    """Normalized plan을 executable task 배열로 컴파일한다."""
    known_ids = {str(item.get("id")) for item in mcp_catalog}
    tasks: List[CompiledTask] = []
    for index, item in enumerate(plan.tasks_draft, start=1):
        mcp_ids = validate_mcp_ids(item.recommended_mcp_ids, mcp_catalog) or map_task_to_mcps(item.title, index - 1)
        selected_mcp_id = (
            item.selected_mcp_id
            if item.selected_mcp_id in known_ids and item.selected_mcp_id not in PLANNING_ONLY_MCP_IDS
            else None
        )
        if not selected_mcp_id and mcp_ids:
            selected_mcp_id = mcp_ids[0]
        tasks.append(
            CompiledTask(
                id=index,
                title=item.title.rstrip("."),
                mcp_ids=mcp_ids,
                selected_mcp_id=selected_mcp_id,
                tool_name=item.tool_name,
                tool_arguments=item.tool_arguments,
                expected_result=item.expected_result,
                approval_required=item.approval_required,
                dependencies=list(item.dependencies),
            )
        )
    return tasks
