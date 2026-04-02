from __future__ import annotations

"""지원 불가/우회 안내 응답을 일관되게 만드는 정책 계층."""

from typing import Any, Dict, List


def _capability_label(capability: str) -> str:
    labels = {
        "weather.read": "실시간 날씨 조회",
        "law.lookup": "법령/판례 조회",
        "browser.action": "브라우저 조작",
        "browser.navigate": "브라우저 열기/이동",
        "filesystem.write": "파일 수정",
        "code.modify": "코드 수정",
    }
    return labels.get(capability, capability)


def build_decline_reply(route_result: Dict[str, Any]) -> str:
    """라우터 결과를 사용자용 지원 불가/우회 메시지로 변환한다."""
    reason = str(route_result.get("reason") or "현재 요청을 처리할 수 없습니다.").strip()
    missing = [str(item).strip() for item in route_result.get("missing_capabilities", []) if str(item).strip()]
    safe = bool((route_result.get("safety") or {}).get("allowed", True))
    if not safe:
        return f"{reason}\n\n이 요청은 현재 정책상 처리할 수 없습니다."

    if missing:
        labels = ", ".join(_capability_label(item) for item in missing)
        return (
            f"{reason}\n\n"
            f"현재 연결된 기능으로는 `{labels}` capability가 없어 직접 처리할 수 없습니다.\n"
            "원하면 가능한 대체 방법이나 수동 확인 절차는 안내할 수 있습니다."
        )

    return (
        f"{reason}\n\n"
        "현재 연결된 기능 범위에서는 직접 처리할 수 없습니다. "
        "원하면 가능한 대안이나 우회 절차를 안내할 수 있습니다."
    )
