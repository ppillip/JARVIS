from __future__ import annotations

"""Executor가 수집한 evidence/findings를 최종 보고 구조로 조립한다.

실행 사실 나열이 아니라 사용자가 읽을 수 있는 결과 보고를 만들기 위해,
findings, result_items, conclusion을 카드 구조로 정규화한다.
"""

from typing import Any, Dict, List

from app.agent_runtime import RuntimeTask


def build_execution_report(
    tasks: List[RuntimeTask],
    used_mcp_names: List[str],
    evidence: List[str],
    findings: List[str],
    result_items: List[str],
) -> Dict[str, Any]:
    """실행 결과를 최종 보고 카드 구조로 정규화한다."""
    task_titles = [f"Task {index}. {task.title}" for index, task in enumerate(tasks, start=1)]
    conclusion = (
        "요청한 실행은 완료되었고, 아래 발견 사항을 기준으로 후속 판단이 가능한 상태입니다."
        if findings
        else "요청한 실행은 완료되었지만, 아직 의미 있는 발견 사항을 구조화하지 못했습니다."
    )
    return {
        "status": "보고 완료",
        "summary": f"보고합니다. 총 {len(tasks)}개의 실행 태스크를 수행했고, 사용 MCP는 {', '.join(used_mcp_names) or '없음'}입니다.",
        "objective": "지령에 따라 필요한 실행을 수행하고, 실행 결과에서 확인된 사실과 결론을 보고합니다.",
        "tasks": task_titles,
        "result_items": result_items,
        "findings": findings,
        "conclusion": conclusion,
        "evidence": evidence,
        "nextAction": "필요하면 이 보고를 기준으로 추가 조사, 수정, 또는 후속 지령을 내리십시오.",
    }
