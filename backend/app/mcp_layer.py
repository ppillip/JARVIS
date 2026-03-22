from __future__ import annotations

"""향후 MCP adapter/skill/guardrail 일반화를 위한 추상 인터페이스."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class McpAdapter(ABC):
    """개별 MCP 서버에 실제 tool call을 전달하는 어댑터 인터페이스."""

    mcp_id: str

    @abstractmethod
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """특정 MCP tool을 호출하고 raw 결과를 반환한다."""
        raise NotImplementedError


class McpSkill(ABC):
    """사용자 의도를 MCP 실행 계획과 결과 해석으로 연결하는 skill 인터페이스."""

    mcp_id: str

    @abstractmethod
    def can_handle(self, intent: Dict[str, Any]) -> bool:
        """이 skill이 현재 intent를 처리할 수 있는지 판정한다."""
        raise NotImplementedError

    @abstractmethod
    def build_task(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        """intent를 MCP 실행 태스크 구조로 변환한다."""
        raise NotImplementedError

    @abstractmethod
    def interpret_result(self, intent: Dict[str, Any], raw_result: Dict[str, Any]) -> Dict[str, Any]:
        """MCP raw 결과를 사용자 보고용 구조로 해석한다."""
        raise NotImplementedError


class McpGuardrail(ABC):
    """MCP 호출 전 인자와 정책을 검증하는 가드레일 인터페이스."""

    @abstractmethod
    def validate(self, mcp_id: str, tool_name: str, arguments: Dict[str, Any]) -> Optional[str]:
        """위반 시 오류 메시지를, 통과 시 None을 반환한다."""
        raise NotImplementedError
