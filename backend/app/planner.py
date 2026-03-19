from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOUL_PATH = PROJECT_ROOT / "soul.md"

app = FastAPI(title="JARVIS Planner MCP")


class MpcDefinition(BaseModel):
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
    step: str
    rationale: str = ""
    recommended_mcp_ids: List[str] = Field(default_factory=list)


class PlannerRequest(BaseModel):
    command: str = Field(..., min_length=1)
    detailed: bool = False
    mcps: List[MpcDefinition] = Field(default_factory=list)


class PlannerResponse(BaseModel):
    plan: List[PlanStep]


def read_soul_prompt() -> str:
    if not SOUL_PATH.exists():
        return "자비스는 항상 한국어로, 침착하고 예의 바른 존댓말로 답한다."
    content = SOUL_PATH.read_text().strip()
    return content or "자비스는 항상 한국어로, 침착하고 예의 바른 존댓말로 답한다."


def serialize_mcps_for_prompt(mcps: List[MpcDefinition]) -> str:
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


def extract_json_object(text: str) -> Dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def validate_mcp_ids(ids: List[str], mcps: List[MpcDefinition]) -> List[str]:
    known_ids = {mcp.id for mcp in mcps}
    return [mcp_id for mcp_id in ids if mcp_id in known_ids]


def fallback_plan(command: str, detailed: bool, mcps: List[MpcDefinition]) -> List[PlanStep]:
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


async def run_codex_exec(prompt: str, model: str) -> str:
    with tempfile.NamedTemporaryFile(delete=False) as output_file:
        output_path = output_file.name

    cmd = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "-C",
        str(PROJECT_ROOT),
        "-o",
        output_path,
    ]
    if model != "default":
        cmd.extend(["-m", model])
    cmd.append(prompt)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()

    try:
        reply = Path(output_path).read_text().strip()
    finally:
        Path(output_path).unlink(missing_ok=True)

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="ignore").strip() or "codex exec failed"
        raise RuntimeError(detail)
    if not reply:
        raise RuntimeError("codex exec returned empty output")
    return reply


@app.get("/health")
def health_check() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/planner/plan", response_model=PlannerResponse)
async def planner_plan(payload: PlannerRequest) -> PlannerResponse:
    model = os.getenv("CODEX_CHAT_MODEL", "default")
    target_steps = "4~6개" if payload.detailed else "3~4개"
    soul_prompt = read_soul_prompt()
    prompt = (
        "너는 JARVIS의 Planner MCP 서비스다. "
        "주어진 지령과 MCP registry를 읽고, 현재 사용 가능한 MCP를 기준으로만 검토 가능한 플랜을 세워라.\n\n"
        "반드시 JSON 객체 하나만 출력한다.\n"
        '형식: {"plan":[{"step":"...", "rationale":"...", "recommended_mcp_ids":["planner"]}]}\n'
        "규칙:\n"
        f"- 단계 수는 {target_steps}로 맞춘다.\n"
        "- recommended_mcp_ids에는 registry에 존재하는 id만 넣는다.\n"
        "- 각 step은 승인 전 검토 가능한 작업 단계여야 한다.\n"
        "- rationale은 한 문장으로 짧게 쓴다.\n"
        "- 한국어로 출력한다.\n"
        "- 불가능한 도구를 가정하지 않는다.\n\n"
        f"[SOUL]\n{soul_prompt}\n\n"
        f"[MCP REGISTRY]\n{serialize_mcps_for_prompt(payload.mcps)}\n\n"
        f"[사용자 지령]\n{payload.command.strip()}"
    )

    try:
        raw = await run_codex_exec(prompt=prompt, model=model)
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
                )
            )
        if plan:
            return PlannerResponse(plan=plan)
    except Exception:
        return PlannerResponse(plan=fallback_plan(payload.command, payload.detailed, payload.mcps))

    raise HTTPException(status_code=502, detail="Planner MCP failed to generate a plan.")
