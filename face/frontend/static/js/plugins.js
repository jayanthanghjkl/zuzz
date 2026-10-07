/* Plugins page — installed, scan, approve. */

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  async function getJSON(path) {
    const res = await fetch(path);
    return await res.json();
  }

  async function postJSON(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return { status: res.status, data: await res.json().catch(() => ({})) };
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  let toastTimer = null;

  function toast(message, kind) {
    const node = $("toast");
    node.textContent = message;
    node.className = "toast" + (kind ? " " + kind : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      node.className = "toast hidden";
    }, 4000);
  }

  function renderPlugins(data) {
    const node = $("plugin-list");
    const installed = data.installed || [];
    const files = data.files_on_disk || [];
    if (!installed.length && !files.length) {
      node.innerHTML = '<p class="prose">No plugins installed yet.</p>';
      return;
    }
    let html = "";
    installed.forEach(function (p) {
      html += '<div class="plugin-card">' +
        '<div class="plugin-name">' + escapeHtml(p.name || p) + "</div>" +
        '<div class="plugin-meta">' + escapeHtml(p.version || "") + "</div></div>";
    });
    files.forEach(function (f) {
      html += '<div class="plugin-card plugin-pending">' +
        '<div class="plugin-name">' + escapeHtml(f) + "</div>" +
        '<div class="plugin-meta">unregistered</div></div>";
    });
    node.innerHTML = html;
  }

  async function load() {
    const data = await getJSON("/api/plugins");
    if (data) renderPlugins(data);
  }

  async function scan() {
    const result = await postJSON("/api/plugins/scan");
    if (result.status === 200) toast("scan complete: " + (result.data.unregistered || []).length + " new", "ok");
    else toast("scan failed", "bad");
    load();
  }

  function wire() {
    $("scan-plugins").addEventListener("click", scan);
  }

  document.addEventListener("DOMContentLoaded", function () {
    wire();
    load();
  });
})(window);
