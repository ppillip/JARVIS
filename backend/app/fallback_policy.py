from __future__ import annotations

"""Capability resolution 이후 LLM fallback 또는 decline 응답 정책."""

from typing import Any, Dict

from app.capability_resolver import CapabilityResolution
from app.decline_policy import build_decline_reply


def should_use_llm_fallback(resolution: CapabilityResolution) -> bool:
    """해결 계약상 LLM fallback이 최종 경로인지 판정한다."""
    return resolution.mode == "llm_fallback"


def build_decline_from_resolution(resolution: CapabilityResolution) -> str:
    """resolution 결과를 사용자용 decline 문구로 변환한다."""
    return build_decline_reply(
        {
            "reason": resolution.reason,
            "missing_capabilities": resolution.missing_capabilities,
            "safety": resolution.safety,
        }
    )
