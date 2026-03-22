from __future__ import annotations

"""Planner와 executor 사이에서 공통으로 쓰는 normalized plan schema.

planner 결과를 UI와 compiler, executor가 안정적으로 공유할 수 있게 하는
표준 데이터 모델을 정의한다.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.agent_runtime import RuntimeStrategy


class PlannerMetadata(BaseModel):
    """플랜 생성 경로를 기록하는 planner 메타데이터."""

    planner_type: str = "unknown"
    planner_version: str = "v1"
    fallback_used: bool = False
    sequential_thinking_applied: bool = False


class NormalizedTaskDraft(BaseModel):
    """승인 전 plan 안에 들어가는 표준 task draft."""

    title: str
    rationale: str = ""
    recommended_mcp_ids: List[str] = Field(default_factory=list)
    selected_mcp_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_arguments: Dict[str, Any] = Field(default_factory=dict)
    expected_result: str = ""
    approval_required: bool = False
    dependencies: List[int] = Field(default_factory=list)


class NormalizedPlan(BaseModel):
    """Planner가 반환해야 하는 표준 normalized plan schema."""

    goal: str
    intent: str = "command"
    summary: str = ""
    assumptions: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    required_capabilities: List[str] = Field(default_factory=list)
    approval_required: bool = True
    risks: List[str] = Field(default_factory=list)
    expected_outputs: List[str] = Field(default_factory=list)
    tasks_draft: List[NormalizedTaskDraft] = Field(default_factory=list)
    planner_metadata: PlannerMetadata = Field(default_factory=PlannerMetadata)
    strategy: Optional[RuntimeStrategy] = None
