from __future__ import annotations

import base64
import asyncio
import fcntl
import hashlib
import os
from pathlib import Path
import secrets
import threading
import time
import tempfile
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel, Field
import uvicorn

load_dotenv()


class MpcDefinition(BaseModel):
    id: str
    name: str
    scope: str
    description: str
    capabilities: List[str]
    expected_input: str
    expected_output: str


class CommandRequest(BaseModel):
    command: str = Field(..., min_length=1)


class ReviewRequest(BaseModel):
    command: str = Field(..., min_length=1)
    revision_count: int = Field(default=1, ge=1)


class ApproveRequest(BaseModel):
    plan: List[str] = Field(default_factory=list, min_length=1)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    previous_response_id: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    response_id: Optional[str] = None
    model: str


class TaskItem(BaseModel):
    id: int
    title: str
    status: Literal["queued", "in_progress", "done"] = "queued"
    mcp_ids: List[str]


class WorkflowResponse(BaseModel):
    phase: str
    approval: str
    message: str
    plan: List[str]
    tasks: List[TaskItem]
    mcps: List[MpcDefinition]


class AuthStatusResponse(BaseModel):
    authenticated: bool
    provider: Optional[str] = None
    profile_id: Optional[str] = None
    account_id: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    expires_at: Optional[int] = None
    error: Optional[str] = None


MCP_CATALOG = [
    MpcDefinition(
        id="planner",
        name="Planner MCP",
        scope="계획",
        description="지령을 구조화하고 실행 전 검토 가능한 플랜으로 변환합니다.",
        capabilities=["목표 분해", "우선순위 정리", "플랜 초안 생성"],
        expected_input="사용자 지령, 제약 조건, 확인 포인트",
        expected_output="플랜 단계, 리스크 포인트, 태스크 초안",
    ),
    MpcDefinition(
        id="memory",
        name="Memory MCP",
        scope="기억",
        description="세션 중 승인 상태와 의사결정 맥락을 유지합니다.",
        capabilities=["결정사항 보존", "수정 이력 추적", "세션 맥락 주입"],
        expected_input="이전 플랜, 수정 요청, 승인 이력",
        expected_output="지속 컨텍스트, 후속 판단 힌트",
    ),
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
        id="terminal",
        name="Terminal MCP",
        scope="실행",
        description="명령 실행, 테스트, 빌드, 로그 수집을 담당합니다.",
        capabilities=["명령 실행", "테스트 수행", "빌드 결과 확인"],
        expected_input="실행 명령, 환경 조건, 작업 디렉터리",
        expected_output="실행 결과, 로그, 오류 정보",
    ),
    MpcDefinition(
        id="browser",
        name="Browser MCP",
        scope="검증",
        description="UI 흐름과 렌더링 상태를 검증합니다.",
        capabilities=["시각 검증", "흐름 점검", "렌더링 확인"],
        expected_input="화면 대상, 검증 시나리오, 비교 기준",
        expected_output="검증 결과, 이슈 목록, 화면 상태",
    ),
    MpcDefinition(
        id="docs",
        name="Docs MCP",
        scope="참조",
        description="문서와 레퍼런스를 조회해 규격 판단을 보조합니다.",
        capabilities=["문서 조회", "규격 확인", "참조 요약"],
        expected_input="문서 대상, API 이름, 필요한 규격 포인트",
        expected_output="참조 정보, 요약 규격, 사용 가이드",
    ),
]


OPENAI_OAUTH_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_OAUTH_SCOPES = "openid profile email offline_access"
STATE_DIR = Path(os.getenv("NICECODEX_STATE_DIR", str(Path.home() / ".nicecodex")))
AGENT_DIR = STATE_DIR / "agent"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOUL_PATH = PROJECT_ROOT / "soul.md"
AUTH_PROFILES_PATH = AGENT_DIR / "auth-profiles.json"
AUTH_PROFILES_LOCK_PATH = AGENT_DIR / "auth-profiles.lock"
PENDING_OAUTH_PATH = AGENT_DIR / "pending-oauth.json"
PENDING_OAUTH_LOCK_PATH = AGENT_DIR / "pending-oauth.lock"
LOOPBACK_CALLBACK_HOST = os.getenv("OPENAI_OAUTH_LOOPBACK_HOST", "localhost")
LOOPBACK_CALLBACK_PORT = int(os.getenv("OPENAI_OAUTH_LOOPBACK_PORT", "1455"))

app = FastAPI(title="NiceCodex API")

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

loopback_app = FastAPI(title="NiceCodex OAuth Loopback")
loopback_app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "nicecodex-dev-secret"),
    same_site="lax",
    https_only=False,
)

_loopback_server_started = False
_loopback_server_lock = threading.Lock()


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/mcps", response_model=List[MpcDefinition])
def list_mcps() -> List[MpcDefinition]:
    return MCP_CATALOG


@app.get("/api/auth/openai")
def openai_oauth_start(request: Request) -> RedirectResponse:
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
    }

    return RedirectResponse(url=f"{OPENAI_OAUTH_AUTHORIZE_URL}?{urlencode(params)}", status_code=302)


@app.get("/api/auth/openai/callback")
async def openai_oauth_callback(request: Request, code: str, state: str) -> RedirectResponse:
    return await complete_openai_oauth(request, code, state)


@loopback_app.get("/auth/callback")
async def loopback_oauth_callback(request: Request, code: str, state: str) -> RedirectResponse:
    return await complete_openai_oauth(request, code, state)


async def complete_openai_oauth(request: Request, code: str, state: str) -> RedirectResponse:
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
    session_auth = request.session.get("auth") or {}
    profile_id = session_auth.get("profile_id")

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
    profile_id = (request.session.get("auth") or {}).get("profile_id")
    request.session.pop("auth", None)
    request.session.pop("auth_error", None)
    if profile_id:
        remove_auth_profile(profile_id)
    return AuthStatusResponse(authenticated=False)


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: Request, payload: ChatRequest) -> ChatResponse:
    _ = request
    model = os.getenv("CODEX_CHAT_MODEL", "default")
    try:
        reply, response_id = await create_chat_response(
            model=model,
            message=payload.message.strip(),
            previous_response_id=payload.previous_response_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ChatResponse(reply=reply, response_id=response_id, model=model)


@app.post("/api/command", response_model=WorkflowResponse)
def create_plan(payload: CommandRequest) -> WorkflowResponse:
    command = payload.command.strip()
    plan = build_plan(command, detailed=False)
    return WorkflowResponse(
        phase="review",
        approval="pending",
        message="지령을 분석해 플랜 초안을 생성했습니다. 검토 후 승인하거나 수정하십시오.",
        plan=plan,
        tasks=[],
        mcps=MCP_CATALOG,
    )


@app.post("/api/review", response_model=WorkflowResponse)
def revise_plan(payload: ReviewRequest) -> WorkflowResponse:
    command = payload.command.strip()
    plan = build_plan(command, detailed=True)
    return WorkflowResponse(
        phase="review",
        approval="pending",
        message=f"수정 요청 {payload.revision_count}회를 반영해 플랜을 더 세분화했습니다.",
        plan=plan,
        tasks=[],
        mcps=MCP_CATALOG,
    )


@app.post("/api/approve", response_model=WorkflowResponse)
def approve_plan(payload: ApproveRequest) -> WorkflowResponse:
    tasks = build_tasks(payload.plan)
    return WorkflowResponse(
        phase="tasking",
        approval="approved",
        message="플랜 승인이 기록되었습니다. 승인된 플랜을 실행 태스크로 확정했습니다.",
        plan=payload.plan,
        tasks=tasks,
        mcps=MCP_CATALOG,
    )


def build_plan(command: str, detailed: bool) -> List[str]:
    base_plan = [
        f'지령의 목표와 산출물을 분해한다: "{command}"',
        "제약 조건과 확인 포인트를 정리해 검토 가능한 실행안으로 만든다.",
        "승인 후 바로 수행할 수 있는 태스크 묶음으로 전환한다.",
    ]

    if not detailed:
        return base_plan

    return [
        f'지령의 핵심 목표를 정의한다: "{command}"',
        "사용자 확인이 필요한 판단 지점을 분리한다.",
        "필요한 준비물과 의존성을 사전에 점검한다.",
        "실행 순서를 작업 단위로 세분화한다.",
        "각 작업의 완료 기준과 검증 포인트를 명시한다.",
    ]


def build_tasks(plan: List[str]) -> List[TaskItem]:
    tasks: List[TaskItem] = []
    for index, item in enumerate(plan, start=1):
        tasks.append(
            TaskItem(
                id=index,
                title=item.rstrip("."),
                status="queued",
                mcp_ids=map_task_to_mcps(item, index - 1),
            )
        )
    return tasks


def map_task_to_mcps(task_title: str, index: int) -> List[str]:
    normalized = task_title.lower()
    ids: List[str] = []

    def add(mcp_id: str) -> None:
        if mcp_id not in ids:
            ids.append(mcp_id)

    if index == 0 or "정의" in normalized or "분해" in normalized:
        add("planner")
        add("memory")

    if "준비물" in normalized or "의존성" in normalized or "확인" in normalized:
        add("docs")
        add("filesystem")

    if "실행" in normalized or "작업 단위" in normalized or "태스크" in normalized:
        add("terminal")
        add("filesystem")

    if "완료 기준" in normalized or "검증" in normalized:
        add("browser")
        add("docs")

    if not ids:
        add("planner")
        add("filesystem")

    return ids


def get_openai_oauth_client_id() -> str:
    client_id = os.getenv("OPENAI_OAUTH_CLIENT_ID")
    if not client_id:
        raise RuntimeError("OPENAI_OAUTH_CLIENT_ID is not configured.")
    return client_id


def get_openai_redirect_uri() -> str:
    default_uri = f"http://{LOOPBACK_CALLBACK_HOST}:{LOOPBACK_CALLBACK_PORT}/auth/callback"
    return os.getenv("OPENAI_OAUTH_REDIRECT_URI", default_uri)


def get_frontend_callback_redirect() -> str:
    return os.getenv("FRONTEND_APP_URL", "http://127.0.0.1:5173") + "/?auth=complete"


def build_pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def get_active_openai_credential(request: Request) -> Dict[str, Any]:
    session_auth = request.session.get("auth") or {}
    profile_id = session_auth.get("profile_id")
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


async def create_chat_response(
    model: str,
    message: str,
    previous_response_id: Optional[str],
) -> tuple[str, Optional[str]]:
    _ = previous_response_id
    soul_prompt = read_soul_prompt()
    system_prompt = (
        "너는 NiceCodex의 채팅 처리 엔진이다. "
        "항상 한국어로 답하고, 간결하지만 실제로 도움이 되게 답해라. "
        "코드/개발 질문이면 실무적으로 답하고, 모르면 추측하지 말고 부족한 점을 짧게 밝혀라."
    )
    full_prompt = (
        f"{system_prompt}\n\n"
        f"[SOUL]\n{soul_prompt}\n\n"
        f"[사용자 질문]\n{message}"
    )

    with tempfile.NamedTemporaryFile(delete=False) as output_file:
        output_path = output_file.name

    cmd = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "-C",
        str(Path.cwd()),
        "-o",
        output_path,
    ]
    if model != "default":
        cmd.extend(["-m", model])
    cmd.append(full_prompt)

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
        raise RuntimeError(f"Codex CLI request failed: {detail}")

    if not reply:
        raise RuntimeError("Codex CLI returned an empty reply.")

    return reply, None


def read_soul_prompt() -> str:
    if not SOUL_PATH.exists():
        return "자비스는 항상 한국어로, 침착하고 예의 바른 존댓말로 답한다."

    content = SOUL_PATH.read_text().strip()
    if not content:
        return "자비스는 항상 한국어로, 침착하고 예의 바른 존댓말로 답한다."
    return content


def ensure_loopback_server() -> None:
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
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    if not AUTH_PROFILES_PATH.exists():
        AUTH_PROFILES_PATH.write_text('{"profiles":{},"order":{"openai-codex":[]}}')


def ensure_pending_oauth_store() -> None:
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    if not PENDING_OAUTH_PATH.exists():
        PENDING_OAUTH_PATH.write_text("{}")


def with_auth_store_lock(fn):
    ensure_auth_store()
    with AUTH_PROFILES_LOCK_PATH.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def with_pending_oauth_lock(fn):
    ensure_pending_oauth_store()
    with PENDING_OAUTH_LOCK_PATH.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def load_auth_profiles() -> Dict[str, Any]:
    def _load() -> Dict[str, Any]:
        ensure_auth_store()
        import json

        return json.loads(AUTH_PROFILES_PATH.read_text())

    return with_auth_store_lock(_load)


def load_pending_oauth() -> Dict[str, Any]:
    def _load() -> Dict[str, Any]:
        ensure_pending_oauth_store()
        import json

        return json.loads(PENDING_OAUTH_PATH.read_text())

    return with_pending_oauth_lock(_load)


def save_pending_oauth(payload: Dict[str, Any]) -> None:
    def _save() -> None:
        import json

        ensure_pending_oauth_store()
        PENDING_OAUTH_PATH.write_text(json.dumps(payload, indent=2))

    with_pending_oauth_lock(_save)


def clear_pending_oauth() -> None:
    def _clear() -> None:
        ensure_pending_oauth_store()
        PENDING_OAUTH_PATH.write_text("{}")

    with_pending_oauth_lock(_clear)


def save_auth_profiles(store: Dict[str, Any]) -> None:
    def _save() -> None:
        import json

        ensure_auth_store()
        AUTH_PROFILES_PATH.write_text(json.dumps(store, indent=2))

    with_auth_store_lock(_save)


def save_auth_profile(profile: Dict[str, Any]) -> None:
    store = load_auth_profiles()
    profile_id = profile["profileId"]
    store.setdefault("profiles", {})[profile_id] = profile
    order = store.setdefault("order", {}).setdefault("openai-codex", [])
    if profile_id in order:
        order.remove(profile_id)
    order.insert(0, profile_id)
    save_auth_profiles(store)


def remove_auth_profile(profile_id: str) -> None:
    store = load_auth_profiles()
    store.setdefault("profiles", {}).pop(profile_id, None)
    order = store.setdefault("order", {}).setdefault("openai-codex", [])
    store["order"]["openai-codex"] = [item for item in order if item != profile_id]
    save_auth_profiles(store)


def resolve_profile(store: Dict[str, Any], profile_id: Optional[str]) -> Optional[Dict[str, Any]]:
    profiles = store.get("profiles", {})
    if profile_id and profile_id in profiles:
        return profiles[profile_id]

    ordered = store.get("order", {}).get("openai-codex", [])
    for candidate in ordered:
        if candidate in profiles:
            return profiles[candidate]

    return next(iter(profiles.values()), None)


def ensure_fresh_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
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
