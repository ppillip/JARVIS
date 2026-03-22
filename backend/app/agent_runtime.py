from __future__ import annotations

"""JARVIS planner/executor 런타임의 공통 인터페이스 정의.

planner 축과 executor 축이 서로 직접 결합되지 않도록, plan/task/report에 대한
공통 데이터 구조와 추상 인터페이스를 이 파일에서 고정한다.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class RuntimeStrategyOption(BaseModel):
    """전략 비교에 포함되는 단일 옵션 구조."""

    name: str
    approach: str = ""
    tradeoffs: str = ""


class RuntimeStrategy(BaseModel):
    """Sequential Thinking이 남기는 전략 정리 결과."""

    applied: bool = False
    summary: str = ""
    recommended_strategy: str = ""
    options: List[RuntimeStrategyOption] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    reason: str = ""


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
    strategy: Optional[RuntimeStrategy] = None
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


class PlannerRuntime(ABC):
    """사용자 지령을 normalized plan으로 만드는 planner 인터페이스."""

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


class ExecutorRuntime(ABC):
    """확정된 태스크를 안정적으로 집행하는 executor 인터페이스."""

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


class AgentRuntime(PlannerRuntime, ExecutorRuntime, ABC):
    """하위 호환용 결합 인터페이스.

    점진적 리팩터링을 위해 planner/executor를 모두 구현하는 기존 타입을 잠시 유지한다.
    """
