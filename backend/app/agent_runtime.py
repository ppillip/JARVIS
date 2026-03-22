from __future__ import annotations

"""JARVIS 런타임이 공통으로 따르는 계획/실행 인터페이스 정의."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class RuntimeTask(BaseModel):
    """승인 후 실행 가능한 단일 태스크 구조."""

    title: str
    rationale: str = ""
    recommended_mcp_ids: List[str] = Field(default_factory=list)
    selected_mcp_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_arguments: Dict[str, Any] = Field(default_factory=dict)
    expected_result: str = ""


class RuntimePlan(BaseModel):
    """사용자에게 검토받는 단일 플랜 구조."""

    objective: str
    summary: str
    proposed_tasks: List[RuntimeTask] = Field(default_factory=list)


class RuntimeExecutionResult(BaseModel):
    """태스크 실행 후 로그, 근거, 보고를 담는 결과 구조."""

    status: Literal["completed", "failed"] = "completed"
    execution_log: List[str] = Field(default_factory=list)
    findings: List[str] = Field(default_factory=list)
    result_items: List[str] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)
    report: Dict[str, Any] = Field(default_factory=dict)
    task_statuses: List[str] = Field(default_factory=list)


class AgentRuntime(ABC):
    """Classic/DeepAgents 런타임이 구현해야 하는 공통 인터페이스."""

    @abstractmethod
    async def build_plan(
        self,
        command: str,
        soul: str,
        mcp_catalog: List[Dict[str, Any]],
        detailed: bool = False,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuntimePlan:
        """사용자 지령을 승인 전 플랜으로 변환한다."""
        raise NotImplementedError

    @abstractmethod
    async def execute_plan(
        self,
        plan: RuntimePlan,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuntimeExecutionResult:
        """플랜 전체를 실행해 보고 가능한 결과를 반환한다."""
        raise NotImplementedError

    @abstractmethod
    async def execute_tasks(
        self,
        tasks: List[RuntimeTask],
        context: Optional[Dict[str, Any]] = None,
    ) -> RuntimeExecutionResult:
        """이미 확정된 태스크 목록을 실행한다."""
        raise NotImplementedError
