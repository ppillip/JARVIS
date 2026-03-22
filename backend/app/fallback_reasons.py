from __future__ import annotations

"""Planner fallback 사유를 표준 코드로 정규화한다.

trace와 운영 분석에서 fallback 원인을 일관되게 비교할 수 있도록
예외 메시지를 고정된 taxonomy로 매핑한다.
"""

from typing import Tuple


FALLBACK_REASON_SCOPE_MISSING = "auth_scope_missing"
FALLBACK_REASON_BRIDGE_UNAVAILABLE = "bridge_unavailable"
FALLBACK_REASON_DEEPAGENT_UNAVAILABLE = "deepagent_unavailable"
FALLBACK_REASON_DEEPAGENT_INVALID_OUTPUT = "deepagent_invalid_output"
FALLBACK_REASON_DEEPAGENT_EXCEPTION = "deepagent_exception"


def classify_fallback_reason(message: str) -> Tuple[str, str]:
    """예외 문자열을 fallback code와 사용자용 이유로 정규화한다."""
    lowered = (message or "").strip().lower()
    if "missing scopes" in lowered or "model.request" in lowered or "api.responses.write" in lowered:
        return FALLBACK_REASON_SCOPE_MISSING, "모델 호출 권한 scope가 부족합니다."
    if "bridge" in lowered and ("unavailable" in lowered or "failed" in lowered):
        return FALLBACK_REASON_BRIDGE_UNAVAILABLE, "LLM bridge가 현재 사용할 수 없습니다."
    if "did not return executable tasks" in lowered or "json" in lowered:
        return FALLBACK_REASON_DEEPAGENT_INVALID_OUTPUT, "Deep Agent가 실행 가능한 planner 출력을 반환하지 못했습니다."
    if "unavailable" in lowered:
        return FALLBACK_REASON_DEEPAGENT_UNAVAILABLE, "Deep Agent planner를 현재 사용할 수 없습니다."
    return FALLBACK_REASON_DEEPAGENT_EXCEPTION, (message or "알 수 없는 planner 예외가 발생했습니다.").strip()
