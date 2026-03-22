<script>
  import { onMount } from "svelte";

  let activeTab = "registry";
  let registryEntries = [];
  let promptEntries = [];
  let runEntries = [];
  let selectedRun = null;
  let busy = false;
  let errorMessage = "";
  let selectedPromptId = "";
  let selectedVersion = null;

  let registryForm = {
    id: "",
    name: "",
    scope: "",
    description: "",
    capabilities: "",
    expected_input: "",
    expected_output: "",
  };

  let promptForm = {
    id: "",
    name: "",
    description: "",
    content: "",
  };

  $: selectedPrompt = promptEntries.find((entry) => entry.id === selectedPromptId) ?? promptEntries[0];

  onMount(async () => {
    await Promise.all([refreshRegistryEntries(), refreshPromptEntries(), refreshRunEntries()]);
  });

  async function refreshRegistryEntries() {
    const response = await fetch("/api/registry/mcps");
    registryEntries = await response.json();
  }

  async function refreshPromptEntries() {
    const response = await fetch("/api/prompts");
    promptEntries = await response.json();
    if (!selectedPromptId && promptEntries.length) {
      loadPrompt(promptEntries[0]);
    }
  }

  async function refreshRunEntries() {
    const response = await fetch("/api/runs");
    runEntries = await response.json();
    if (!selectedRun && runEntries.length) {
      await loadRun(runEntries[0].id);
    }
  }

  async function postJson(url, body, method = "POST") {
    const response = await fetch(url, {
      method,
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail ?? "Request failed");
    }
    return data;
  }

  function resetPromptForm() {
    selectedPromptId = "";
    selectedVersion = null;
    promptForm = {
      id: "",
      name: "",
      description: "",
      content: "",
    };
  }

  function loadPrompt(entry) {
    selectedPromptId = entry.id;
    selectedVersion = entry.active_version;
    promptForm = {
      id: entry.id,
      name: entry.name,
      description: entry.description,
      content: entry.content,
    };
  }

  async function toggleRegistryEntry(entry) {
    busy = true;
    errorMessage = "";
    try {
      await postJson(`/api/registry/mcps/${entry.id}`, { enabled: !entry.enabled }, "PATCH");
      await refreshRegistryEntries();
    } catch (error) {
      errorMessage = error.message ?? "레지스트리 업데이트에 실패했습니다.";
    } finally {
      busy = false;
    }
  }

  async function createRegistryEntry() {
    if (!registryForm.id.trim() || !registryForm.name.trim() || !registryForm.description.trim()) return;

    busy = true;
    errorMessage = "";
    try {
      await postJson("/api/registry/mcps", {
        ...registryForm,
        capabilities: registryForm.capabilities
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      });
      registryForm = {
        id: "",
        name: "",
        scope: "",
        description: "",
        capabilities: "",
        expected_input: "",
        expected_output: "",
      };
      await refreshRegistryEntries();
    } catch (error) {
      errorMessage = error.message ?? "MCP 생성에 실패했습니다.";
    } finally {
      busy = false;
    }
  }

  async function savePrompt() {
    if (!promptForm.id.trim() || !promptForm.name.trim() || !promptForm.content.trim()) return;

    busy = true;
    errorMessage = "";
    try {
      if (selectedPromptId) {
        await postJson(`/api/prompts/${selectedPromptId}`, {
          name: promptForm.name,
          description: promptForm.description,
          content: promptForm.content,
        }, "PATCH");
      } else {
        await postJson("/api/prompts", promptForm);
      }
      await refreshPromptEntries();
      const nextId = selectedPromptId || promptForm.id;
      const nextPrompt = (await fetch("/api/prompts").then((response) => response.json())).find((entry) => entry.id === nextId);
      if (nextPrompt) loadPrompt(nextPrompt);
    } catch (error) {
      errorMessage = error.message ?? "프롬프트 저장에 실패했습니다.";
    } finally {
      busy = false;
    }
  }

  async function deletePrompt() {
    if (!selectedPromptId) return;

    busy = true;
    errorMessage = "";
    try {
      await fetch(`/api/prompts/${selectedPromptId}`, {
        method: "DELETE",
        credentials: "include",
      }).then(async (response) => {
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail ?? "Request failed");
        }
        return data;
      });
      resetPromptForm();
      await refreshPromptEntries();
    } catch (error) {
      errorMessage = error.message ?? "프롬프트 삭제에 실패했습니다.";
    } finally {
      busy = false;
    }
  }

  async function activatePromptVersion(version) {
    if (!selectedPromptId) return;

    busy = true;
    errorMessage = "";
    try {
      const updated = await postJson(`/api/prompts/${selectedPromptId}/activate-version`, { version });
      await refreshPromptEntries();
      loadPrompt(updated);
    } catch (error) {
      errorMessage = error.message ?? "프롬프트 버전 활성화에 실패했습니다.";
    } finally {
      busy = false;
    }
  }

  async function loadRun(runId) {
    busy = true;
    errorMessage = "";
    try {
      const response = await fetch(`/api/runs/${runId}`);
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Request failed");
      }
      selectedRun = data;
    } catch (error) {
      errorMessage = error.message ?? "실행 이력 조회에 실패했습니다.";
    } finally {
      busy = false;
    }
  }
</script>

<svelte:head>
  <title>JARVIS Admin Console</title>
</svelte:head>

<div class="admin-shell">
  <section class="admin-panel">
    <div class="admin-header">
      <div>
        <p class="eyebrow">ADMIN CONSOLE</p>
        <h1>JARVIS Admin</h1>
        <p class="sidebar-copy">MCP 레지스트리와 LLM 프롬프트를 관리하는 관리자 화면입니다.</p>
      </div>
      <a class="ghost-button admin-link" href="/">JARVIS로 돌아가기</a>
    </div>

    <div class="admin-tab-row">
      <button class:active={activeTab === "registry"} class="tab-button" on:click={() => (activeTab = "registry")}>
        MCP Registry
      </button>
      <button class:active={activeTab === "prompts"} class="tab-button" on:click={() => (activeTab = "prompts")}>
        Prompt DB
      </button>
      <button class:active={activeTab === "runs"} class="tab-button" on:click={() => (activeTab = "runs")}>
        Runs
      </button>
    </div>

    {#if errorMessage}
      <div class="admin-error">{errorMessage}</div>
    {/if}

    {#if activeTab === "registry"}
      <div class="admin-grid">
        <article class="mcp-detail-card">
          <div class="mcp-item-header">
            <span class="mcp-name">Registry Control</span>
            <span class="mcp-badge">{registryEntries.length}개</span>
          </div>
          <div class="registry-list">
            {#each registryEntries as entry}
              <div class={`registry-item ${entry.enabled ? "enabled" : "disabled"}`}>
                <div class="mcp-item-header">
                  <span class="mcp-name">{entry.name}</span>
                  <span class="mcp-badge">{entry.enabled ? "활성" : "비활성"}</span>
                </div>
                <p class="mcp-detail-copy">{entry.description}</p>
                <div class="task-meta">id: {entry.id} / scope: {entry.scope}</div>
                <button class="ghost-button registry-button" disabled={busy} on:click={() => toggleRegistryEntry(entry)}>
                  {entry.enabled ? "비활성화" : "활성화"}
                </button>
              </div>
            {/each}
          </div>
        </article>

        <article class="mcp-detail-card">
          <div class="mcp-item-header">
            <span class="mcp-name">새 MCP 추가</span>
            <span class="mcp-badge">POST</span>
          </div>
          <div class="registry-form">
            <input bind:value={registryForm.id} placeholder="id" />
            <input bind:value={registryForm.name} placeholder="name" />
            <input bind:value={registryForm.scope} placeholder="scope" />
            <textarea bind:value={registryForm.description} rows="3" placeholder="description"></textarea>
            <input bind:value={registryForm.capabilities} placeholder="capability1, capability2" />
            <input bind:value={registryForm.expected_input} placeholder="expected input" />
            <input bind:value={registryForm.expected_output} placeholder="expected output" />
            <button class="primary-button" disabled={busy} on:click={createRegistryEntry}>
              {busy ? "처리 중..." : "MCP 추가"}
            </button>
          </div>
        </article>
      </div>
    {:else if activeTab === "prompts"}
      <div class="admin-grid prompt-grid">
        <article class="mcp-detail-card">
          <div class="mcp-item-header">
            <span class="mcp-name">Prompt Catalog</span>
            <span class="mcp-badge">{promptEntries.length}개</span>
          </div>
          <div class="registry-list">
            {#each promptEntries as entry}
              <button class:selected={selectedPromptId === entry.id} class="prompt-list-item" on:click={() => loadPrompt(entry)}>
                <div class="mcp-item-header">
                  <span class="mcp-name">{entry.name}</span>
                  <span class="mcp-badge">{entry.id}</span>
                </div>
                <p class="mcp-detail-copy">{entry.description}</p>
                <div class="task-meta">updated: {entry.updated_at}</div>
              </button>
            {/each}
          </div>
        </article>

        <article class="mcp-detail-card">
          <div class="mcp-item-header">
            <span class="mcp-name">{selectedPromptId ? "프롬프트 수정" : "새 프롬프트 추가"}</span>
            <span class="mcp-badge">{selectedPromptId ? "PATCH" : "POST"}</span>
          </div>
          <div class="registry-form prompt-form">
            <input bind:value={promptForm.id} disabled={Boolean(selectedPromptId)} placeholder="prompt id" />
            <input bind:value={promptForm.name} placeholder="prompt name" />
            <input bind:value={promptForm.description} placeholder="prompt description" />
            <textarea bind:value={promptForm.content} rows="16" placeholder="prompt content"></textarea>
            <div class="prompt-actions">
              <button class="primary-button" disabled={busy} on:click={savePrompt}>
                {busy ? "처리 중..." : selectedPromptId ? "프롬프트 저장" : "프롬프트 생성"}
              </button>
              <button class="ghost-button" disabled={busy} on:click={resetPromptForm}>
                새 프롬프트
              </button>
              <button class="ghost-button danger-button" disabled={busy || !selectedPromptId} on:click={deletePrompt}>
                프롬프트 삭제
              </button>
            </div>
          </div>
        </article>

        <article class="mcp-detail-card">
          <div class="mcp-item-header">
            <span class="mcp-name">버전 관리</span>
            <span class="mcp-badge">{selectedPrompt?.versions?.length ?? 0}개</span>
          </div>
          {#if selectedPrompt}
            <div class="version-list">
              {#each selectedPrompt.versions.slice().reverse() as version}
                <div class:active={version.version === selectedPrompt.active_version} class="version-item">
                  <div class="mcp-item-header">
                    <span class="mcp-name">v{version.version}</span>
                    <span class="mcp-badge">{version.version === selectedPrompt.active_version ? "활성" : "대기"}</span>
                  </div>
                  <p class="mcp-detail-copy">{version.description}</p>
                  <div class="task-meta">created: {version.created_at}</div>
                  <div class="prompt-actions">
                    <button class="ghost-button" disabled={busy} on:click={() => loadPrompt({ ...selectedPrompt, ...version, active_version: selectedPrompt.active_version })}>
                      내용 보기
                    </button>
                    <button
                      class="primary-button"
                      disabled={busy || version.version === selectedPrompt.active_version}
                      on:click={() => activatePromptVersion(version.version)}
                    >
                      {version.version === selectedPrompt.active_version ? "현재 활성 버전" : "이 버전 활성화"}
                    </button>
                  </div>
                </div>
              {/each}
            </div>
          {:else}
            <div class="task-meta">프롬프트를 선택하면 버전 목록이 표시됩니다.</div>
          {/if}
        </article>
      </div>
    {:else}
      <div class="admin-grid">
        <article class="mcp-detail-card">
          <div class="mcp-item-header">
            <span class="mcp-name">최근 실행</span>
            <span class="mcp-badge">{runEntries.length}개</span>
          </div>
          <div class="registry-list">
            {#each runEntries as entry}
              <button class="prompt-list-item" on:click={() => loadRun(entry.id)}>
                <div class="mcp-item-header">
                  <span class="mcp-name">{entry.planner_type ?? "unknown"}</span>
                  <span class="mcp-badge">{entry.phase}</span>
                </div>
                <p class="mcp-detail-copy">{entry.command_text ?? "command unavailable"}</p>
                <div class="task-meta">run_id: {entry.id}</div>
                <div class="task-meta">fallback: {entry.fallback_used ? "yes" : "no"}</div>
              </button>
            {/each}
          </div>
        </article>

        <article class="mcp-detail-card">
          <div class="mcp-item-header">
            <span class="mcp-name">실행 상세</span>
            <span class="mcp-badge">{selectedRun?.phase ?? "없음"}</span>
          </div>
          {#if selectedRun}
            <p class="mcp-detail-copy">{selectedRun.command_text}</p>
            <div class="task-meta">planner: {selectedRun.planner_type ?? "unknown"}</div>
            <div class="task-meta">fallback: {selectedRun.fallback_used ? "yes" : "no"}</div>
            {#if selectedRun.trace?.length}
              <div class="task-meta"><strong>Trace</strong></div>
              <ul class="item-list compact-list trace-list">
                {#each selectedRun.trace as traceEntry}
                  <li><code>{JSON.stringify(traceEntry)}</code></li>
                {/each}
              </ul>
            {/if}
            {#if selectedRun.report}
              <div class="task-meta"><strong>Report Summary</strong></div>
              <p class="mcp-detail-copy">{selectedRun.report.summary}</p>
            {/if}
          {:else}
            <div class="task-meta">실행 이력을 선택하면 trace와 report를 볼 수 있습니다.</div>
          {/if}
        </article>
      </div>
    {/if}
  </section>
</div>
