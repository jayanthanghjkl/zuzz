/* Background agents page */

(function (global) {
  "use strict";

  const $ = (id) => document.getElementById(id);
  let agents = [];

  async function api(method, path, body) {
    const response = await fetch(path, {
      method: method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try { data = await response.json(); } catch (err) { data = {}; }
    return { status: response.status, data };
  }

  function showResult(id, text) {
    const el = $(id);
    el.textContent = text;
    el.classList.remove("hidden");
  }

  async function loadAgents() {
    const res = await api("GET", "/api/agents/list");
    if (res.status === 200) {
      agents = res.data.agents || [];
      renderAgents();
    }
  }

  function renderAgents() {
    const container = $("agent-list");
    if (!agents.length) {
      container.innerHTML = "<p class='muted'>No agents deployed yet.</p>";
      return;
    }
    container.innerHTML = agents.map(a => `
      <div class="agent-card" data-id="${a.agent_id}">
        <div>
          <strong>${a.name}</strong>
          <span class="muted">(${a.agent_type})</span>
        </div>
        <span class="status ${a.status}">${a.status}</span>
      </div>
    `).join("");
  container.querySelectorAll(".agent-card").forEach(card => {
    card.addEventListener("click", (e) => {
      if (e.target.dataset.action === "stop") {
        e.stopPropagation();
        stopAgent(e.target.dataset.agentId);
      } else {
        showDetails(card.dataset.id);
      }
    });
  });
  }

  async function showDetails(agentId) {
    const res = await api("GET", "/api/agents/" + agentId + "/status");
    if (res.status === 200) {
      const a = res.data;
      $("agent-details").innerHTML = `
        <p><strong>ID:</strong> ${a.agent_id}</p>
        <p><strong>Name:</strong> ${a.name}</p>
        <p><strong>Status:</strong> ${a.status}</p>
        <p><strong>Type:</strong> ${a.agent_type}</p>
        <p><strong>Schedule:</strong> ${a.schedule || "none"}</p>
        <p><strong>Colab:</strong> ${a.colab_url || "not connected"}</p>
        <button class="ghost-btn" data-action="stop" data-agent-id="${a.agent_id}">Stop</button>
      `;
    }
  }

  async function deployAgent() {
    const type = $("agent-type").value;
    const name = $("agent-name").value.trim() || type;
    const body = { name: name, agent_type: type };
    if (type === "custom") {
      body.code = $("agent-code").value;
      body.agent_type = "custom";
    }
    const res = await api("POST", "/api/agents/deploy", body);
    if (res.status === 200) {
      showResult("deploy-result", "Deployed: " + res.data.agent_id);
      loadAgents();
    } else {
      showResult("deploy-result", "Failed: " + (res.data.detail || res.status));
    }
  }

  async function stopAgent(agentId) {
    const res = await api("POST", "/api/agents/stop?agent_id=" + encodeURIComponent(agentId), {});
    if (res.status === 200) {
      showResult("deploy-result", "Stopped: " + agentId);
      loadAgents();
    } else {
      showResult("deploy-result", "Failed: " + (res.data.detail || res.status));
    }
  }

  async function scheduleAgent() {
    const agentId = $("schedule-agent-id").value.trim();
    const cron = $("cron-expression").value.trim();
    if (!agentId || !cron) return;
    const res = await api("POST", "/api/agents/schedule?agent_id=" + encodeURIComponent(agentId), { cron_expression: cron });
    if (res.status === 200) {
      showResult("schedule-result", "Scheduled: " + res.data.schedule);
    } else {
      showResult("schedule-result", "Failed: " + (res.data.detail || res.status));
    }
  }

  $("agent-type").addEventListener("change", (e) => {
    const codeBox = $("agent-code");
    codeBox.classList.toggle("hidden", e.target.value !== "custom");
  });

  $("refresh-agents").addEventListener("click", loadAgents);
  $("deploy-btn").addEventListener("click", deployAgent);
  $("schedule-btn").addEventListener("click", scheduleAgent);

  loadAgents();
})(this);
