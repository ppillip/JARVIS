from __future__ import annotations

"""JARVIS의 메인 FastAPI 엔드포인트와 세션/UI/API 조립 계층.

planner/executor/registry/bridge를 직접 구현하지 않고, 각 계층을 조합해
프론트엔드가 쓰는 API와 세션, timeline 저장을 한 곳에서 제공한다.
"""

import base64
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import time
import tempfile
import uuid
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel, Field
import uvicorn

from app.agent_runtime import RuntimePlan, RuntimeTask
from app.capability_map_service import build_capability_map
from app.capability_resolver import resolve_capability
from app.fallback_policy import build_decline_from_resolution
from app.intent_router import adjudicate_intent
from app.filesystem_skill import execute_filesystem_task
from app.llm_bridge import extract_json_object, invoke_bridge_text
from app.plan_normalizer import normalize_runtime_plan
from app.plan_schema import NormalizedPlan, NormalizedTaskDraft, PlannerMetadata
from app.prompt_store import get_prompt_content, render_prompt_template
from app.runtime_factory import get_executor_runtime, get_planner_runtime
from app.tool_answer_runtime import ToolAnswerRuntime
from app.sqlite_store import (
    activate_prompt_version as activate_prompt_version_store,
    append_conversation_event,
    append_prompt_version,
    create_prompt_entry,
    delete_prompt_entry,
    get_workflow_run,
    initialize_database,
    list_conversation_events,
    list_conversation_summaries,
    list_workflow_runs,
    list_prompt_entries,
    list_registry_entries as list_registry_entries_from_db,
    replace_workflow_trace,
    upsert_workflow_run,
)
from app.task_compiler import compile_tasks, map_task_to_mcps

load_dotenv()
initialize_database()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HOME_ROOT = Path.home()
MCP_REGISTRY_URL = os.getenv("MCP_REGISTRY_URL", "http://127.0.0.1:7100/registry/mcps")
PLANNER_MCP_URL = os.getenv("PLANNER_MCP_URL", "http://127.0.0.1:7200/planner/plan")
MCP_RUNTIME_ROOT = PROJECT_ROOT / "mcp-runtime"
FILESYSTEM_MCP_BIN = MCP_RUNTIME_ROOT / "node_modules" / ".bin" / "mcp-server-filesystem"
MCP_PROTOCOL_VERSION = "2025-11-25"
TOOL_ANSWER_RUNTIME = ToolAnswerRuntime()


class MpcDefinition(BaseModel):
    """메인 API가 사용하는 MCP registry 항목 구조."""

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
    risk_level: Literal["low", "medium", "high"] = "low"
    enabled: bool = True


class ProposedTask(BaseModel):
    """승인 전 플랜 안에 들어가는 제안 태스크 구조."""

    title: str
    rationale: str = ""
    recommended_mcp_ids: List[str] = Field(default_factory=list)
    selected_mcp_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_arguments: Dict[str, Any] = Field(default_factory=dict)
    expected_result: str = ""


class StrategyOption(BaseModel):
    """UI에 표시하는 전략 옵션 구조."""

    name: str
    approach: str = ""
    tradeoffs: str = ""


class StrategyDraft(BaseModel):
    """Sequential Thinking이 생성한 전략 정리 UI 구조."""

    applied: bool = False
    summary: str = ""
    recommended_strategy: str = ""
    options: List[StrategyOption] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    reason: str = ""


class PlanDraft(BaseModel):
    """UI에 노출되는 단일 플랜 초안."""

    objective: str
    summary: str
    assumptions: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    required_capabilities: List[str] = Field(default_factory=list)
    approval_required: bool = True
    risks: List[str] = Field(default_factory=list)
    expected_outputs: List[str] = Field(default_factory=list)
    planner_metadata: Dict[str, Any] = Field(default_factory=dict)
    strategy: Optional[StrategyDraft] = None
    proposed_tasks: List[ProposedTask] = Field(default_factory=list, min_length=1)


class CommandRequest(BaseModel):
    """플랜 생성을 요청하는 입력 구조."""

    command: str = Field(..., min_length=1)


class ReviewRequest(BaseModel):
    """플랜 세분화/수정 요청 입력 구조."""

    command: str = Field(..., min_length=1)
    revision_count: int = Field(default=1, ge=1)


class ApproveRequest(BaseModel):
    """승인할 플랜을 전달하는 입력 구조."""

    plan: PlanDraft
    run_id: Optional[str] = None


class ChatRequest(BaseModel):
    """일반 채팅 또는 지령 입력 요청 구조."""

    message: str = Field(..., min_length=1)
    previous_response_id: Optional[str] = None
    conversation: List[Dict[str, str]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """채팅 응답 구조."""

    reply: str
    response_id: Optional[str] = None
    model: str
    mode: Literal["answer", "tool_answer", "plan", "decline"] = "answer"
    workflow: Optional[Dict[str, Any]] = None


class TaskItem(BaseModel):
    """UI와 실행 API 사이에서 오가는 확정 태스크 구조."""

    id: int
    title: str
    status: Literal["queued", "in_progress", "done", "failed"] = "queued"
    mcp_ids: List[str]
    selected_mcp_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_arguments: Dict[str, Any] = Field(default_factory=dict)
    expected_result: str = ""


class WorkflowResponse(BaseModel):
    """플랜/승인/태스크 상태를 함께 내려주는 워크플로우 응답 구조."""

    phase: str
    approval: str
    message: str
    plan: Optional[PlanDraft] = None
    tasks: List[TaskItem]
    mcps: List[MpcDefinition]
    trace: List[Dict[str, Any]] = Field(default_factory=list)
    run_id: Optional[str] = None


class ExecuteRequest(BaseModel):
    """실행할 태스크 목록 입력 구조."""

    run_id: Optional[str] = None
    tasks: List[TaskItem] = Field(default_factory=list, min_length=1)


class ExecuteResponse(BaseModel):
    """실행 로그와 보고를 포함한 실행 응답 구조."""

    phase: str
    message: str
    tasks: List[TaskItem]
    execution_log: List[str]
    execution_report: Dict[str, Any]
    trace: List[Dict[str, Any]] = Field(default_factory=list)
    run_id: Optional[str] = None


class WorkflowRunRecord(BaseModel):
    """저장된 실행 단위의 trace/report 조회 응답 구조."""

    id: str
    command_text: Optional[str] = None
    phase: str
    planner_type: Optional[str] = None
    fallback_used: bool = False
    plan: Optional[Dict[str, Any]] = None
    tasks: List[Dict[str, Any]] = Field(default_factory=list)
    report: Optional[Dict[str, Any]] = None
    trace: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str


class WorkflowRunSummary(BaseModel):
    """최근 실행 목록에 쓰는 경량 run 요약 구조."""

    id: str
    command_text: Optional[str] = None
    phase: str
    planner_type: Optional[str] = None
    fallback_used: bool = False
    created_at: str
    updated_at: str


class ConversationEventRecord(BaseModel):
    """현재 세션 타임라인의 단일 대화/워크플로우 이벤트."""

    id: int
    conversation_id: str
    sequence_no: int
    event_type: str
    payload: Dict[str, Any]
    created_at: str


class ConversationSummaryRecord(BaseModel):
    """좌측 패널에 노출할 대화 세션 요약 구조."""

    conversation_id: str
    title: str
    preview: str
    event_count: int
    created_at: str
    updated_at: str
    current: bool = False


class ConversationSelectRequest(BaseModel):
    """브라우저 세션이 바라볼 active conversation 변경 요청."""

    conversation_id: str = Field(..., min_length=1)


class RegistryToggleRequest(BaseModel):
    """레지스트리 항목 활성/비활성 요청."""

    enabled: bool


class RegistryCreateRequest(BaseModel):
    """신규 MCP registry 항목 생성 요청."""

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    scope: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    capabilities: List[str] = Field(default_factory=list)
    expected_input: str = Field(..., min_length=1)
    expected_output: str = Field(..., min_length=1)
    source_url: Optional[str] = None
    package_name: Optional[str] = None
    transport: Optional[str] = None
    auth_required: bool = False
    risk_level: Literal["low", "medium", "high"] = "low"
    enabled: bool = True


class AuthStatusResponse(BaseModel):
    """현재 세션의 OAuth 인증 상태 응답."""

    authenticated: bool
    provider: Optional[str] = None
    profile_id: Optional[str] = None
    account_id: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    expires_at: Optional[int] = None
    error: Optional[str] = None


class PromptRecord(BaseModel):
    """Prompt DB에서 읽은 프롬프트 레코드 구조."""

    id: str
    name: str
    description: str
    content: str
    active_version: int
    updated_at: str
    versions: List[Dict[str, Any]] = Field(default_factory=list)


class PromptCreateRequest(BaseModel):
    """프롬프트 신규 생성 요청."""

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)


class PromptUpdateRequest(BaseModel):
    """프롬프트 새 버전 추가 요청."""

    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)


class PromptActivateVersionRequest(BaseModel):
    """특정 프롬프트 버전을 다시 활성화하는 요청."""

    version: int = Field(..., ge=1)


DEFAULT_MCP_CATALOG = [
    MpcDefinition(
        id="filesystem",
        name="Filesystem MCP",
        scope="파일",
        description="프로젝트 파일 탐색과 생성, 수정 작업을 담당합니다.",
        capabilities=["파일 탐색", "코드 수정", "산출물 생성"],
        expected_input="작업 경로, 변경 대상, 수정 요구사항",
        expected_output="변경 파일, 구조 정보, 산출물 목록",
    ),
    MpcDefinition(
        id="playwright",
        name="Playwright MCP",
        scope="브라우저",
        description="실제 브라우저를 열어 페이지 이동, 클릭, 입력, 스냅샷, 스크린샷 기반 UI 검증을 수행합니다.",
        capabilities=["브라우저 자동화", "페이지 열기", "클릭/입력", "스냅샷/스크린샷", "UI 흐름 검증"],
        expected_input="URL, 액션 시나리오, 요소 ref, 입력값, 검증 대상",
        expected_output="브라우저 상태, 스냅샷, 검증 결과, 스크린샷 경로",
    ),
    MpcDefinition(
        id="korean_law",
        name="Korean Law MCP",
        scope="법령",
        description="법제처 Open API 기반으로 법령, 조문, 판례, 행정규칙, 자치법규, 법령해석을 검색하고 본문을 조회합니다.",
        capabilities=["법령 검색", "조문 조회", "판례 검색", "행정규칙/자치법규 조회", "법령해석 조회"],
        expected_input="질의어, 법령명, 조문 번호, 판례 검색어, LAW_OC API 키",
        expected_output="검색 결과 목록, 본문, 조문 텍스트, 판례/해석 요약",
    ),
]


OPENAI_OAUTH_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_OAUTH_SCOPES = "openid profile email offline_access api.connectors.read api.connectors.invoke"
STATE_DIR = Path(os.getenv("NICECODEX_STATE_DIR", str(Path.home() / ".nicecodex")))
AGENT_DIR = STATE_DIR / "agent"
SOUL_PATH = PROJECT_ROOT / "soul.md"
AUTH_PROFILES_PATH = AGENT_DIR / "auth-profiles.json"
AUTH_PROFILES_LOCK_PATH = AGENT_DIR / "auth-profiles.lock"
PENDING_OAUTH_PATH = AGENT_DIR / "pending-oauth.json"
PENDING_OAUTH_LOCK_PATH = AGENT_DIR / "pending-oauth.lock"
LOOPBACK_CALLBACK_HOST = os.getenv("OPENAI_OAUTH_LOOPBACK_HOST", "localhost")
LOOPBACK_CALLBACK_PORT = int(os.getenv("OPENAI_OAUTH_LOOPBACK_PORT", "1455"))

app = FastAPI(title="JARVIS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "nicecodex-dev-secret"),
    same_site="lax",
    https_only=False,
)

loopback_app = FastAPI(title="JARVIS OAuth Loopback")
loopback_app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "nicecodex-dev-secret"),
    same_site="lax",
    https_only=False,
)

_loopback_server_started = False
_loopback_server_lock = threading.Lock()


class McpWebSocketHub:
    """MCP registry 변경을 UI에 push하기 위한 websocket 연결 관리자."""

    def __init__(self) -> None:
        """연결 목록과 동기화용 lock을 초기화한다."""
        self.connections: List[WebSocket] = []
        self.lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """새 websocket 연결을 등록한다."""
        await websocket.accept()
        async with self.lock:
            self.connections.append(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        """끊긴 websocket 연결을 제거한다."""
        async with self.lock:
            if websocket in self.connections:
                self.connections.remove(websocket)

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        """현재 연결된 모든 클라이언트에 MCP 변경 이벤트를 보낸다."""
        stale: List[WebSocket] = []
        async with self.lock:
            for websocket in self.connections:
                try:
                    await websocket.send_json(payload)
                except Exception:
                    stale.append(websocket)
            for websocket in stale:
                if websocket in self.connections:
                    self.connections.remove(websocket)


mcp_ws_hub = McpWebSocketHub()


def load_mcp_catalog() -> List[MpcDefinition]:
    """registry server, SQLite, 기본값 순서로 MCP 카탈로그를 로드한다."""
    try:
        raw = fetch_registry_entries()
        if not isinstance(raw, list) or not raw:
            raise ValueError("registry must be a non-empty list")
        return [enrich_mcp_definition(MpcDefinition(**item)) for item in raw if item.get("enabled", True)]
    except Exception:
        try:
            raw = list_registry_entries_from_db()
            if not isinstance(raw, list) or not raw:
                raise ValueError("registry table must be a non-empty list")
            return [enrich_mcp_definition(MpcDefinition(**item)) for item in raw if item.get("enabled", True)]
        except Exception:
            return [enrich_mcp_definition(item) for item in DEFAULT_MCP_CATALOG]


def fetch_registry_entries() -> List[Dict[str, Any]]:
    """외부 registry server에서 MCP 목록을 가져온다."""
    with httpx.Client(timeout=5.0) as client:
        response = client.get(MCP_REGISTRY_URL)
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, list):
        raise RuntimeError("Registry server returned invalid payload.")
    return data


def get_registry_base_url() -> str:
    """MCP registry base URL을 반환한다."""
    return MCP_REGISTRY_URL.removesuffix("/registry/mcps")


def serialize_mcps_for_prompt(mcps: List[MpcDefinition]) -> str:
    """MCP 카탈로그를 프롬프트 주입용 JSON 문자열로 직렬화한다."""
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


def build_planner_capability_map(mcps: List[MpcDefinition]) -> List[Dict[str, Any]]:
    """planner/ST/classifier에 전달할 capability map을 만든다."""
    return build_capability_map([mcp.model_dump() for mcp in mcps])


def enrich_mcp_definition(mcp: MpcDefinition) -> MpcDefinition:
    """특정 MCP에 런타임 특화 capability 설명을 보강한다."""
    capabilities = list(mcp.capabilities)
    if mcp.id == "filesystem":
        capabilities.extend(
            [
                "tools:list_directory(path), directory_tree(path), read_text_file(path), write_file(path,content)",
                "허용 경로는 $HOME, $PROJECT_ROOT 로 제한됨",
                "path 변수 사용 가능: $HOME, $PROJECT_ROOT",
                "읽기 중심 조회는 list_directory 또는 directory_tree를 우선 사용",
            ]
        )
    if mcp.id == "playwright":
        capabilities.extend(
            [
                "tools:open(url, headed?), snapshot(), click(ref), fill(ref,text), press(key), screenshot(path?)",
                "실제 브라우저 자동화가 필요할 때 선택",
                "요소 조작 전 snapshot으로 ref를 수집하는 흐름을 우선 사용",
                "UI 검증과 사용자 흐름 재현에 사용",
            ]
        )
    if mcp.id == "korean_law":
        capabilities.extend(
            [
                "tools:search_law(query), get_law_text(mst, jo?), search_precedents(query), get_precedent_text(id)",
                "tools:search_admin_rule(query), get_admin_rule(id), search_ordinance(query), get_ordinance(id)",
                "tools:search_interpretations(query), get_interpretation_text(id), search_all(query)",
                "법제처 Open API 키 LAW_OC 가 필요함",
                "국가 법령, 조문, 판례, 행정규칙, 자치법규, 법령해석 조회에 사용",
            ]
        )
    return mcp.model_copy(update={"capabilities": list(dict.fromkeys(capabilities))})


def list_prompt_records() -> List[PromptRecord]:
    """Prompt DB 전체를 PromptRecord 목록으로 반환한다."""
    return [PromptRecord(**item) for item in list_prompt_entries()]


def normalized_plan_to_draft(plan: NormalizedPlan) -> PlanDraft:
    """Normalized plan을 프론트엔드용 PlanDraft로 변환한다."""
    return PlanDraft(
        objective=plan.goal,
        summary=plan.summary,
        assumptions=plan.assumptions,
        constraints=plan.constraints,
        required_capabilities=plan.required_capabilities,
        approval_required=plan.approval_required,
        risks=plan.risks,
        expected_outputs=plan.expected_outputs,
        planner_metadata=plan.planner_metadata.model_dump(),
        strategy=StrategyDraft(**plan.strategy.model_dump()) if plan.strategy else None,
        proposed_tasks=[
            ProposedTask(
                title=item.title,
                rationale=item.rationale,
                recommended_mcp_ids=item.recommended_mcp_ids or ([item.selected_mcp_id] if item.selected_mcp_id else []),
                selected_mcp_id=item.selected_mcp_id,
                tool_name=item.tool_name,
                tool_arguments=item.tool_arguments,
                expected_result=item.expected_result,
            )
            for item in plan.tasks_draft
        ],
    )


def draft_to_normalized_plan(plan: PlanDraft) -> NormalizedPlan:
    """프론트엔드에서 되돌아온 PlanDraft를 normalized plan으로 복원한다."""
    return NormalizedPlan(
        goal=plan.objective,
        intent="command",
        summary=plan.summary,
        assumptions=list(plan.assumptions),
        constraints=list(plan.constraints),
        required_capabilities=list(plan.required_capabilities),
        approval_required=plan.approval_required,
        risks=list(plan.risks),
        expected_outputs=list(plan.expected_outputs),
        planner_metadata=PlannerMetadata(**plan.planner_metadata) if plan.planner_metadata else PlannerMetadata(),
        strategy=plan.strategy.model_dump() if plan.strategy else None,
        tasks_draft=[
            NormalizedTaskDraft(
                title=item.title,
                rationale=item.rationale,
                recommended_mcp_ids=item.recommended_mcp_ids,
                selected_mcp_id=item.selected_mcp_id,
                tool_name=item.tool_name,
                tool_arguments=item.tool_arguments,
                expected_result=item.expected_result,
            )
            for item in plan.proposed_tasks
        ],
    )


def to_runtime_tasks(tasks: List[TaskItem]) -> List[RuntimeTask]:
    """UI의 TaskItem 목록을 런타임용 RuntimeTask 목록으로 변환한다."""
    return [
        RuntimeTask(
            title=task.title,
            rationale="",
            recommended_mcp_ids=task.mcp_ids,
            selected_mcp_id=task.selected_mcp_id,
            tool_name=task.tool_name,
            tool_arguments=task.tool_arguments,
            expected_result=task.expected_result,
        )
        for task in tasks
    ]


def fallback_plan(command: str, detailed: bool) -> List[ProposedTask]:
    """메인 API가 planner 실패 시 사용할 기본 플랜을 만든다."""
    base_plan = [
        f'지령의 목표와 산출물을 분해한다: "{command}"',
        "제약 조건과 확인 포인트를 정리해 검토 가능한 실행안으로 만든다.",
        "승인 후 바로 수행할 수 있는 태스크 묶음으로 전환한다.",
    ]

    if detailed:
        base_plan = [
            f'지령의 핵심 목표를 정의한다: "{command}"',
            "사용자 확인이 필요한 판단 지점을 분리한다.",
            "필요한 준비물과 의존성을 사전에 점검한다.",
            "실행 순서를 작업 단위로 세분화한다.",
            "각 작업의 완료 기준과 검증 포인트를 명시한다.",
        ]

    return [
        ProposedTask(
            title=item,
            rationale="LLM 플랜 생성 실패로 기본 플랜을 사용합니다.",
            recommended_mcp_ids=map_task_to_mcps(item, index),
        )
        for index, item in enumerate(base_plan)
    ]


def get_conversation_id(request: Request) -> str:
    """현재 브라우저 세션에 연결된 conversation_id를 반환하고 없으면 생성한다."""
    conversation_id = request.session.get("conversation_id")
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        request.session["conversation_id"] = conversation_id
    return str(conversation_id)


def append_message_event(request: Request, event_type: str, text: str) -> None:
    """단일 메시지를 DB 기반 타임라인에 저장한다."""
    append_conversation_event(get_conversation_id(request), event_type, {"text": text})


def append_workflow_snapshot_event(
    request: Request,
    *,
    phase: str,
    approval: str,
    plan: Optional[PlanDraft],
    tasks: List[TaskItem],
    execution_log: List[str],
    execution_report: Optional[Dict[str, Any]],
    trace: List[Dict[str, Any]],
    run_id: Optional[str],
) -> None:
    """현재 워크플로우 상태를 하나의 snapshot 이벤트로 저장한다."""
    append_conversation_event(
        get_conversation_id(request),
        "workflow_snapshot",
        {
            "phase": phase,
            "approval": approval,
            "plan": plan.model_dump() if plan else None,
            "tasks": [task.model_dump() for task in tasks],
            "executionLog": execution_log,
            "executionReport": execution_report,
            "traceEvents": trace,
            "currentRunId": run_id,
        },
    )


def build_assistant_report_reply(report: Dict[str, Any]) -> str:
    """실행 보고서를 채팅형 최종 응답으로 압축한다."""
    result_items = [str(item).strip() for item in report.get("result_items", []) if str(item).strip()]
    findings = [str(item).strip() for item in report.get("findings", []) if str(item).strip()]
    summary = str(report.get("summary", "")).strip()
    conclusion = str(report.get("conclusion", "")).strip()

    lines: List[str] = []
    if result_items:
        lines.append("결과를 보고합니다.")
        lines.extend(result_items[:8])
    elif findings:
        lines.append("결과를 보고합니다.")
        lines.extend(findings[:4])
    elif summary:
        lines.append(summary)
    elif conclusion:
        lines.append(conclusion)
    else:
        lines.append("실행은 완료되었지만 요약 가능한 결과를 아직 만들지 못했습니다.")

    if conclusion and conclusion not in lines[-1]:
        lines.extend(["", conclusion])

    return "\n".join(lines)


@app.get("/api/health")
def health_check() -> dict[str, str]:
    """메인 백엔드 healthcheck."""
    return {"status": "ok"}


@app.get("/api/mcps", response_model=List[MpcDefinition])
def list_mcps() -> List[MpcDefinition]:
    """현재 활성 MCP 카탈로그를 반환한다."""
    return load_mcp_catalog()


@app.websocket("/ws/mcps")
async def mcp_updates_ws(websocket: WebSocket) -> None:
    """MCP 변경을 실시간으로 push하는 websocket 엔드포인트."""
    await mcp_ws_hub.connect(websocket)
    try:
        await websocket.send_json(
            {
                "type": "mcps_updated",
                "mcps": [item.model_dump() for item in load_mcp_catalog()],
            }
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await mcp_ws_hub.disconnect(websocket)
    except Exception:
        await mcp_ws_hub.disconnect(websocket)


@app.get("/api/registry/mcps")
def list_registry_mcps() -> List[Dict[str, Any]]:
    """관리자 UI용으로 registry 전체 항목을 반환한다."""
    try:
        return fetch_registry_entries()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Registry server request failed: {exc}") from exc


@app.post("/api/registry/mcps")
async def create_registry_mcp(payload: RegistryCreateRequest) -> Dict[str, Any]:
    """registry server에 새 MCP를 생성하고 변경 이벤트를 broadcast한다."""
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.post(f"{get_registry_base_url()}/registry/mcps", json=payload.model_dump())
            response.raise_for_status()
            created = response.json()
        await mcp_ws_hub.broadcast(
            {
                "type": "mcps_updated",
                "mcps": [item.model_dump() for item in load_mcp_catalog()],
            }
        )
        return created
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "Registry create failed."
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Registry server request failed: {exc}") from exc


@app.patch("/api/registry/mcps/{mcp_id}")
async def update_registry_mcp(mcp_id: str, payload: RegistryToggleRequest) -> Dict[str, Any]:
    """registry 항목 상태를 바꾸고 변경 이벤트를 broadcast한다."""
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.patch(
                f"{get_registry_base_url()}/registry/mcps/{mcp_id}",
                json=payload.model_dump(),
            )
            response.raise_for_status()
            updated = response.json()
        await mcp_ws_hub.broadcast(
            {
                "type": "mcps_updated",
                "mcps": [item.model_dump() for item in load_mcp_catalog()],
            }
        )
        return updated
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "Registry update failed."
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Registry server request failed: {exc}") from exc


@app.get("/api/prompts", response_model=List[PromptRecord])
def list_prompts() -> List[PromptRecord]:
    """Prompt DB 전체 레코드를 반환한다."""
    return list_prompt_records()


@app.post("/api/prompts", response_model=PromptRecord)
def create_prompt(payload: PromptCreateRequest) -> PromptRecord:
    """새 프롬프트를 생성한다."""
    try:
        return PromptRecord(**create_prompt_entry(payload.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.patch("/api/prompts/{prompt_id}", response_model=PromptRecord)
def update_prompt(prompt_id: str, payload: PromptUpdateRequest) -> PromptRecord:
    """기존 프롬프트에 새 버전을 추가한다."""
    try:
        return PromptRecord(**append_prompt_version(prompt_id, payload.model_dump()))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}", response_model=WorkflowRunRecord)
def get_saved_workflow_run(run_id: str) -> WorkflowRunRecord:
    """저장된 workflow trace/report 스냅샷을 반환한다."""
    item = get_workflow_run(run_id)
    if not item:
        raise HTTPException(status_code=404, detail="Workflow run not found.")
    return WorkflowRunRecord(**item)


@app.get("/api/runs", response_model=List[WorkflowRunSummary])
def list_saved_workflow_runs(limit: int = 20) -> List[WorkflowRunSummary]:
    """최근 workflow run 목록을 반환한다."""
    safe_limit = max(1, min(limit, 100))
    return [WorkflowRunSummary(**item) for item in list_workflow_runs(safe_limit)]


@app.get("/api/conversation/events", response_model=List[ConversationEventRecord])
def get_conversation_events(request: Request) -> List[ConversationEventRecord]:
    """현재 세션의 대화 타임라인 이벤트를 시간순으로 반환한다."""
    return [
        ConversationEventRecord(**item)
        for item in list_conversation_events(get_conversation_id(request))
    ]


@app.get("/api/conversations", response_model=List[ConversationSummaryRecord])
def get_conversation_summaries(request: Request, limit: int = 50) -> List[ConversationSummaryRecord]:
    """최근 대화 세션 목록을 반환한다."""
    current_id = get_conversation_id(request)
    safe_limit = max(1, min(limit, 100))
    return [
        ConversationSummaryRecord(**item, current=item["conversation_id"] == current_id)
        for item in list_conversation_summaries(safe_limit)
    ]


@app.post("/api/conversations/select", response_model=ConversationSummaryRecord)
def select_conversation(request: Request, payload: ConversationSelectRequest) -> ConversationSummaryRecord:
    """기존 대화 세션 하나를 현재 브라우저 세션에 연결한다."""
    summary = next(
        (item for item in list_conversation_summaries(200) if item["conversation_id"] == payload.conversation_id),
        None,
    )
    if not summary:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    request.session["conversation_id"] = payload.conversation_id
    return ConversationSummaryRecord(**summary, current=True)


@app.post("/api/conversation/reset")
def reset_conversation(request: Request) -> Dict[str, str]:
    """세션의 conversation_id를 새로 발급해 새 타임라인을 시작한다."""
    request.session["conversation_id"] = str(uuid.uuid4())
    return {"conversation_id": request.session["conversation_id"]}


@app.post("/api/prompts/{prompt_id}/activate-version", response_model=PromptRecord)
def activate_prompt_version(prompt_id: str, payload: PromptActivateVersionRequest) -> PromptRecord:
    """특정 버전을 다시 활성 프롬프트로 전환한다."""
    try:
        return PromptRecord(**activate_prompt_version_store(prompt_id, payload.version))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/prompts/{prompt_id}", response_model=PromptRecord)
def delete_prompt(prompt_id: str) -> PromptRecord:
    """프롬프트 정의 전체를 삭제한다."""
    try:
        return PromptRecord(**delete_prompt_entry(prompt_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/auth/openai")
def openai_oauth_start(request: Request) -> RedirectResponse:
    """OpenAI GUI OAuth 로그인을 시작하고 authorize URL로 리다이렉트한다."""
    ensure_loopback_server()
    try:
        client_id = get_openai_oauth_client_id()
        redirect_uri = get_openai_redirect_uri()
    except RuntimeError as exc:
        request.session["auth_error"] = str(exc)
        return RedirectResponse(url=f"{get_frontend_callback_redirect()}&status=error", status_code=302)

    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = build_pkce_challenge(verifier)

    save_pending_oauth(
        {
            "state": state,
            "verifier": verifier,
            "created_at": int(time.time()),
        }
    )

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": OPENAI_OAUTH_SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "codex_cli_rs",
    }

    return RedirectResponse(url=f"{OPENAI_OAUTH_AUTHORIZE_URL}?{urlencode(params)}", status_code=302)


@app.get("/api/auth/openai/callback")
async def openai_oauth_callback(request: Request, code: str, state: str) -> RedirectResponse:
    """메인 백엔드 callback 경로를 공통 OAuth 완료 처리로 연결한다."""
    return await complete_openai_oauth(request, code, state)


@loopback_app.get("/auth/callback")
async def loopback_oauth_callback(request: Request, code: str, state: str) -> RedirectResponse:
    """Loopback callback 경로를 공통 OAuth 완료 처리로 연결한다."""
    return await complete_openai_oauth(request, code, state)


async def complete_openai_oauth(request: Request, code: str, state: str) -> RedirectResponse:
    """authorization code를 token으로 교환하고 로컬 auth profile에 저장한다."""
    callback_redirect = get_frontend_callback_redirect()
    try:
        client_id = get_openai_oauth_client_id()
        redirect_uri = get_openai_redirect_uri()
    except RuntimeError as exc:
        request.session["auth_error"] = str(exc)
        return RedirectResponse(url=f"{callback_redirect}&status=error", status_code=302)

    pending = load_pending_oauth()
    expected_state = pending.get("state")
    verifier = pending.get("verifier")

    if not expected_state or state != expected_state or not verifier:
        request.session["auth_error"] = "OAuth state verification failed."
        return RedirectResponse(url=f"{callback_redirect}&status=error", status_code=302)

    token_payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            token_response = await client.post(
                OPENAI_OAUTH_TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=token_payload,
            )
            token_response.raise_for_status()
    except httpx.HTTPError as exc:
        request.session["auth_error"] = f"OAuth token exchange failed: {exc}"
        return RedirectResponse(url=f"{callback_redirect}&status=error", status_code=302)

    token_data = token_response.json()
    profile = build_oauth_profile(token_data)
    save_auth_profile(profile)
    request.session["auth"] = {
        "provider": "openai-codex",
        "profile_id": profile["profileId"],
    }
    request.session.pop("auth_error", None)
    clear_pending_oauth()

    return RedirectResponse(url=f"{callback_redirect}&status=success", status_code=302)


@app.get("/api/auth/status", response_model=AuthStatusResponse)
def auth_status(request: Request) -> AuthStatusResponse:
    """현재 브라우저 세션의 인증 상태를 조회한다."""
    session_auth = request.session.get("auth") or {}
    profile_id = session_auth.get("profile_id")

    if not profile_id:
        return AuthStatusResponse(
            authenticated=False,
            error=request.session.get("auth_error"),
        )

    try:
        store = load_auth_profiles()
        profile = resolve_profile(store, profile_id)
        if not profile:
            return AuthStatusResponse(
                authenticated=False,
                error=request.session.get("auth_error"),
            )

        credential = ensure_fresh_profile(profile)
        request.session["auth"] = {
            "provider": "openai-codex",
            "profile_id": profile["profileId"],
        }
        return AuthStatusResponse(
            authenticated=True,
            provider=credential.get("provider"),
            profile_id=profile["profileId"],
            account_id=credential.get("accountId"),
            email=credential.get("email"),
            name=credential.get("email"),
            expires_at=credential.get("expires"),
        )
    except RuntimeError as exc:
        request.session["auth_error"] = str(exc)
        return AuthStatusResponse(authenticated=False, error=str(exc))


@app.post("/api/auth/logout", response_model=AuthStatusResponse)
def auth_logout(request: Request) -> AuthStatusResponse:
    """현재 세션과 auth profile을 정리해 로그아웃 처리한다."""
    profile_id = (request.session.get("auth") or {}).get("profile_id")
    request.session.pop("auth", None)
    request.session.pop("auth_error", None)
    if profile_id:
        remove_auth_profile(profile_id)
    return AuthStatusResponse(authenticated=False)


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: Request, payload: ChatRequest) -> ChatResponse:
    """질문/지령 입력을 받아 일반 답변 또는 플랜 생성으로 분기한다."""
    try:
        credential = get_active_openai_credential(request)
    except RuntimeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    model = os.getenv("CODEX_CHAT_MODEL", "default")
    message = payload.message.strip()
    append_message_event(request, "user_message", message)
    mcps = load_mcp_catalog()
    adjudication = await adjudicate_intent(
        model=model,
        message=message,
        mcp_catalog=[mcp.model_dump() for mcp in mcps],
        conversation=payload.conversation,
    )
    resolution = await resolve_capability(
        model=model,
        message=message,
        adjudication=adjudication,
        mcp_catalog=[mcp.model_dump() for mcp in mcps],
    )

    if resolution.mode == "mcp_action":
        capability_map = build_planner_capability_map(mcps)
        planner_context = {"trace": []}
        run_id = str(uuid.uuid4())
        runtime_plan = await get_planner().build_plan(
            command=message,
            soul=read_soul_prompt(),
            mcp_catalog=capability_map,
            detailed=False,
            context=planner_context,
        )
        normalized_plan = normalize_runtime_plan(runtime_plan, planner_context.get("trace", []))
        plan = normalized_plan_to_draft(normalized_plan)
        upsert_workflow_run(
            run_id,
            phase="review",
            command_text=message,
            planner_type=normalized_plan.planner_metadata.planner_type,
            fallback_used=normalized_plan.planner_metadata.fallback_used,
            plan=plan.model_dump(),
        )
        replace_workflow_trace(run_id, planner_context.get("trace", []))
        workflow = WorkflowResponse(
            phase="review",
            approval="pending",
            message="지령을 분석해 MCP-aware 플랜 초안을 생성했습니다. 검토 후 승인하거나 수정하십시오.",
            plan=plan,
            tasks=[],
            mcps=mcps,
            trace=planner_context.get("trace", []),
            run_id=run_id,
        )
        append_message_event(request, "assistant_message", "지령을 분석해 MCP-aware 플랜 초안을 생성했습니다. 아래 단계를 검토해 주십시오.")
        append_workflow_snapshot_event(
            request,
            phase="review",
            approval="pending",
            plan=plan,
            tasks=[],
            execution_log=[],
            execution_report=None,
            trace=planner_context.get("trace", []),
            run_id=run_id,
        )
        return ChatResponse(
            reply="지령을 분석해 MCP-aware 플랜 초안을 생성했습니다. 아래 단계를 검토해 주십시오.",
            response_id=None,
            model=model,
            mode="plan",
            workflow=workflow.model_dump(),
        )

    if resolution.mode == "mcp_retrieval":
        try:
            tool_response = await TOOL_ANSWER_RUNTIME.answer_question(
                model=model,
                message=message,
                required_capabilities=resolution.required_capabilities,
                mcp_catalog=[mcp.model_dump() for mcp in mcps],
            )
        except RuntimeError as exc:
            reply = build_decline_from_resolution(
                resolution.model_copy(update={"reason": str(exc)})
            )
            append_message_event(request, "assistant_message", reply)
            return ChatResponse(reply=reply, response_id=None, model=model, mode="decline")

        append_message_event(request, "assistant_message", tool_response["reply"])
        return ChatResponse(
            reply=tool_response["reply"],
            response_id=tool_response.get("response_id"),
            model=model,
            mode="tool_answer",
        )

    if resolution.mode == "decline":
        reply = build_decline_from_resolution(resolution)
        append_message_event(request, "assistant_message", reply)
        return ChatResponse(reply=reply, response_id=None, model=model, mode="decline")

    try:
        reply, response_id = await create_chat_response(
            model=model,
            message=message,
            previous_response_id=payload.previous_response_id,
            conversation=payload.conversation,
            mcps=mcps,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    append_message_event(request, "assistant_message", reply)
    return ChatResponse(reply=reply, response_id=response_id, model=model, mode="answer")


@app.post("/api/command", response_model=WorkflowResponse)
async def create_plan(request: Request, payload: CommandRequest) -> WorkflowResponse:
    """지령을 받아 승인 전 플랜을 생성한다."""
    command = payload.command.strip()
    mcps = load_mcp_catalog()
    capability_map = build_planner_capability_map(mcps)
    planner_context = {"trace": []}
    run_id = str(uuid.uuid4())
    append_message_event(request, "user_message", command)
    runtime_plan = await get_planner().build_plan(
        command=command,
        soul=read_soul_prompt(),
        mcp_catalog=capability_map,
        detailed=False,
        context=planner_context,
    )
    normalized_plan = normalize_runtime_plan(runtime_plan, planner_context.get("trace", []))
    plan = normalized_plan_to_draft(normalized_plan)
    upsert_workflow_run(
        run_id,
        phase="review",
        command_text=command,
        planner_type=normalized_plan.planner_metadata.planner_type,
        fallback_used=normalized_plan.planner_metadata.fallback_used,
        plan=plan.model_dump(),
    )
    replace_workflow_trace(run_id, planner_context.get("trace", []))
    append_message_event(request, "assistant_message", "지령을 분석해 플랜 초안을 생성했습니다. 검토 후 승인하거나 수정하십시오.")
    append_workflow_snapshot_event(
        request,
        phase="review",
        approval="pending",
        plan=plan,
        tasks=[],
        execution_log=[],
        execution_report=None,
        trace=planner_context.get("trace", []),
        run_id=run_id,
    )
    return WorkflowResponse(
        phase="review",
        approval="pending",
        message="지령을 분석해 플랜 초안을 생성했습니다. 검토 후 승인하거나 수정하십시오.",
        plan=plan,
        tasks=[],
        mcps=mcps,
        trace=planner_context.get("trace", []),
        run_id=run_id,
    )


@app.post("/api/review", response_model=WorkflowResponse)
async def revise_plan(request: Request, payload: ReviewRequest) -> WorkflowResponse:
    """플랜 세분화 요청을 받아 더 자세한 플랜을 생성한다."""
    command = payload.command.strip()
    mcps = load_mcp_catalog()
    capability_map = build_planner_capability_map(mcps)
    planner_context = {"trace": []}
    run_id = str(uuid.uuid4())
    runtime_plan = await get_planner().build_plan(
        command=command,
        soul=read_soul_prompt(),
        mcp_catalog=capability_map,
        detailed=True,
        context=planner_context,
    )
    normalized_plan = normalize_runtime_plan(runtime_plan, planner_context.get("trace", []))
    plan = normalized_plan_to_draft(normalized_plan)
    upsert_workflow_run(
        run_id,
        phase="review",
        command_text=command,
        planner_type=normalized_plan.planner_metadata.planner_type,
        fallback_used=normalized_plan.planner_metadata.fallback_used,
        plan=plan.model_dump(),
    )
    replace_workflow_trace(run_id, planner_context.get("trace", []))
    append_message_event(request, "system_message", f"플랜 수정 요청 {payload.revision_count}회를 반영했습니다.")
    append_workflow_snapshot_event(
        request,
        phase="review",
        approval="pending",
        plan=plan,
        tasks=[],
        execution_log=[],
        execution_report=None,
        trace=planner_context.get("trace", []),
        run_id=run_id,
    )
    return WorkflowResponse(
        phase="review",
        approval="pending",
        message=f"수정 요청 {payload.revision_count}회를 반영해 플랜을 더 세분화했습니다.",
        plan=plan,
        tasks=[],
        mcps=mcps,
        trace=planner_context.get("trace", []),
        run_id=run_id,
    )


@app.post("/api/approve", response_model=WorkflowResponse)
def approve_plan(request: Request, payload: ApproveRequest) -> WorkflowResponse:
    """승인된 플랜을 실행 태스크 목록으로 확정한다."""
    mcps = load_mcp_catalog()
    normalized_plan = draft_to_normalized_plan(payload.plan)
    compiled_tasks = compile_tasks(normalized_plan, [mcp.model_dump() for mcp in mcps])
    trace = [
        {"event": "task_compiler.started", "task_draft_count": len(normalized_plan.tasks_draft)},
        {
            "event": "task_compiler.completed",
            "task_count": len(compiled_tasks),
            "planner_type": normalized_plan.planner_metadata.planner_type,
            "fallback_used": normalized_plan.planner_metadata.fallback_used,
        },
    ]
    tasks = [
        TaskItem(
            id=task.id,
            title=task.title,
            status="queued",
            mcp_ids=task.mcp_ids,
            selected_mcp_id=task.selected_mcp_id,
            tool_name=task.tool_name,
            tool_arguments=task.tool_arguments,
            expected_result=task.expected_result,
        )
        for task in compiled_tasks
    ]
    run_id = payload.run_id or str(uuid.uuid4())
    upsert_workflow_run(
        run_id,
        phase="tasking",
        command_text=payload.plan.objective,
        planner_type=normalized_plan.planner_metadata.planner_type,
        fallback_used=normalized_plan.planner_metadata.fallback_used,
        plan=payload.plan.model_dump(),
        tasks=[task.model_dump() for task in tasks],
    )
    replace_workflow_trace(run_id, trace)
    append_message_event(request, "system_message", "플랜 승인이 기록되었습니다.")
    append_workflow_snapshot_event(
        request,
        phase="tasking",
        approval="approved",
        plan=payload.plan,
        tasks=tasks,
        execution_log=[],
        execution_report=None,
        trace=trace,
        run_id=run_id,
    )
    return WorkflowResponse(
        phase="tasking",
        approval="approved",
        message="플랜 승인이 기록되었습니다. 승인된 플랜을 실행 태스크로 확정했습니다.",
        plan=payload.plan,
        tasks=tasks,
        mcps=mcps,
        trace=trace,
        run_id=run_id,
    )


@app.post("/api/execute", response_model=ExecuteResponse)
async def execute_workflow(request: Request, payload: ExecuteRequest) -> ExecuteResponse:
    """확정된 태스크를 실행하고 로그/보고를 반환한다."""
    execution_context = {"mcp_catalog": [mcp.model_dump() for mcp in load_mcp_catalog()], "trace": []}
    runtime_result = await get_executor().execute_tasks(
        to_runtime_tasks(payload.tasks),
        context=execution_context,
    )
    updated_tasks: List[TaskItem] = []
    for index, task in enumerate(payload.tasks):
        status = runtime_result.task_statuses[index] if index < len(runtime_result.task_statuses) else "done"
        updated_tasks.append(
            TaskItem(
                id=task.id,
                title=task.title,
                status="done" if status == "done" else "failed",
                mcp_ids=task.mcp_ids,
                selected_mcp_id=task.selected_mcp_id,
                tool_name=task.tool_name,
                tool_arguments=task.tool_arguments,
                expected_result=task.expected_result,
            )
        )
    run_id = payload.run_id or str(uuid.uuid4())
    upsert_workflow_run(
        run_id,
        phase="completed",
        planner_type=None,
        fallback_used=False,
        tasks=[task.model_dump() for task in updated_tasks],
        report=runtime_result.report,
    )
    replace_workflow_trace(run_id, execution_context.get("trace", []))
    append_message_event(request, "system_message", "실행 완료. 결과를 보고합니다.")
    append_workflow_snapshot_event(
        request,
        phase="completed",
        approval="approved",
        plan=None,
        tasks=updated_tasks,
        execution_log=runtime_result.execution_log,
        execution_report=runtime_result.report,
        trace=execution_context.get("trace", []),
        run_id=run_id,
    )
    append_message_event(request, "assistant_message", build_assistant_report_reply(runtime_result.report))
    return ExecuteResponse(
        phase="completed",
        message="승인된 실행 태스크를 순서대로 수행하고 결과 보고까지 마쳤습니다.",
        tasks=updated_tasks,
        execution_log=runtime_result.execution_log,
        execution_report=runtime_result.report,
        trace=execution_context.get("trace", []),
        run_id=run_id,
    )


def build_plan_draft(command: str, items: List[ProposedTask]) -> PlanDraft:
    """command와 태스크 목록으로 PlanDraft를 조립한다."""
    return PlanDraft(
        objective=command.strip(),
        summary="요청된 목표를 수행하기 위한 승인 전 실행 계획입니다.",
        proposed_tasks=items,
    )


def parse_proposed_tasks(items: List[Dict[str, Any]]) -> List[ProposedTask]:
    """planner 응답 JSON을 ProposedTask 목록으로 변환한다."""
    parsed: List[ProposedTask] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("step") or "").strip()
        if not title:
            continue
        parsed.append(
            ProposedTask(
                title=title,
                rationale=str(item.get("rationale", "")).strip(),
                recommended_mcp_ids=[str(mcp_id) for mcp_id in item.get("recommended_mcp_ids", []) if str(mcp_id).strip()],
                selected_mcp_id=(str(item.get("selected_mcp_id", "")).strip() or None),
                tool_name=(str(item.get("tool_name", "")).strip() or None),
                tool_arguments=item.get("tool_arguments", {}) if isinstance(item.get("tool_arguments"), dict) else {},
                expected_result=str(item.get("expected_result", "")).strip(),
            )
        )
    return parsed


async def build_plan(command: str, detailed: bool, mcps: List[MpcDefinition]) -> PlanDraft:
    """레거시 planner 서비스 경로를 통해 플랜을 생성한다."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                PLANNER_MCP_URL,
                json={
                    "command": command,
                    "detailed": detailed,
                    "mcps": [mcp.model_dump() for mcp in mcps],
                },
            )
            response.raise_for_status()
        payload = response.json()
        items = payload.get("plan")
        if isinstance(items, list) and items:
            normalized = normalize_plan_steps(parse_proposed_tasks(items))
            return build_plan_draft(command, normalized)
    except Exception:
        pass

    return build_plan_draft(command, normalize_plan_steps(fallback_plan(command, detailed)))


def is_reporting_only_step(item: ProposedTask) -> bool:
    """보고/정리용 메타 단계인지 판정한다."""
    normalized = item.title.lower()
    markers = ["정리", "요약", "보고", "보여", "전달", "출력 형식", "간단한 보고"]
    return any(marker in normalized for marker in markers)


def normalize_plan_steps(plan: List[ProposedTask]) -> List[ProposedTask]:
    """중복 또는 메타 단계를 제거해 실행 가능한 플랜으로 정규화한다."""
    if not plan:
        return plan

    has_executable_step = any(item.tool_name for item in plan)
    normalized: List[ProposedTask] = []
    seen_signatures: set[str] = set()

    for item in plan:
        signature = json.dumps(
            {
                "selected_mcp_id": item.selected_mcp_id,
                "tool_name": item.tool_name,
                "tool_arguments": item.tool_arguments,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

        if item.tool_name and signature in seen_signatures:
            continue
        if not item.tool_name and has_executable_step and is_reporting_only_step(item):
            continue

        if item.tool_name:
            seen_signatures.add(signature)
        normalized.append(item)

    return normalized or plan[:1]


    if "완료 기준" in normalized or "검증" in normalized:
        add("browser")
        add("docs")

    if not ids:
        add("planner")
        add("filesystem")

    return ids


class StdioMcpClient:
    """메인 파일 안에 남아 있는 레거시 stdio MCP 클라이언트."""

    def __init__(self, command: List[str]) -> None:
        """실행할 MCP 프로세스 명령을 저장한다."""
        self.command = command
        self.process: Optional[asyncio.subprocess.Process] = None
        self._request_id = 0

    async def __aenter__(self) -> "StdioMcpClient":
        """프로세스를 띄우고 initialize까지 마친 뒤 client를 반환한다."""
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """MCP 프로세스를 종료한다."""
        if not self.process:
            return
        if self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()

    async def initialize(self) -> None:
        """MCP initialize/initialized 핸드셰이크를 수행한다."""
        await self.request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "jarvis", "version": "0.1.0"},
            },
        )
        await self.notify("notifications/initialized", {})

    async def notify(self, method: str, params: Dict[str, Any]) -> None:
        """응답이 필요 없는 MCP notification을 전송한다."""
        if not self.process or not self.process.stdin:
            raise RuntimeError("MCP process is not running.")
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.process.stdin.drain()

    async def request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """응답이 필요한 MCP request를 전송하고 결과를 기다린다."""
        if not self.process or not self.process.stdin or not self.process.stdout:
            raise RuntimeError("MCP process is not running.")

        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.process.stdin.drain()

        while True:
            line = await self.process.stdout.readline()
            if not line:
                stderr = ""
                if self.process.stderr:
                    stderr = (await self.process.stderr.read()).decode("utf-8", errors="ignore").strip()
                raise RuntimeError(f"MCP process closed unexpectedly. {stderr}".strip())

            message = json.loads(line.decode("utf-8"))
            if message.get("id") != self._request_id:
                continue
            if "error" in message:
                raise RuntimeError(str(message["error"]))
            return message.get("result", {})


async def call_filesystem_mcp(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """레거시 경로에서 Filesystem MCP tool을 직접 호출한다."""
    if not FILESYSTEM_MCP_BIN.exists():
        raise RuntimeError("Filesystem MCP binary is not installed.")

    command = [str(FILESYSTEM_MCP_BIN), str(PROJECT_ROOT), str(HOME_ROOT)]
    async with StdioMcpClient(command) as client:
        return await client.request("tools/call", {"name": tool_name, "arguments": arguments})


def extract_tool_text(result: Dict[str, Any]) -> str:
    """MCP raw 응답에서 사람이 읽을 텍스트를 추출한다."""
    content = result.get("content")
    if isinstance(content, list):
        texts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
        joined = "\n".join(text for text in texts if text.strip())
        if joined.strip():
            return joined.strip()

    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        content_text = structured.get("content")
        if isinstance(content_text, str) and content_text.strip():
            return content_text.strip()

    return json.dumps(result, ensure_ascii=False)


def build_execution_report(
    tasks: List[TaskItem],
    used_mcp_names: List[str],
    evidence: List[str],
    findings: List[str],
    result_items: List[str],
) -> Dict[str, Any]:
    """태스크 실행 결과를 UI 보고 카드 구조로 조합한다."""
    task_titles = [f"Task {task.id}. {task.title}" for task in tasks]
    if findings:
        conclusion = "요청한 실행은 완료되었고, 아래 발견 사항을 기준으로 후속 판단이 가능한 상태입니다."
    else:
        conclusion = "요청한 실행은 완료되었지만, 아직 의미 있는 발견 사항을 구조화하지 못했습니다."

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


async def execute_tasks(tasks: List[TaskItem]) -> tuple[List[TaskItem], List[str], Dict[str, Any]]:
    """레거시 실행 경로에서 태스크 목록을 순차 실행한다."""
    updated_tasks: List[TaskItem] = []
    execution_log: List[str] = []
    evidence: List[str] = []
    findings: List[str] = []
    result_items: List[str] = []

    for task in tasks:
        task_status = "done"
        updated_tasks.append(
            TaskItem(
                id=task.id,
                title=task.title,
                status=task_status,
                mcp_ids=task.mcp_ids,
                selected_mcp_id=task.selected_mcp_id,
                tool_name=task.tool_name,
                tool_arguments=task.tool_arguments,
                expected_result=task.expected_result,
            )
        )
        execution_log.append(f"Task {task.id}: {task.title}")
        selected_mcp_id = task.selected_mcp_id or (task.mcp_ids[0] if task.mcp_ids else None)
        tool_name = task.tool_name
        try:
            if selected_mcp_id == "filesystem" and tool_name in {"list_directory", "directory_tree", "read_text_file"}:
                skill_result = await execute_filesystem_task(
                    task=task,
                    call_tool=call_filesystem_mcp,
                    extract_tool_text=extract_tool_text,
                    home_root=HOME_ROOT,
                    project_root=PROJECT_ROOT,
                )
                evidence.extend(skill_result["evidence"])
                findings.extend(skill_result["findings"])
                if skill_result["result_items"]:
                    result_items = skill_result["result_items"]
                execution_log.append(skill_result["log"])
                continue

            evidence.append(
                f"실행 계획 미확정: {task.title} (selected_mcp_id={selected_mcp_id or '없음'}, tool_name={tool_name or '없음'})"
            )
            findings.append(f"{task.title}: MCP와 tool이 구조화되어 내려오지 않아 실제 호출 근거를 만들지 못했습니다.")
        except Exception as exc:
            evidence.append(f"실행 실패: {task.title} - {exc}")
            findings.append(f"{task.title}: 실행 중 오류가 발생했습니다. 오류 내용: {exc}")
            execution_log.append(f"실행 실패: {task.title}")

    used_mcp_ids = list(dict.fromkeys(mcp_id for task in tasks for mcp_id in task.mcp_ids))
    used_mcp_names = [mcp.name for mcp in load_mcp_catalog() if mcp.id in used_mcp_ids]
    execution_report = build_execution_report(tasks, used_mcp_names, evidence, findings, result_items)

    execution_log.append("모든 태스크 수행이 끝났습니다.")
    return updated_tasks, execution_log, execution_report


def get_openai_oauth_client_id() -> str:
    """환경변수에서 OpenAI OAuth client id를 읽는다."""
    client_id = os.getenv("OPENAI_OAUTH_CLIENT_ID")
    if not client_id:
        raise RuntimeError("OPENAI_OAUTH_CLIENT_ID is not configured.")
    return client_id


def get_openai_redirect_uri() -> str:
    """현재 OAuth callback redirect URI를 반환한다."""
    default_uri = f"http://{LOOPBACK_CALLBACK_HOST}:{LOOPBACK_CALLBACK_PORT}/auth/callback"
    return os.getenv("OPENAI_OAUTH_REDIRECT_URI", default_uri)


def get_frontend_callback_redirect() -> str:
    """OAuth 완료 후 프론트로 되돌아갈 redirect URL을 만든다."""
    return os.getenv("FRONTEND_APP_URL", "http://127.0.0.1:7400") + "/?auth=complete"


def build_pkce_challenge(verifier: str) -> str:
    """PKCE verifier로 code challenge를 생성한다."""
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def get_active_openai_credential(request: Request) -> Dict[str, Any]:
    """세션에 연결된 auth profile의 유효한 credential을 반환한다."""
    session_auth = request.session.get("auth") or {}
    profile_id = session_auth.get("profile_id")
    if not profile_id:
        raise RuntimeError("OpenAI login is required before chat requests can be processed.")

    store = load_auth_profiles()
    profile = resolve_profile(store, profile_id)
    if not profile:
        raise RuntimeError("OpenAI login is required before chat requests can be processed.")

    credential = ensure_fresh_profile(profile)
    request.session["auth"] = {
        "provider": "openai-codex",
        "profile_id": profile["profileId"],
    }
    return credential


def fallback_chat_intent(message: str, conversation: Optional[List[Dict[str, str]]] = None) -> Literal["question", "command"]:
    """LLM 분류 실패 시 질문/지령을 대략적으로 판정한다."""
    normalized = message.strip().lower()
    history_text = " ".join(str(item.get("content", "")) for item in (conversation or [])[-6:]).lower()
    command_markers = ["구현", "만들", "고쳐", "정리", "설계", "추가", "삭제", "수정", "작성", "개발", "리팩토링", "열어", "띄워", "실행", "이동", "클릭", "보여줘"]
    polite_command_markers = ["해줘", "해라"]
    followup_markers = ["보여", "보여줘", "다시", "설명", "어디", "뭐였", "목록", "리스트"]
    information_markers = [
        "날씨",
        "시간",
        "날짜",
        "환율",
        "주가",
        "뜻",
        "의미",
        "정의",
        "요약",
        "설명",
        "비교",
        "추천",
        "알려줘",
        "알려주",
        "조회",
        "찾아",
        "뭐야",
        "무엇",
        "왜",
        "어떻게",
    ]
    if any(marker in normalized for marker in followup_markers) and ("보고합니다" in history_text or "폴더 목록" in history_text or "실행 근거" in history_text):
        return "question"
    if "?" in normalized:
        return "question"
    if any(marker in normalized for marker in information_markers) and not any(marker in normalized for marker in command_markers):
        return "question"
    if any(marker in normalized for marker in command_markers):
        return "command"
    if any(marker in normalized for marker in polite_command_markers) and not any(marker in normalized for marker in information_markers):
        return "command"
    return "question"


async def classify_chat_intent(
    model: str,
    message: str,
    mcps: List[MpcDefinition],
    conversation: List[Dict[str, str]],
) -> Literal["question", "command"]:
    """Bridge를 통해 사용자의 입력이 질문인지 지령인지 분류한다."""
    template = get_prompt_content(
        "intent_classifier",
        fallback=(
            "너는 사용자의 입력이 '일반 질문'인지 '실행해야 할 지령'인지 분류하는 분류기다.\n"
            "반드시 JSON 객체 하나만 출력한다.\n"
            '형식: {"intent":"question"} 또는 {"intent":"command"}\n'
            "판정 기준:\n"
            "- question: 설명, 정의, 비교, 의견, 원인 질문, 사실 조회, 요약 요청\n"
            "- command: 무언가를 만들기/고치기/설계하기/진행하기를 요구하는 지시\n"
            "- 말투가 명령형이어도 실질이 정보 질의면 question이다. 예: 날씨 알려줘라, 의미를 설명해라, 최신 뉴스를 요약해줘.\n"
            "- MCP나 외부 도구가 꼭 없어도 LLM이 바로 답할 수 있는 요청은 question이다.\n\n"
            "- 직전 대화의 실행 결과를 다시 보여달라거나 설명해달라는 후속 발화는 question이다.\n\n"
            "[대화 히스토리]\n{{conversation}}\n\n"
            "[현재 MCP REGISTRY]\n{{mcps}}\n\n"
            "[사용자 입력]\n{{message}}"
        ),
    )
    prompt = render_prompt_template(
        template,
        {
            "conversation": format_conversation_history(conversation),
            "mcps": json.dumps(build_planner_capability_map(mcps), ensure_ascii=False, indent=2),
            "message": message,
        },
    )
    raw, _ = await invoke_bridge_text(model=model, prompt=prompt)
    parsed = extract_json_object(raw)
    intent = str(parsed.get("intent", "")).strip().lower()
    if intent not in {"question", "command"}:
        raise RuntimeError("intent classification failed")
    return intent  # type: ignore[return-value]


async def create_chat_response(
    model: str,
    message: str,
    previous_response_id: Optional[str],
    conversation: List[Dict[str, str]],
    mcps: List[MpcDefinition],
) -> tuple[str, Optional[str]]:
    """일반 질문에 대한 JARVIS 답변을 생성한다."""
    _ = previous_response_id
    soul_prompt = read_soul_prompt()
    mcp_status_prompt = serialize_mcp_runtime_status(mcps)
    template = get_prompt_content(
        "chat_system",
        fallback=(
            "너는 JARVIS의 채팅 처리 엔진이다. "
            "항상 한국어로 답하고, 간결하지만 실제로 도움이 되게 답해라. "
            "코드/개발 질문이면 실무적으로 답하고, 모르면 추측하지 말고 부족한 점을 짧게 밝혀라.\n"
            "특히 MCP 존재 여부, 활성 상태, 실행 가능 여부를 묻는 질문에는 추측하지 말고 아래 현재 MCP 상태를 근거로만 답해라.\n"
            "등록은 되어 있지만 설정이 부족한 MCP는 '등록됨/미설정'으로 명확히 구분해서 말해라.\n\n"
            "[SOUL]\n{{soul}}\n\n"
            "[현재 MCP 상태]\n{{mcp_status}}\n\n"
            "[대화 히스토리]\n{{conversation}}\n\n"
            "[사용자 질문]\n{{message}}"
        ),
    )
    conversation_prompt = format_conversation_history(conversation)
    full_prompt = render_prompt_template(
        template,
        {
            "soul": soul_prompt,
            "mcp_status": mcp_status_prompt,
            "conversation": conversation_prompt,
            "message": message,
        },
    )
    return await invoke_bridge_text(model=model, prompt=full_prompt)


def serialize_mcp_runtime_status(mcps: List[MpcDefinition]) -> str:
    """일반 답변 프롬프트에 넣을 현재 MCP 등록/설정 상태 요약을 만든다."""
    items: List[Dict[str, Any]] = []
    for mcp in mcps:
        entry: Dict[str, Any] = {
            "id": mcp.id,
            "name": mcp.name,
            "enabled": True,
            "registered": True,
            "transport": getattr(mcp, "transport", None),
            "auth_required": getattr(mcp, "auth_required", False),
        }
        if mcp.id == "filesystem":
            entry["runtime_status"] = "ready" if FILESYSTEM_MCP_BIN.exists() else "missing_binary"
        elif mcp.id == "playwright":
            code_home = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex")))
            playwright_cli = code_home / "skills" / "playwright" / "scripts" / "playwright_cli.sh"
            entry["runtime_status"] = "ready" if playwright_cli.exists() else "missing_wrapper"
        elif mcp.id == "korean_law":
            command_path = shutil.which(os.getenv("KOREAN_LAW_MCP_COMMAND", "korean-law-mcp") or "korean-law-mcp")
            law_oc = str(os.getenv("LAW_OC", "")).strip()
            if command_path and law_oc:
                entry["runtime_status"] = "ready"
            elif command_path and not law_oc:
                entry["runtime_status"] = "registered_but_missing_law_oc"
            else:
                entry["runtime_status"] = "registered_but_missing_command"
            entry["command_found"] = bool(command_path)
            entry["law_oc_set"] = bool(law_oc)
        else:
            entry["runtime_status"] = "registered"
        items.append(entry)
    return json.dumps(items, ensure_ascii=False, indent=2)


def format_conversation_history(conversation: List[Dict[str, str]]) -> str:
    """최근 대화 히스토리를 prompt용 문자열로 정리한다."""
    if not conversation:
        return "이전 대화 없음"

    lines: List[str] = []
    for item in conversation[-12:]:
        role = str(item.get("role", "")).strip().lower()
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        if role == "user":
            lines.append(f"USER: {content}")
        elif role == "assistant":
            lines.append(f"ASSISTANT: {content}")
        elif role == "system":
            lines.append(f"SYSTEM: {content}")
    return "\n".join(lines) if lines else "이전 대화 없음"


def read_soul_prompt() -> str:
    """프로젝트 루트의 soul.md를 읽고 비어 있으면 기본값을 반환한다."""
    if not SOUL_PATH.exists():
        return "자비스는 항상 한국어로, 침착하고 예의 바른 존댓말로 답한다."

    content = SOUL_PATH.read_text().strip()
    if not content:
        return "자비스는 항상 한국어로, 침착하고 예의 바른 존댓말로 답한다."
    return content


def get_planner():
    """현재 설정 기준 planner runtime 구현체를 반환한다."""
    return get_planner_runtime(read_soul_prompt())


def get_executor():
    """현재 설정 기준 stable executor 구현체를 반환한다."""
    return get_executor_runtime()


def get_optional_openai_credential(request: Request) -> Optional[Dict[str, Any]]:
    """로그인되어 있으면 credential을, 아니면 None을 반환한다."""
    try:
        return get_active_openai_credential(request)
    except RuntimeError:
        return None


def ensure_loopback_server() -> None:
    """OAuth loopback callback 서버를 한 번만 백그라운드로 기동한다."""
    global _loopback_server_started
    with _loopback_server_lock:
        if _loopback_server_started:
            return

        def run_server() -> None:
            config = uvicorn.Config(
                loopback_app,
                host=LOOPBACK_CALLBACK_HOST,
                port=LOOPBACK_CALLBACK_PORT,
                log_level="warning",
            )
            server = uvicorn.Server(config)
            server.run()

        thread = threading.Thread(target=run_server, daemon=True, name="nicecodex-oauth-loopback")
        thread.start()
        _loopback_server_started = True


def decode_jwt_payload(token: str) -> Dict[str, Any]:
    """JWT access token의 payload를 서명 검증 없이 디코드한다."""
    parts = token.split(".")
    if len(parts) < 2:
        return {}

    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload + padding)
        import json

        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}




def build_oauth_profile(token_data: Dict[str, Any]) -> Dict[str, Any]:
    """token 교환 결과를 로컬 auth profile 저장 구조로 변환한다."""
    access_token = token_data.get("access_token")
    payload = decode_jwt_payload(access_token) if access_token else {}
    profile_email = payload.get("https://api.openai.com/profile", {}).get("email")
    account_id = payload.get("https://api.openai.com/auth", {}).get("chatgpt_account_id") or payload.get("sub")
    profile_id = f"openai-codex:{profile_email or account_id or 'default'}"
    expires_ms = token_data.get("expires_at")
    if not expires_ms:
        expires_ms = int(payload.get("exp", 0) * 1000) if payload.get("exp") else int((time.time() + 3600) * 1000)

    return {
        "profileId": profile_id,
        "credential": {
            "type": "oauth",
            "provider": "openai-codex",
            "access": access_token,
            "refresh": token_data.get("refresh_token"),
            "expires": expires_ms,
            "email": profile_email,
            "accountId": account_id,
        },
    }


def ensure_auth_store() -> None:
    """auth profile 저장 파일이 없으면 기본 구조로 생성한다."""
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    if not AUTH_PROFILES_PATH.exists():
        AUTH_PROFILES_PATH.write_text('{"profiles":{},"order":{"openai-codex":[]}}')


def ensure_pending_oauth_store() -> None:
    """OAuth 진행 중 임시 상태 파일이 없으면 생성한다."""
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    if not PENDING_OAUTH_PATH.exists():
        PENDING_OAUTH_PATH.write_text("{}")


def with_auth_store_lock(fn):
    """auth profile 파일에 대한 파일 잠금 래퍼."""
    ensure_auth_store()
    with AUTH_PROFILES_LOCK_PATH.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def with_pending_oauth_lock(fn):
    """pending OAuth 상태 파일에 대한 파일 잠금 래퍼."""
    ensure_pending_oauth_store()
    with PENDING_OAUTH_LOCK_PATH.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def load_auth_profiles() -> Dict[str, Any]:
    """로컬 auth profile 저장소를 읽는다."""
    def _load() -> Dict[str, Any]:
        ensure_auth_store()
        import json

        return json.loads(AUTH_PROFILES_PATH.read_text())

    return with_auth_store_lock(_load)


def load_pending_oauth() -> Dict[str, Any]:
    """진행 중인 OAuth state/code_verifier 저장소를 읽는다."""
    def _load() -> Dict[str, Any]:
        ensure_pending_oauth_store()
        import json

        return json.loads(PENDING_OAUTH_PATH.read_text())

    return with_pending_oauth_lock(_load)


def save_pending_oauth(payload: Dict[str, Any]) -> None:
    """진행 중 OAuth 상태를 파일에 기록한다."""
    def _save() -> None:
        import json

        ensure_pending_oauth_store()
        PENDING_OAUTH_PATH.write_text(json.dumps(payload, indent=2))

    with_pending_oauth_lock(_save)


def clear_pending_oauth() -> None:
    """진행 중 OAuth 상태를 비운다."""
    def _clear() -> None:
        ensure_pending_oauth_store()
        PENDING_OAUTH_PATH.write_text("{}")

    with_pending_oauth_lock(_clear)


def save_auth_profiles(store: Dict[str, Any]) -> None:
    """auth profile 저장소 전체를 파일에 기록한다."""
    def _save() -> None:
        import json

        ensure_auth_store()
        AUTH_PROFILES_PATH.write_text(json.dumps(store, indent=2))

    with_auth_store_lock(_save)


def save_auth_profile(profile: Dict[str, Any]) -> None:
    """단일 auth profile을 저장소에 병합 저장한다."""
    store = load_auth_profiles()
    profile_id = profile["profileId"]
    store.setdefault("profiles", {})[profile_id] = profile
    order = store.setdefault("order", {}).setdefault("openai-codex", [])
    if profile_id in order:
        order.remove(profile_id)
    order.insert(0, profile_id)
    save_auth_profiles(store)


def remove_auth_profile(profile_id: str) -> None:
    """주어진 profile id를 저장소에서 제거한다."""
    store = load_auth_profiles()
    store.setdefault("profiles", {}).pop(profile_id, None)
    order = store.setdefault("order", {}).setdefault("openai-codex", [])
    store["order"]["openai-codex"] = [item for item in order if item != profile_id]
    save_auth_profiles(store)


def resolve_profile(store: Dict[str, Any], profile_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """profile id로 저장소에서 단일 profile을 찾는다."""
    profiles = store.get("profiles", {})
    if not profile_id:
        return None
    return profiles.get(profile_id)


def ensure_fresh_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """만료 직전/만료된 OAuth credential이면 refresh 후 최신 credential을 반환한다."""
    credential = profile.get("credential", {})
    expires = credential.get("expires") or 0
    if expires > int(time.time() * 1000) + 60_000:
        return credential

    refresh_token = credential.get("refresh")
    if not refresh_token:
        raise RuntimeError("Stored OAuth profile is expired and has no refresh token.")

    token_data = refresh_openai_token(refresh_token)
    refreshed = build_oauth_profile(token_data)
    refreshed["profileId"] = profile["profileId"]
    if not refreshed["credential"].get("refresh"):
        refreshed["credential"]["refresh"] = refresh_token
    if not refreshed["credential"].get("email"):
        refreshed["credential"]["email"] = credential.get("email")
    if not refreshed["credential"].get("accountId"):
        refreshed["credential"]["accountId"] = credential.get("accountId")
    save_auth_profile(refreshed)
    return refreshed["credential"]


def refresh_openai_token(refresh_token: str) -> Dict[str, Any]:
    """refresh token으로 OpenAI OAuth access token을 갱신한다."""
    payload = {
        "grant_type": "refresh_token",
        "client_id": get_openai_oauth_client_id(),
        "refresh_token": refresh_token,
    }
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            OPENAI_OAUTH_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=payload,
        )
        response.raise_for_status()
        return response.json()
