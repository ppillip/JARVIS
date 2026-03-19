<script>
  import { onMount, tick } from "svelte";

  const phaseLabels = {
    idle: "대기",
    review: "검토 요청",
    tasking: "태스크 확정",
    executing: "실행 중",
    completed: "완료",
  };

  let command = "";
  let currentCommand = "";
  let phase = "idle";
  let approval = "pending";
  let revisionCount = 0;
  let plan = [];
  let tasks = [];
  let executionLog = [];
  let chatMessages = [
      {
        type: "bot",
        text: "자비스 준비 완료. OpenAI 로그인 후 지령을 입력하면 플랜을 먼저 제안하고, 승인 후에만 실행 태스크를 확정합니다.",
      },
  ];
  let mcps = [];
  let selectedMcpId = "planner";
  let activeSideTab = "detail";
  let runTimer;
  let authStatus = {
    authenticated: false,
    provider: null,
    account_id: null,
    expires_at: null,
    error: null,
  };
  let authBusy = false;
  let chatBusy = false;
  let previousResponseId = null;
  let commandInputRef;

  $: selectedMcp = mcps.find((mcp) => mcp.id === selectedMcpId) ?? mcps[0];
  $: activeTask = tasks.find((task) => task.status === "in_progress") ?? tasks[0];
  $: linkedTasks = selectedMcp ? tasks.filter((task) => task.mcp_ids.includes(selectedMcp.id)) : [];

  onMount(async () => {
    await refreshBootstrapState();

    const params = new URLSearchParams(window.location.search);
    if (params.get("auth") === "complete") {
      await refreshAuthStatus();
      params.delete("auth");
      params.delete("status");
      const nextUrl = `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ""}`;
      window.history.replaceState({}, "", nextUrl);
    }

    await focusComposer();
  });

  async function submitCommand() {
    const trimmed = command.trim();
    if (!trimmed || chatBusy) return;

    addMessage("user", trimmed);
    chatBusy = true;
    try {
      const response = await postJson("/api/chat", {
        message: trimmed,
        previous_response_id: previousResponseId,
      });
      previousResponseId = response.response_id;
      addMessage("bot", response.reply);
      command = "";
    } catch (error) {
      addMessage("system", error.message ?? "질문 처리 중 오류가 발생했습니다.");
    } finally {
      chatBusy = false;
      await focusComposer();
    }
  }

  async function revisePlan() {
    if (!plan.length) return;

    revisionCount += 1;
    addMessage("user", "플랜을 더 구체적으로 다듬어라.");

    const response = await postJson("/api/review", {
      command: currentCommand,
      revision_count: revisionCount,
    });

    plan = response.plan;
    tasks = [];
    executionLog = [];
    phase = response.phase;
    approval = response.approval;
    mcps = response.mcps;
    addMessage("bot", response.message);
  }

  async function approvePlan() {
    if (!plan.length) return;

    const response = await postJson("/api/approve", { plan });
    tasks = response.tasks;
    phase = "executing";
    approval = response.approval;
    mcps = response.mcps;
    executionLog = [];
    addMessage("system", "플랜 승인이 기록되었습니다.");
    addMessage("bot", response.message);
    activeSideTab = "routing";
    selectedMcpId = response.tasks[0]?.mcp_ids?.[0] ?? selectedMcpId;
    runTasks(response.tasks);
  }

  function runTasks(nextTasks) {
    stopRunTimer();
    tasks = nextTasks.map((task, index) => ({
      ...task,
      status: index === 0 ? "in_progress" : "queued",
    }));
    executionLog = [`Task 1: ${tasks[0].title}`];

    let pointer = 0;
    runTimer = setInterval(() => {
      tasks = tasks.map((task, index) => {
        if (index < pointer) return { ...task, status: "done" };
        if (index === pointer) return { ...task, status: "done" };
        if (index === pointer + 1) return { ...task, status: "in_progress" };
        return task;
      });

      pointer += 1;

      if (pointer >= tasks.length) {
        stopRunTimer();
        phase = "completed";
        executionLog = [...executionLog, "모든 태스크 수행이 끝났습니다."];
        addMessage("system", "실행 완료.");
        addMessage("bot", "승인된 순서대로 작업을 마쳤습니다. 다음 지령을 받을 준비가 되어 있습니다.");
        return;
      }

      selectedMcpId = tasks[pointer].mcp_ids[0] ?? selectedMcpId;
      executionLog = [...executionLog, `Task ${tasks[pointer].id}: ${tasks[pointer].title}`];
    }, 900);
  }

  function addMessage(type, text) {
    chatMessages = [...chatMessages, { type, text }];
  }

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail ?? "Request failed");
    }
    return data;
  }

  function resetSession() {
    stopRunTimer();
    command = "";
    currentCommand = "";
    previousResponseId = null;
    phase = "idle";
    approval = "pending";
    revisionCount = 0;
    plan = [];
    tasks = [];
    executionLog = [];
    activeSideTab = "detail";
    selectedMcpId = "planner";
    chatMessages = [
      {
        type: "bot",
        text: "세션을 초기화했습니다. 새 지령을 입력하면 같은 승인 기반 워크플로우로 다시 시작합니다.",
      },
    ];
  }

  function formatMcpNames(ids) {
    return ids.map((id) => mcps.find((mcp) => mcp.id === id)?.name ?? id).join(", ");
  }

  function stopRunTimer() {
    if (runTimer) {
      clearInterval(runTimer);
      runTimer = null;
    }
  }

  function loginWithOpenAI() {
    authBusy = true;
    window.location.href = "/api/auth/openai";
  }

  function handleComposerKeydown(event) {
    if (event.key === "Enter" && event.metaKey && !event.shiftKey) {
      event.preventDefault();
      submitCommand();
    }
  }

  async function focusComposer() {
    await tick();
    commandInputRef?.focus();
  }

  async function logout() {
    authBusy = true;
    try {
      authStatus = await postJson("/api/auth/logout", {});
    } finally {
      authBusy = false;
    }
  }

  async function refreshBootstrapState() {
    const [mcpResponse, authResponse] = await Promise.all([
      fetch("/api/mcps"),
      fetch("/api/auth/status", { credentials: "include" }),
    ]);

    mcps = await mcpResponse.json();
    authStatus = await authResponse.json();
  }

  async function refreshAuthStatus() {
    const response = await fetch("/api/auth/status", { credentials: "include" });
    authStatus = await response.json();
  }
</script>

<svelte:head>
  <title>NiceCodex</title>
</svelte:head>

<div class="app-shell">
  <aside class="mission-sidebar">
    <div>
      <p class="eyebrow">MISSION CONTROL</p>
      <h1>JARVIS</h1>
      <p class="sidebar-copy">
        지령을 받으면 계획을 세우고, 승인받고, 실행 태스크를 확정한 뒤 그대로 수행하는 자비스형 챗봇입니다.
      </p>
    </div>

    <section class="status-panel">
      <div class="status-card auth-card">
        <span>OpenAI 로그인</span>
        {#if authStatus.authenticated}
          <strong>연결됨</strong>
          <div class="auth-meta">
            OpenAI 인증 프로필 활성화
            <br />
            {authStatus.email ?? authStatus.account_id ?? "account id unavailable"}
          </div>
          <button class="ghost-button auth-button" disabled={authBusy} on:click={logout}>
            {authBusy ? "처리 중..." : "로그아웃"}
          </button>
        {:else}
          <strong>미연결</strong>
          <div class="auth-meta">
            {authStatus.error ?? "OpenAI 로그인 화면으로 이동해 인증하고, 인증 프로필을 로컬에 저장해 세션을 유지합니다."}
          </div>
          <button class="primary-button auth-button" disabled={authBusy} on:click={loginWithOpenAI}>
            {authBusy ? "이동 중..." : "OpenAI 로그인"}
          </button>
        {/if}
      </div>

      <div class="status-card">
        <span>현재 단계</span>
        <strong>{phaseLabels[phase] ?? phase}</strong>
      </div>
      <div class="status-card">
        <span>승인 상태</span>
        <strong>{approval === "approved" ? "승인됨" : "미승인"}</strong>
      </div>
    </section>
  </aside>

  <main class="main-panel">
    <section class="chat-panel">
      <header class="panel-header">
        <div>
          <p class="eyebrow">CHAT INTERFACE</p>
          <h2>JARVIS Console</h2>
        </div>
        <button class="ghost-button" on:click={resetSession}>세션 초기화</button>
      </header>

      <div class="chat-log">
        {#each chatMessages as item}
          <div class={`message ${item.type}`}>{item.text}</div>
        {/each}
      </div>

      <div class="workflow-strip">
        <article class="workflow-card">
          <div class="card-title-row">
            <h3>플랜 초안</h3>
            <span class="badge">{plan.length ? `${plan.length}개 단계` : "비어 있음"}</span>
          </div>
          <ul class={`item-list ${plan.length ? "" : "empty-state"}`}>
            {#if plan.length}
              {#each plan as step}
                <li>{step}</li>
              {/each}
            {:else}
              <li>아직 생성된 플랜이 없습니다.</li>
            {/if}
          </ul>
        </article>

        <article class="workflow-card compact">
          <div class="card-title-row">
            <h3>검토</h3>
            <span class="badge">{approval === "approved" ? "승인됨" : "검토 필요"}</span>
          </div>
          <div class="review-actions">
            <button class="primary-button" disabled={!plan.length} on:click={approvePlan}>플랜 승인</button>
            <button class="ghost-button" disabled={!plan.length} on:click={revisePlan}>수정 요청</button>
          </div>
        </article>

        <article class="workflow-card">
          <div class="card-title-row">
            <h3>실행 태스크</h3>
            <span class="badge">{tasks.length ? `${tasks.filter((task) => task.status === "done").length}/${tasks.length}` : "대기"}</span>
          </div>
          <ol class={`item-list ${tasks.length ? "" : "empty-state"}`}>
            {#if tasks.length}
              {#each tasks as task}
                <li>
                  <strong>{task.title}</strong>
                  <div class="task-meta">사용 MCP: {formatMcpNames(task.mcp_ids)}</div>
                </li>
              {/each}
            {:else}
              <li>플랜 승인이 끝나면 태스크가 확정됩니다.</li>
            {/if}
          </ol>
        </article>

        <article class="workflow-card">
          <div class="card-title-row">
            <h3>실행 로그</h3>
            <span class="badge">{phase === "executing" ? "진행 중" : phase === "completed" ? "완료" : "정지"}</span>
          </div>
          <ul class={`item-list ${executionLog.length ? "" : "empty-state"}`}>
            {#if executionLog.length}
              {#each executionLog as entry}
                <li>{entry}</li>
              {/each}
            {:else}
              <li>아직 실행되지 않았습니다.</li>
            {/if}
          </ul>
        </article>
      </div>

      <form
        class="composer"
        on:submit|preventDefault={submitCommand}
      >
        <textarea
          bind:value={command}
          bind:this={commandInputRef}
          disabled={chatBusy}
          on:keydown={handleComposerKeydown}
          rows="3"
          placeholder="예: 새 랜딩 페이지를 만들고, API 연동 전까지는 목업 데이터로 검증해."
        ></textarea>
        <button class="primary-button" disabled={chatBusy} type="submit">
          {chatBusy ? "처리 중..." : "질문 보내기"}
        </button>
      </form>
    </section>
  </main>

  <aside class="mcp-sidebar">
    <section class="mcp-panel">
      <div>
        <p class="eyebrow">MCP REGISTRY</p>
        <h2>Available MCPs</h2>
      </div>

      <ul class="mcp-list">
        {#each mcps as mcp}
          <li>
            <button
              type="button"
              class:selected={selectedMcpId === mcp.id}
              class:active={activeTask && activeTask.mcp_ids.includes(mcp.id)}
              class="mcp-item"
              on:click={() => {
                selectedMcpId = mcp.id;
                activeSideTab = "detail";
              }}
            >
              <div class="mcp-item-header">
                <span class="mcp-name">{mcp.name}</span>
                <span class="mcp-badge">{mcp.scope}</span>
              </div>
              <p>{mcp.description}</p>
            </button>
          </li>
        {/each}
      </ul>
    </section>

    <section class="mcp-panel">
      <div>
        <p class="eyebrow">DETAIL CONSOLE</p>
        <h2>MCP Console</h2>
      </div>

      <div class="tab-row">
        <button class:active={activeSideTab === "detail"} class="tab-button" on:click={() => (activeSideTab = "detail")}>
          MCP 상세
        </button>
        <button class:active={activeSideTab === "routing"} class="tab-button" on:click={() => (activeSideTab = "routing")}>
          Task 라우팅
        </button>
      </div>

      {#if activeSideTab === "detail" && selectedMcp}
        <article class="mcp-detail-card">
          <div class="mcp-item-header">
            <span class="mcp-name">{selectedMcp.name}</span>
            <span class="mcp-badge">{selectedMcp.scope}</span>
          </div>
          <p class="mcp-detail-copy">{selectedMcp.description}</p>

          <div class="mcp-detail-group">
            <h3>지원 태스크</h3>
            <ul class="detail-list">
              {#each selectedMcp.capabilities as capability}
                <li>{capability}</li>
              {/each}
            </ul>
          </div>

          <div class="mcp-detail-group">
            <h3>예상 입력</h3>
            <p class="mcp-detail-copy">{selectedMcp.expected_input}</p>
          </div>

          <div class="mcp-detail-group">
            <h3>예상 출력</h3>
            <p class="mcp-detail-copy">{selectedMcp.expected_output}</p>
          </div>

          <div class="mcp-detail-group">
            <h3>현재 연결 상태</h3>
            <p class="mcp-detail-copy">
              {#if linkedTasks.length}
                현재 {linkedTasks.length}개 Task에 연결되어 있습니다:
                {linkedTasks.map((task) => `Task ${task.id}`).join(", ")}
              {:else}
                아직 어떤 Task에도 연결되지 않았습니다.
              {/if}
            </p>
          </div>
        </article>
      {:else}
        <ul class={`mcp-task-map ${tasks.length ? "" : "empty-state"}`}>
          {#if tasks.length}
            {#each tasks as task}
              <li class="mcp-task-item">
                <div class="mcp-task-header">
                  <span class="mcp-task-title">Task {task.id}</span>
                  <span class="mcp-badge">
                    {task.status === "done" ? "완료" : task.status === "in_progress" ? "진행 중" : "대기"}
                  </span>
                </div>
                <p>{task.title}</p>
                <div class="mcp-task-chips">
                  {#each task.mcp_ids as mcpId}
                    <span class="mcp-chip">{mcps.find((mcp) => mcp.id === mcpId)?.name ?? mcpId}</span>
                  {/each}
                </div>
              </li>
            {/each}
          {:else}
            <li>태스크가 생성되면 MCP 연결 정보가 표시됩니다.</li>
          {/if}
        </ul>
      {/if}
    </section>
  </aside>
</div>
