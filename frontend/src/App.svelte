<script>
  import { onDestroy, onMount, tick } from "svelte";

  const phaseLabels = {
    idle: "대기",
    review: "검토 요청",
    tasking: "태스크 확정",
    executing: "실행 중",
    reporting: "보고 중",
    completed: "완료",
  };

  let command = "";
  let currentCommand = "";
  let phase = "idle";
  let approval = "pending";
  let revisionCount = 0;
  let plan = null;
  let tasks = [];
  let executionLog = [];
  let executionReport = null;
  let timelineEvents = [];
  let mcps = [];
  let selectedMcpId = "filesystem";
  let activeSideTab = "detail";
  let mcpSidebarOpen = false;
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
  let conversationsBusy = false;
  let liveStatusText = "";
  let previousResponseId = null;
  let currentRunId = null;
  let traceEvents = [];
  let tracePanelOpen = false;
  let conversationSummaries = [];
  let activeConversationId = null;
  let commandInputRef;
  let chatLogRef;
  let mcpSocket;
  $: canSubmit = authStatus.authenticated && !chatBusy;

  $: selectedMcp = mcps.find((mcp) => mcp.id === selectedMcpId) ?? mcps[0];
  $: activeTask = tasks.find((task) => task.status === "in_progress") ?? tasks[0];
  $: linkedTasks = selectedMcp ? tasks.filter((task) => task.mcp_ids.includes(selectedMcp.id)) : [];
  $: activeConversationSummary = conversationSummaries.find((item) => item.conversation_id === activeConversationId) ?? null;

  onMount(async () => {
    await refreshBootstrapState();
    connectMcpSocket();

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

  onDestroy(() => {
    disconnectMcpSocket();
  });

  $: activeWorkflowSequence =
    [...timelineEvents].reverse().find((item) => item.event_type === "workflow_snapshot")?.sequence_no ?? null;

  $: if (timelineEvents.length || executionLog.length || executionReport || tasks.length || plan) {
    scrollChatToBottom();
  }

  function appendOptimisticEvent(event_type, payload) {
    const nextSequence = (timelineEvents.at(-1)?.sequence_no ?? 0) + 1;
    timelineEvents = [
      ...timelineEvents,
      {
        id: `optimistic-${Date.now()}-${nextSequence}`,
        conversation_id: "pending",
        sequence_no: nextSequence,
        event_type,
        payload,
        created_at: new Date().toISOString(),
      },
    ];
  }

  async function submitCommand() {
    const trimmed = command.trim();
    if (!authStatus.authenticated) {
      await focusComposer();
      return;
    }
    if (!trimmed || chatBusy) return;

    chatBusy = true;
    liveStatusText = "지령을 분석하고 플랜 가능 여부를 판단하는 중입니다.";
    appendOptimisticEvent("user_message", { text: trimmed });
    try {
      const response = await postJson("/api/chat", {
        message: trimmed,
        previous_response_id: previousResponseId,
        conversation: buildConversationContext(),
      });
      previousResponseId = response.response_id;
      if (response.mode === "plan" && response.workflow) {
        liveStatusText = "MCP-aware 플랜 초안을 정리하고 있습니다.";
        currentCommand = trimmed;
        revisionCount = 0;
        phase = response.workflow.phase;
        approval = response.workflow.approval;
        plan = response.workflow.plan;
        tasks = response.workflow.tasks;
        executionLog = [];
        executionReport = null;
        currentRunId = response.workflow.run_id ?? null;
        traceEvents = response.workflow.trace ?? [];
        mcps = response.workflow.mcps;
        activeSideTab = "detail";
        selectedMcpId = response.workflow.plan?.proposed_tasks?.[0]?.recommended_mcp_ids?.[0] ?? selectedMcpId;
      }
      await refreshTimeline();
      await refreshConversationSummaries();
      command = "";
    } catch (error) {
      console.error(error);
    } finally {
      chatBusy = false;
      liveStatusText = "";
      await focusComposer();
    }
  }

  async function revisePlan() {
    if (!plan?.proposed_tasks?.length) return;

    revisionCount += 1;
    chatBusy = true;
    liveStatusText = "기존 플랜을 재검토하고 더 구체적인 실행안으로 다듬는 중입니다.";
    appendOptimisticEvent("system_message", { text: `플랜 수정 요청 ${revisionCount}회를 반영 중입니다.` });

    try {
      const response = await postJson("/api/review", {
        command: currentCommand,
        revision_count: revisionCount,
      });

      plan = response.plan;
      tasks = [];
      executionLog = [];
      executionReport = null;
      currentRunId = response.run_id ?? null;
      traceEvents = response.trace ?? [];
      phase = response.phase;
      approval = response.approval;
      mcps = response.mcps;
      await refreshTimeline();
      await refreshConversationSummaries();
    } finally {
      chatBusy = false;
      liveStatusText = "";
    }
  }

  async function approvePlan() {
    if (!plan?.proposed_tasks?.length) return;

    chatBusy = true;
    liveStatusText = "플랜 승인을 반영하고 실행 가능한 태스크로 변환하는 중입니다.";
    appendOptimisticEvent("system_message", { text: "플랜 승인을 처리 중입니다." });
    const response = await postJson("/api/approve", { plan, run_id: currentRunId });
    tasks = response.tasks;
    currentRunId = response.run_id ?? currentRunId;
    traceEvents = response.trace ?? [];
    phase = "executing";
    approval = response.approval;
    mcps = response.mcps;
    executionLog = [];
    executionReport = null;
    activeSideTab = "routing";
    selectedMcpId = response.tasks[0]?.mcp_ids?.[0] ?? selectedMcpId;
    await refreshTimeline();
    await refreshConversationSummaries();
    try {
      liveStatusText = "승인된 태스크를 순차 실행하고 증적을 수집하는 중입니다.";
      appendOptimisticEvent("system_message", { text: "실행 중입니다." });
      const execution = await postJson("/api/execute", { run_id: currentRunId, tasks: response.tasks });
      tasks = execution.tasks;
      executionLog = execution.execution_log;
      executionReport = execution.execution_report;
      currentRunId = execution.run_id ?? currentRunId;
      traceEvents = execution.trace ?? [];
      phase = execution.phase;
      liveStatusText = "실행 결과를 정리해 보고서를 만드는 중입니다.";
      await refreshTimeline();
      await refreshConversationSummaries();
    } catch (error) {
      console.error(error);
    } finally {
      chatBusy = false;
      liveStatusText = "";
      await focusComposer();
    }
  }

  function escapeHtml(value) {
    return value
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function renderInlineMarkdown(value) {
    const markdownLinks = [];
    const withPlaceholders = value
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_match, label, url) => {
        const token = `__JARVIS_LINK_${markdownLinks.length}__`;
        markdownLinks.push(`<a href="${url}" target="_blank" rel="noreferrer">${label}</a>`);
        return token;
      })
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>")
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, (_match, prefix, url) => {
        return `${prefix}<a href="${url}" target="_blank" rel="noreferrer">${url}</a>`;
      });

    return withPlaceholders.replace(/__JARVIS_LINK_(\d+)__/g, (_match, index) => markdownLinks[Number(index)] ?? "");
  }

  function renderMarkdown(text) {
    const escaped = escapeHtml(text ?? "");
    const blocks = escaped.split(/\n{2,}/).map((chunk) => chunk.trim()).filter(Boolean);

    return blocks
      .map((block) => {
        if (block.startsWith("```") && block.endsWith("```")) {
          const lines = block.split("\n");
          const code = lines.slice(1, -1).join("\n");
          return `<pre><code>${code}</code></pre>`;
        }

        if (block.startsWith("- ")) {
          const items = block
            .split("\n")
            .filter((line) => line.startsWith("- "))
            .map((line) => `<li>${renderInlineMarkdown(line.slice(2))}</li>`)
            .join("");
          return `<ul>${items}</ul>`;
        }

        if (block.startsWith("1. ")) {
          const items = block
            .split("\n")
            .filter((line) => /^\d+\.\s/.test(line))
            .map((line) => `<li>${renderInlineMarkdown(line.replace(/^\d+\.\s/, ""))}</li>`)
            .join("");
          return `<ol>${items}</ol>`;
        }

        if (block.startsWith("### ")) return `<h4>${renderInlineMarkdown(block.slice(4))}</h4>`;
        if (block.startsWith("## ")) return `<h3>${renderInlineMarkdown(block.slice(3))}</h3>`;
        if (block.startsWith("# ")) return `<h2>${renderInlineMarkdown(block.slice(2))}</h2>`;

        return `<p>${renderInlineMarkdown(block).replace(/\n/g, "<br />")}</p>`;
      })
      .join("");
  }

  function buildConversationContext() {
    return timelineEvents
      .filter((item) => item.event_type === "user_message" || item.event_type === "assistant_message" || item.event_type === "system_message")
      .slice(-12)
      .map((item) => ({
        role: item.event_type === "assistant_message" ? "assistant" : item.event_type === "user_message" ? "user" : "system",
        content: item.payload.text,
      }));
  }

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const rawText = await response.text();
    let data = {};
    try {
      data = rawText ? JSON.parse(rawText) : {};
    } catch (_error) {
      data = { detail: rawText || "Request failed" };
    }
    if (!response.ok) {
      throw new Error(data.detail ?? "Request failed");
    }
    return data;
  }

  async function resetSession() {
    const response = await postJson("/api/conversation/reset", {});
    stopRunTimer();
    command = "";
    currentCommand = "";
    previousResponseId = null;
    activeConversationId = response.conversation_id;
    currentRunId = null;
    phase = "idle";
    approval = "pending";
    revisionCount = 0;
    plan = null;
    tasks = [];
    executionLog = [];
    executionReport = null;
    traceEvents = [];
    tracePanelOpen = false;
    activeSideTab = "detail";
    selectedMcpId = "filesystem";
    timelineEvents = [];
    await refreshConversationSummaries();
    await focusComposer();
  }

  function formatMcpNames(ids) {
    return (ids ?? []).map((id) => mcps.find((mcp) => mcp.id === id)?.name ?? id).join(", ");
  }

  function formatTraceEvent(entry) {
    const detail = Object.entries(entry ?? {})
      .filter(([key]) => key !== "event")
      .map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`)
      .join(" | ");
    return detail ? `${entry.event} | ${detail}` : entry.event;
  }

  function stopRunTimer() {
    if (runTimer) {
      clearInterval(runTimer);
      runTimer = null;
    }
  }

  function connectMcpSocket() {
    disconnectMcpSocket();
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    mcpSocket = new WebSocket(`${protocol}//${window.location.host}/ws/mcps`);
    mcpSocket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "mcps_updated" && Array.isArray(payload.mcps)) {
          mcps = payload.mcps;
          if (!payload.mcps.some((mcp) => mcp.id === selectedMcpId)) {
            selectedMcpId = payload.mcps[0]?.id ?? "filesystem";
          }
        }
      } catch (_error) {
        // Ignore malformed websocket payloads.
      }
    };
    mcpSocket.onclose = () => {
      window.setTimeout(() => {
        if (!mcpSocket || mcpSocket.readyState === WebSocket.CLOSED) {
          connectMcpSocket();
        }
      }, 1500);
    };
  }

  function disconnectMcpSocket() {
    if (mcpSocket) {
      mcpSocket.onclose = null;
      mcpSocket.close();
      mcpSocket = null;
    }
  }

  function toggleMcpSidebar() {
    mcpSidebarOpen = !mcpSidebarOpen;
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

  async function scrollChatToBottom() {
    await tick();
    if (!chatLogRef) return;
    chatLogRef.scrollTop = chatLogRef.scrollHeight;
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
    const [mcpResponse, authResponse, conversationResponse] = await Promise.all([
      fetch("/api/mcps"),
      fetch("/api/auth/status", { credentials: "include" }),
      fetch("/api/conversations", { credentials: "include" }),
    ]);

    mcps = await mcpResponse.json();
    authStatus = await authResponse.json();
    conversationSummaries = await conversationResponse.json();
    activeConversationId = conversationSummaries.find((item) => item.current)?.conversation_id ?? activeConversationId;
    await refreshTimeline();
  }

  async function refreshAuthStatus() {
    const response = await fetch("/api/auth/status", { credentials: "include" });
    authStatus = await response.json();
  }

  async function refreshTimeline() {
    const response = await fetch("/api/conversation/events", { credentials: "include" });
    timelineEvents = await response.json();
    activeConversationId = timelineEvents[0]?.conversation_id ?? activeConversationId;
    hydrateCurrentWorkflow();
  }

  async function refreshConversationSummaries() {
    const response = await fetch("/api/conversations", { credentials: "include" });
    conversationSummaries = await response.json();
    activeConversationId = conversationSummaries.find((item) => item.current)?.conversation_id ?? activeConversationId;
  }

  async function selectConversation(conversationId) {
    if (!conversationId || conversationId === activeConversationId || chatBusy || conversationsBusy) return;
    conversationsBusy = true;
    try {
      const summary = await postJson("/api/conversations/select", { conversation_id: conversationId });
      activeConversationId = summary.conversation_id;
      previousResponseId = null;
      tracePanelOpen = false;
      await refreshTimeline();
      await refreshConversationSummaries();
    } finally {
      conversationsBusy = false;
      await focusComposer();
    }
  }

  function formatConversationDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat("ko-KR", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function hydrateCurrentWorkflow() {
    const latestWorkflow = [...timelineEvents].reverse().find((item) => item.event_type === "workflow_snapshot");
    if (!latestWorkflow) {
      phase = "idle";
      approval = "pending";
      plan = null;
      tasks = [];
      executionLog = [];
      executionReport = null;
      traceEvents = [];
      currentRunId = null;
      tracePanelOpen = false;
      return;
    }

    const snapshot = latestWorkflow.payload;
    phase = snapshot.phase ?? "idle";
    approval = snapshot.approval ?? "pending";
    plan = snapshot.plan;
    tasks = snapshot.tasks ?? [];
    executionLog = snapshot.executionLog ?? [];
    executionReport = snapshot.executionReport ?? null;
    traceEvents = snapshot.traceEvents ?? [];
    currentRunId = snapshot.currentRunId ?? null;
  }

</script>

<svelte:head>
  <title>JARVIS</title>
</svelte:head>

<div class:sidebar-collapsed={!mcpSidebarOpen} class="app-shell">
  <aside class="mission-sidebar">
    <div>
      <p class="eyebrow">MISSION CONTROL</p>
      <h1>JARVIS</h1>
      <p class="sidebar-copy">
        대화 세션을 기준으로 지령, 승인, 실행 이력을 이어서 관리합니다.
      </p>
    </div>

    <section class="conversation-panel">
      <div class="conversation-panel-header">
        <div>
          <p class="eyebrow">CONVERSATIONS</p>
          <h2>이전 대화</h2>
        </div>
      </div>

      {#if activeConversationId && !activeConversationSummary}
        <button type="button" class:selected={true} class="conversation-item draft">
          <div class="conversation-item-header">
            <span class="conversation-time">현재</span>
          </div>
          <strong class="conversation-title">새 대화</strong>
        </button>
      {/if}

      <div class="conversation-list">
        {#if conversationSummaries.length}
          {#each conversationSummaries as conversation}
            <button
              type="button"
              class:selected={conversation.conversation_id === activeConversationId}
              class="conversation-item"
              disabled={chatBusy || conversationsBusy}
              on:click={() => selectConversation(conversation.conversation_id)}
            >
              <div class="conversation-item-header">
                <span class="conversation-time">{formatConversationDate(conversation.updated_at)}</span>
              </div>
              <strong class="conversation-title">{conversation.title}</strong>
            </button>
          {/each}
        {:else}
          <div class="conversation-empty">
            아직 저장된 이전 대화가 없습니다.
          </div>
        {/if}
      </div>
    </section>

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
    <section class:busy={chatBusy} class="chat-panel">
      <header class="panel-header">
        <div>
          <p class="eyebrow">CHAT INTERFACE</p>
          <div class="panel-title-row">
            <h2>{activeConversationSummary?.title ?? "JARVIS Console"}</h2>
            <div class:active={chatBusy} class="processing-indicator">
              <span class="processing-dot"></span>
              <span>{chatBusy ? "PROCESSING" : "STANDBY"}</span>
            </div>
          </div>
          <p class="panel-subtitle">
            {activeConversationSummary?.preview ?? "현재 세션에서 이어서 질문하거나 새 대화를 시작할 수 있습니다."}
          </p>
        </div>
        <div class="header-actions">
          <button
            class="ghost-button header-icon-button"
            aria-label="세션 초기화"
            title="세션 초기화"
            on:click={resetSession}
          >
            ↻
          </button>
          <button class="ghost-button sidebar-toggle" on:click={toggleMcpSidebar}>
            {mcpSidebarOpen ? "MCP 닫기" : "MCP"}
          </button>
        </div>
      </header>

        <div class="chat-log" bind:this={chatLogRef}>
        {#if !timelineEvents.length}
          <div class="message bot">
            <div class="message-content">
              {@html renderMarkdown("자비스 준비 완료. OpenAI 로그인 후 지령을 입력하면 플랜을 먼저 제안하고, 승인 후에만 실행 태스크를 확정합니다.")}
            </div>
          </div>
        {/if}
        {#each timelineEvents as item}
          {#if item.event_type === "workflow_snapshot"}
            <article class="workflow-entry workflow-message">
              {#if item.payload.plan}
                <div class="card-title-row">
                  <h3>플랜</h3>
                  <span class="badge">1개</span>
                </div>
                <p><strong>{item.payload.plan.summary}</strong></p>
                {#if item.payload.plan.strategy?.applied}
                  <section class="strategy-panel">
                    <div class="card-title-row">
                      <h4>Sequential Thinking</h4>
                      <span class="badge">ST 개입됨</span>
                    </div>
                    {#if item.payload.plan.strategy.reason}
                      <div class="task-meta"><strong>개입 이유</strong></div>
                      <p>{item.payload.plan.strategy.reason}</p>
                    {/if}
                    {#if item.payload.plan.strategy.summary}
                      <div class="task-meta"><strong>전략 요약</strong></div>
                      <p>{item.payload.plan.strategy.summary}</p>
                    {/if}
                  </section>
                {/if}
                <div class="task-meta"><strong>목표</strong></div>
                <p>{item.payload.plan.objective}</p>
                <div class="task-meta"><strong>제안 태스크</strong></div>
                <ul class="item-list">
                  {#each item.payload.plan.proposed_tasks as task}
                    <li>
                      <strong>{task.title}</strong>
                      {#if task.recommended_mcp_ids?.length}
                        <div class="task-meta">추천 MCP: {formatMcpNames(task.recommended_mcp_ids)}</div>
                      {/if}
                      {#if task.expected_result}
                        <div class="task-meta">예상 결과: {task.expected_result}</div>
                      {/if}
                      {#if task.rationale}
                        <div class="task-meta">{task.rationale}</div>
                      {/if}
                    </li>
                  {/each}
                </ul>
              {/if}

              {#if item.payload.plan}
                <article class="workflow-entry workflow-message compact embedded-workflow">
                  <div class="card-title-row">
                    <h3>검토</h3>
                    <span class="badge">{item.payload.approval === "approved" ? "승인됨" : "검토 필요"}</span>
                  </div>
                  {#if item.sequence_no === activeWorkflowSequence && item.payload.approval !== "approved"}
                    <div class="review-actions inline">
                      <button class="primary-button" disabled={!plan?.proposed_tasks?.length || approval === "approved"} on:click={approvePlan}>플랜 승인</button>
                      <button class="ghost-button" disabled={!plan?.proposed_tasks?.length || approval === "approved"} on:click={revisePlan}>수정 요청</button>
                    </div>
                  {/if}
                </article>
              {/if}

              {#if item.payload.tasks?.length}
                <div class="card-title-row">
                  <h3>실행 태스크</h3>
                  <span class="badge">{item.payload.tasks.filter((task) => task.status === "done").length}/{item.payload.tasks.length}</span>
                </div>
                <ol class="item-list">
                  {#each item.payload.tasks as task}
                    <li>
                      <strong>{task.title}</strong>
                      <div class="task-meta">사용 MCP: {formatMcpNames(task.mcp_ids)}</div>
                      <div class="task-meta">상태: {task.status === "done" ? "완료" : task.status === "in_progress" ? "진행 중" : "대기"}</div>
                    </li>
                  {/each}
                </ol>
              {/if}

              {#if item.payload.executionLog?.length}
                <div class="card-title-row">
                  <h3>실행 로그</h3>
                  <span class="badge">{item.payload.phase === "completed" ? "완료" : item.payload.phase}</span>
                </div>
                <ul class="item-list">
                  {#each item.payload.executionLog as entry}
                    <li>{entry}</li>
                  {/each}
                </ul>
              {/if}

              {#if item.payload.executionReport}
                <div class="card-title-row">
                  <h3>보고</h3>
                  <span class="badge">{item.payload.executionReport.status}</span>
                </div>
                <p><strong>{item.payload.executionReport.summary}</strong></p>
                {#if item.payload.executionReport.result_items?.length}
                  <div class="task-meta"><strong>결과 목록</strong></div>
                  <ul class="item-list">
                    {#each item.payload.executionReport.result_items as resultItem}
                      <li>{resultItem}</li>
                    {/each}
                  </ul>
                {/if}
                <div class="task-meta"><strong>발견 사항</strong></div>
                <ul class="item-list">
                  {#each item.payload.executionReport.findings as finding}
                    <li>{finding}</li>
                  {/each}
                </ul>
                <div class="task-meta"><strong>결론</strong></div>
                <p>{item.payload.executionReport.conclusion}</p>
                <div class="task-meta"><strong>실행 근거</strong></div>
                <ul class="item-list compact-list">
                  {#each item.payload.executionReport.evidence as evidence}
                    <li>{evidence}</li>
                  {/each}
                </ul>
              {/if}

              {#if item.sequence_no === activeWorkflowSequence && item.payload.traceEvents?.length}
                <article class="workflow-entry workflow-message compact embedded-workflow">
                  <div class="card-title-row">
                    <h3>운영 Trace</h3>
                    <span class="badge">{item.payload.traceEvents.length}개</span>
                  </div>
                  <div class="review-actions inline">
                    <button class="ghost-button" on:click={() => (tracePanelOpen = !tracePanelOpen)}>
                      {tracePanelOpen ? "Trace 닫기" : "Trace 열기"}
                    </button>
                    {#if item.payload.currentRunId}
                      <span class="task-meta">run_id: {item.payload.currentRunId}</span>
                    {/if}
                  </div>
                  {#if tracePanelOpen}
                    <ul class="item-list compact-list trace-list">
                      {#each item.payload.traceEvents as traceEntry}
                        <li><code>{formatTraceEvent(traceEntry)}</code></li>
                      {/each}
                    </ul>
                  {/if}
                </article>
              {/if}
            </article>
          {:else}
            <div class={`message ${item.event_type === "assistant_message" ? "bot" : item.event_type === "user_message" ? "user" : "system"}`}>
              <div class="message-content">{@html renderMarkdown(item.payload.text)}</div>
            </div>
          {/if}
        {/each}
      </div>

      <div class="workflow-status-bar">
        <span class="status-pill">단계: {phaseLabels[phase] ?? phase}</span>
        <span class="status-pill">승인: {approval === "approved" ? "승인됨" : "검토 필요"}</span>
        <span class="status-pill">플랜: {plan ? "1개" : "대기"}</span>
        <span class="status-pill">태스크: {tasks.length ? `${tasks.filter((task) => task.status === "done").length}/${tasks.length}` : "대기"}</span>
        <span class="status-pill">보고: {executionReport ? executionReport.status : "대기"}</span>
      </div>

      <form
        class:busy={chatBusy}
        class="composer"
        on:submit|preventDefault={submitCommand}
      >
        <textarea
          bind:value={command}
          bind:this={commandInputRef}
          disabled={!authStatus.authenticated || chatBusy}
          on:keydown={handleComposerKeydown}
          rows="3"
          placeholder={authStatus.authenticated
            ? "예: 새 랜딩 페이지를 만들고, API 연동 전까지는 목업 데이터로 검증해."
            : "OpenAI 로그인 후 질문을 입력할 수 있습니다."}
        ></textarea>
        {#if chatBusy && liveStatusText}
          <div class="composer-status">
            <span class="composer-status-label">현재 내부 동작 상태</span>
            <strong>{liveStatusText}</strong>
          </div>
        {/if}
        <button class:pulse={chatBusy} class="primary-button" disabled={!canSubmit} type="submit">
          {chatBusy ? "처리 중..." : "질문 보내기"}
        </button>
      </form>
    </section>
  </main>

  <aside class:open={mcpSidebarOpen} class="mcp-sidebar">
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
