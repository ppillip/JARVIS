from __future__ import annotations

"""독립 planner 서비스. 현재는 bridge를 통해 MCP-aware 계획 JSON을 생성한다."""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.llm_bridge import extract_json_object, invoke_bridge_text
from app.prompt_store import get_prompt_content, render_prompt_template

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOUL_PATH = PROJECT_ROOT / "soul.md"

app = FastAPI(title="JARVIS Planner MCP")


class MpcDefinition(BaseModel):
    """planner 서비스가 받는 MCP registry 항목 구조."""

    id: str
    name: str
    scope: str
    description: str
    capabilities: List[str]
    expected_input: str
    expected_output: str
    source_url: Optional[str] = None
    package_name: Optional[str] = None
    transport: Optional[str] = None
    auth_required: bool = False
    risk_level: str = "low"
    enabled: bool = True


class PlanStep(BaseModel):
    """planner가 반환하는 단일 계획 단계 구조."""

    step: str
    rationale: str = ""
    recommended_mcp_ids: List[str] = Field(default_factory=list)
    selected_mcp_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_arguments: Dict[str, Any] = Field(default_factory=dict)
    expected_result: str = ""


class PlannerRequest(BaseModel):
    """planner 서비스 입력 구조."""

    command: str = Field(..., min_length=1)
    detailed: bool = False
    mcps: List[MpcDefinition] = Field(default_factory=list)


class PlannerResponse(BaseModel):
    """planner 서비스 출력 구조."""

    plan: List[PlanStep]


def read_soul_prompt() -> str:
    """프로젝트의 soul.md를 읽어 planner system prompt 일부로 사용한다."""
    if not SOUL_PATH.exists():
        return "자비스는 항상 한국어로, 침착하고 예의 바른 존댓말로 답한다."
    content = SOUL_PATH.read_text().strip()
    return content or "자비스는 항상 한국어로, 침착하고 예의 바른 존댓말로 답한다."


def serialize_mcps_for_prompt(mcps: List[MpcDefinition]) -> str:
    """planner prompt에 주입할 MCP registry JSON 문자열을 만든다."""
    return json.dumps(
        [
            {
                "id": mcp.id,
                "name": mcp.name,
                "scope": mcp.scope,
                "description": mcp.description,
                "capabilities": mcp.capabilities,
                "expected_input": mcp.expected_input,
                "expected_output": mcp.expected_output,
                "auth_required": mcp.auth_required,
                "risk_level": mcp.risk_level,
                "transport": mcp.transport,
            }
            for mcp in mcps
        ],
        ensure_ascii=False,
        indent=2,
    )


def validate_mcp_ids(ids: List[str], mcps: List[MpcDefinition]) -> List[str]:
    """planner가 고른 MCP id 중 실제 registry에 존재하는 값만 남긴다."""
    known_ids = {mcp.id for mcp in mcps}
    return [mcp_id for mcp_id in ids if mcp_id in known_ids]


def fallback_plan(command: str, detailed: bool, mcps: List[MpcDefinition]) -> List[PlanStep]:
    """LLM planning 실패 시 planner 서비스가 반환할 기본 플랜이다."""
    items = [
        f'지령의 목표와 산출물을 분해한다: "{command}"',
        "제약 조건과 확인 포인트를 정리해 검토 가능한 실행안으로 만든다.",
        "승인 후 바로 수행할 수 있는 태스크 묶음으로 전환한다.",
    ]
    if detailed:
        items = [
            f'지령의 핵심 목표를 정의한다: "{command}"',
            "사용자 확인이 필요한 판단 지점을 분리한다.",
            "필요한 준비물과 의존성을 사전에 점검한다.",
            "실행 순서를 작업 단위로 세분화한다.",
            "각 작업의 완료 기준과 검증 포인트를 명시한다.",
        ]

    mcps_by_scope = {mcp.scope: mcp.id for mcp in mcps}
    default_ids = [mcps_by_scope.get("계획", "planner")]
    return [
        PlanStep(
            step=item,
            rationale="Planner MCP fallback plan",
            recommended_mcp_ids=default_ids,
        )
        for item in items
    ]


@app.get("/health")
def health_check() -> Dict[str, str]:
    """planner 서비스 healthcheck."""
    return {"status": "ok"}


@app.post("/planner/plan", response_model=PlannerResponse)
async def planner_plan(payload: PlannerRequest) -> PlannerResponse:
    """지령과 MCP registry를 받아 MCP-aware planning JSON을 생성한다."""
    model = os.getenv("CODEX_CHAT_MODEL", "default")
    target_steps = "4~6개" if payload.detailed else "3~4개"
    soul_prompt = read_soul_prompt()
    template = get_prompt_content(
        "planner_system",
        fallback=(
            "너는 JARVIS의 Planner MCP 서비스다. "
            "주어진 지령과 MCP registry를 읽고, 현재 사용 가능한 MCP를 기준으로만 검토 가능한 플랜과 실행계획을 세워라.\n\n"
            "반드시 JSON 객체 하나만 출력한다.\n"
            '형식: {"plan":[{"step":"...", "rationale":"...", "recommended_mcp_ids":["filesystem"], "selected_mcp_id":"filesystem", "tool_name":"list_directory", "tool_arguments":{"path":"$HOME"}, "expected_result":"홈디렉터리 폴더 목록"}]}\n'
            "규칙:\n"
            "- 단계 수는 {{target_steps}}로 맞춘다.\n"
            "- recommended_mcp_ids에는 registry에 존재하는 id만 넣는다.\n"
            "- selected_mcp_id는 recommended_mcp_ids 중 하나여야 한다.\n"
            "- 실제로 바로 수행 가능한 단순 조회/읽기 지령이면 1~2단계로 줄이고 메타 검토 단계를 만들지 않는다.\n"
            "- 사용자가 명시하지 않은 추가 범위나 추가 경로를 임의로 탐색하지 않는다.\n"
            "- 사용자가 단일 목록 조회나 단일 보고를 요청했으면 정확히 1개의 실행 단계만 만든다.\n"
            "- 최종 보고는 시스템이 수행하므로 '보고용 정리 단계'를 별도 step으로 만들지 않는다.\n"
            "- MCP capability에 tool 정보가 있으면 tool_name과 tool_arguments를 반드시 채운다.\n"
            "- Filesystem MCP의 허용 경로가 $HOME, $PROJECT_ROOT 로 제한되어 있으면 그 범위를 벗어나는 path를 계획하지 않는다.\n"
            "- 경로는 필요하면 $HOME 또는 $PROJECT_ROOT 변수를 사용한다.\n"
            "- 각 step은 승인 후 실행 가능한 작업 단계여야 한다.\n"
            "- rationale은 한 문장으로 짧게 쓴다.\n"
            "- expected_result에는 사용자가 최종 보고에서 받게 될 결과를 짧게 적는다.\n"
            "- 한국어로 출력한다.\n"
            "- 불가능한 도구를 가정하지 않는다.\n\n"
            "[SOUL]\n{{soul}}\n\n"
            "[MCP REGISTRY]\n{{mcps}}\n\n"
            "[사용자 지령]\n{{command}}"
        ),
    )
    prompt = render_prompt_template(
        template,
        {
            "target_steps": target_steps,
            "soul": soul_prompt,
            "mcps": serialize_mcps_for_prompt(payload.mcps),
            "command": payload.command.strip(),
        },
    )

    try:
        # planner도 직접 모델을 부르지 않고 bridge를 통해 JSON 계획을 받는다.
        raw, _ = await invoke_bridge_text(model=model, prompt=prompt)
        parsed = extract_json_object(raw)
        items = parsed.get("plan")
        if not isinstance(items, list) or not items:
            raise ValueError("plan array missing")
        plan = []
        for item in items:
            if not isinstance(item, dict):
                continue
            step = str(item.get("step", "")).strip()
            if not step:
                continue
            plan.append(
                PlanStep(
                    step=step,
                    rationale=str(item.get("rationale", "")).strip(),
                    recommended_mcp_ids=validate_mcp_ids(
                        [str(mcp_id) for mcp_id in item.get("recommended_mcp_ids", [])],
                        payload.mcps,
                    ),
                    selected_mcp_id=(
                        str(item.get("selected_mcp_id", "")).strip() or None
                    ),
                    tool_name=(str(item.get("tool_name", "")).strip() or None),
                    tool_arguments=item.get("tool_arguments", {}) if isinstance(item.get("tool_arguments"), dict) else {},
                    expected_result=str(item.get("expected_result", "")).strip(),
                )
            )
        if plan:
            return PlannerResponse(plan=plan)
    except Exception:
        return PlannerResponse(plan=fallback_plan(payload.command, payload.detailed, payload.mcps))

    raise HTTPException(status_code=502, detail="Planner MCP failed to generate a plan.")
