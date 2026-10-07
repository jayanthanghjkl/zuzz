/* Dashboard — live system overview. */

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const state = {
    timer: null,
  };

  async function getJSON(path) {
    try {
      const res = await fetch(path);
      return await res.json();
    } catch (err) {
      return null;
    }
  }

  function toast(message, kind) {
    const node = $("toast");
    node.textContent = message;
    node.className = "toast" + (kind ? " " + kind : "");
    setTimeout(function () {
      node.className = "toast hidden";
    }, 4000);
  }

  function pct(used, total) {
    if (!total || total <= 0) return 0;
    return Math.max(0, Math.min(100, Math.round((used / total) * 100)));
  }

  function fmtMB(mb) {
    if (mb == null) return "?";
    if (mb >= 1024) return (mb / 1024).toFixed(1) + " GB";
    return Math.round(mb) + " MB";
  }

  function regionClass(name) {
    const n = name.toLowerCase();
    if (n.includes("error") || n.includes("blocked")) return "status-err";
    if (n.includes("warn") || n.includes("pressure")) return "status-warn";
    if (n.includes("active") || n.includes("processing")) return "status-ok";
    return "status-idle";
  }

  function renderRegions(regions) {
    const node = $("brain-regions");
    if (!regions || !regions.length) {
      node.innerHTML = '<p class="prose">No region data yet.</p>';
      return;
    }
    node.innerHTML = regions
      .map(function (r) {
        return (
          '<div class="region">' +
          '<span class="region-name">' + escapeHtml(r.name || r) + "</span>" +
          '<span class="region-status ' + regionClass(String(r.status || r.state || "idle")) + '">' +
          escapeHtml(String(r.status || r.state || "idle")) +
          "</span></div>"
        );
      })
      .join("");
  }

  function renderResources(res) {
    if (!res) return;
    const vramUsed = res.vram_used_mb || res.get("vram_used_mb", 0);
    const vramTotal = res.vram_total_mb || res.get("vram_total_mb", 0);
    const ramUsed = res.ram_mb || res.get("ram_mb", 0);
    const ramTotal = 16384;
    const cpu = res.cpu_percent || res.get("cpu_percent", 0);
    const gpu = res.gpu_util_percent || res.get("gpu_util_percent", 0);

    $("bar-vram").style.width = pct(vramUsed, vramTotal) + "%";
    $("val-vram").textContent = fmtMB(vramUsed) + " / " + fmtMB(vramTotal);

    $("bar-ram").style.width = pct(ramUsed, ramTotal) + "%";
    $("val-ram").textContent = fmtMB(ramUsed) + " / " + fmtMB(ramTotal);

    $("bar-cpu").style.width = pct(cpu, 100) + "%";
    $("val-cpu").textContent = Math.round(cpu) + "%";

    $("bar-gpu").style.width = pct(gpu, 100) + "%";
    $("val-gpu").textContent = Math.round(gpu) + "%";

    if (res.uptime_seconds != null) {
      const m = Math.floor(res.uptime_seconds / 60);
      const s = Math.floor(res.uptime_seconds % 60);
      $("resource-uptime").textContent = "uptime " + m + "m " + s + "s";
    }
  }

  function renderActivity(entries) {
    const node = $("recent-activity");
    if (!entries || !entries.length) {
      node.innerHTML = '<p class="prose">No activity yet.</p>';
      return;
    }
    node.innerHTML = entries
      .map(function (e) {
        const icon = e.level === "error" ? "&#10005;" : e.level === "warn" ? "&#9888;" : "&#10003;";
        const ago = e.age || "";
        return (
          '<div class="activity-row">' +
          '<span class="activity-icon">' + icon + "</span>" +
          '<span class="activity-text">' + escapeHtml(e.message || e.action || String(e)) + "</span>" +
          '<span class="activity-time">' + escapeHtml(ago) + "</span></div>"
        );
      })
      .join("");
  }

  function renderTasks(tasks) {
    const node = $("active-tasks");
    $("task-count").textContent = tasks ? tasks.length + " running" : "";
    if (!tasks || !tasks.length) {
      node.innerHTML = '<p class="prose">No active tasks.</p>';
      return;
    }
    node.innerHTML = tasks
      .map(function (t) {
        return (
          '<div class="task">' +
          '<span class="task-name">' + escapeHtml(t.name || t.id || "task") + "</span>" +
          '<span class="task-meta">' + escapeHtml(t.status || "") + "</span></div>"
        );
      })
      .join("");
  }

  function renderAgents(agents) {
    const node = $("running-agents");
    $("agent-count").textContent = agents ? agents.length + " active" : "";
    if (!agents || !agents.length) {
      node.innerHTML = '<p class="prose">No agents running.</p>';
      return;
    }
    node.innerHTML = agents
      .map(function (a) {
        return (
          '<div class="agent">' +
          '<span class="agent-name">' + escapeHtml(a.name || a.role || "agent") + "</span>" +
          '<span class="agent-meta">' + escapeHtml(a.provider || a.location || "") + "</span></div>"
        );
      })
      .join("");
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function load() {
    const data = await getJSON("/api/dashboard");
    if (!data) {
      toast("could not reach ZUZZ", "bad");
      return;
    }

    const brain = data.brain || {};
    $("health-score").textContent = data.health_score != null ? data.health_score : "?";

    if (brain.kill_switch && brain.kill_switch.engaged) {
      $("status-badge").textContent = "Stopped";
      $("status-badge").className = "badge badge-err";
    } else {
      $("status-badge").textContent = "Online";
      $("status-badge").className = "badge badge-ok";
    }

    const trust = (brain.thalamus && brain.thalamus.trust_level) || 3;
    $("trust-badge").textContent = "L" + trust;

    const regions = (brain.thalamus && brain.thalamus.regions) || [];
    renderRegions(regions);
    renderResources(data.resources || brain.heartbeat || {});
    renderActivity(data.activity || []);
    renderTasks((brain.thalamus && brain.thalamus.active_tasks) || []);
    renderAgents((brain.thalamus && brain.thalamus.agents) || []);
  }

  async function engageKill() {
    const res = await fetch("/api/kill-switch/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "dashboard kill switch", source: "ui" }),
    });
    if (res.status === 200) {
      toast("kill switch engaged", "bad");
      load();
    } else {
      toast("stop failed", "bad");
    }
  }

  function wire() {
    $("kill-btn").addEventListener("click", function () {
      if (confirm("Engage the emergency kill switch? This stops all ZUZZ activity.")) {
        engageKill();
      }
    });
    $("refresh-activity").addEventListener("click", load);
  }

  document.addEventListener("DOMContentLoaded", function () {
    wire();
    load();
    state.timer = setInterval(load, 2000);
  });
})(window);
