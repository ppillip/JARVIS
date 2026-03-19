<script>
  import { onMount } from "svelte";

  let registryEntries = [];
  let busy = false;
  let form = {
    id: "",
    name: "",
    scope: "",
    description: "",
    capabilities: "",
    expected_input: "",
    expected_output: "",
  };

  onMount(async () => {
    await refreshRegistryEntries();
  });

  async function refreshRegistryEntries() {
    const response = await fetch("/api/registry/mcps");
    registryEntries = await response.json();
  }

  async function postJson(url, body, method = "POST") {
    const response = await fetch(url, {
      method,
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

  async function toggleRegistryEntry(entry) {
    busy = true;
    try {
      await postJson(`/api/registry/mcps/${entry.id}`, { enabled: !entry.enabled }, "PATCH");
      await refreshRegistryEntries();
    } finally {
      busy = false;
    }
  }

  async function createRegistryEntry() {
    if (!form.id.trim() || !form.name.trim() || !form.description.trim()) return;

    busy = true;
    try {
      await postJson("/api/registry/mcps", {
        ...form,
        capabilities: form.capabilities
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      });
      form = {
        id: "",
        name: "",
        scope: "",
        description: "",
        capabilities: "",
        expected_input: "",
        expected_output: "",
      };
      await refreshRegistryEntries();
    } finally {
      busy = false;
    }
  }
</script>

<svelte:head>
  <title>JARVIS Registry Admin</title>
</svelte:head>

<div class="admin-shell">
  <section class="admin-panel">
    <div class="admin-header">
      <div>
        <p class="eyebrow">ADMIN CONSOLE</p>
        <h1>Registry Admin</h1>
        <p class="sidebar-copy">MCP 레지스트리를 활성/비활성하거나 새 항목을 추가하는 관리자 화면입니다.</p>
      </div>
      <a class="ghost-button admin-link" href="/">JARVIS로 돌아가기</a>
    </div>

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
          <input bind:value={form.id} placeholder="id" />
          <input bind:value={form.name} placeholder="name" />
          <input bind:value={form.scope} placeholder="scope" />
          <textarea bind:value={form.description} rows="3" placeholder="description"></textarea>
          <input bind:value={form.capabilities} placeholder="capability1, capability2" />
          <input bind:value={form.expected_input} placeholder="expected input" />
          <input bind:value={form.expected_output} placeholder="expected output" />
          <button class="primary-button" disabled={busy} on:click={createRegistryEntry}>
            {busy ? "처리 중..." : "MCP 추가"}
          </button>
        </div>
      </article>
    </div>
  </section>
</div>
