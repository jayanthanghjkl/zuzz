/* Routines page — view, test, schedule. */

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

  async function load() {
    const data = await getJSON("/api/habits");
    const node = $("routine-list");
    const routines = (data && data.routines) || [];
    if (!routines.length) {
      node.innerHTML = '<p class="prose">No routines yet. Enable Mirror Neurons to start detecting patterns.</p>';
      return;
    }
    node.innerHTML = routines
      .map(function (r) {
        return (
          '<div class="routine">' +
          '<div class="routine-name">' + escapeHtml(r.name || r.id || "routine") + "</div>" +
          '<div class="routine-meta">' + escapeHtml(r.trigger || "") + " — " + (r.enabled ? "enabled" : "disabled") + "</div>" +
          '<div class="btn-row"><button class="ghost-btn test-btn" data-id="' + escapeHtml(r.id || "") + '">Test</button></div></div>"
        );
      })
      .join("");
    node.querySelectorAll(".test-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const id = btn.getAttribute("data-id");
        toast("testing " + id + "...", "ok");
      });
    });
  }

  function wire() {
    $("refresh-routines").addEventListener("click", load);
  }

  document.addEventListener("DOMContentLoaded", function () {
    wire();
    load();
  });
})(window);
