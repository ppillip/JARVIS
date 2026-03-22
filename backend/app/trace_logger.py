from __future__ import annotations

"""Planner/executor/fallback 경로를 구조화해 남기는 trace helper.

누가 계획했고, 어디서 fallback이 일어났고, 어떤 실행이 완료됐는지
운영 관점에서 재구성할 수 있도록 일관된 trace 이벤트를 남긴다.
"""

from typing import Any, Dict, List, Optional


def ensure_trace(context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """context에서 trace 배열을 보장해 반환한다."""
    if context is None:
        return []
    trace = context.setdefault("trace", [])
    if not isinstance(trace, list):
        trace = []
        context["trace"] = trace
    return trace


def add_trace(context: Optional[Dict[str, Any]], event: str, **fields: Any) -> None:
    """구조화된 trace 이벤트를 context에 추가한다."""
    trace = ensure_trace(context)
    trace.append({"event": event, **fields})
