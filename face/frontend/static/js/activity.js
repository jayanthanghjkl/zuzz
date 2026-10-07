/* Activity page — real-time action log with filters. */

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  async function getJSON(path) {
    const res = await fetch(path);
    return await res.json();
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

  function render(entries) {
    const node = $("activity-list");
    if (!entries || !entries.length) {
      node.innerHTML = '<p class="prose">No activity.</p>';
      return;
    }
    node.innerHTML = entries
      .map(function (e) {
        const icon = e.level === "error" ? "&#10005;" : e.level === "warn" ? "&#9888;" : "&#10003;";
        return (
          '<div class="activity-row">' +
          '<span class="activity-icon">' + icon + "</span>" +
          '<span class="activity-text">' + escapeHtml(e.message || e.action || String(e)) + "</span>" +
          '<span class="activity-time">' + escapeHtml(e.age || "") + "</span></div>"
        );
      })
      .join("");
  }

  async function load() {
    const cat = $("filter-cat").value;
    const since = $("filter-since").value;
    const params = new URLSearchParams();
    if (cat) params.set("category", cat);
    if (since) params.set("since", since);
    const qs = params.toString();
    const data = await getJSON("/api/brain/activity" + (qs ? "?" + qs : ""));
    render((data && data.entries) || []);
  }

  function exportCSV() {
    const rows = document.querySelectorAll("#activity-list .activity-row");
    let csv = "time,level,message\n";
    rows.forEach(function (r) {
      const text = r.querySelector(".activity-text") ? r.querySelector(".activity-text").textContent : "";
      const time = r.querySelector(".activity-time") ? r.querySelector(".activity-time").textContent : "";
      csv += '"' + time.replace(/"/g, '""') + '","","' + text.replace(/"/g, '""') + '"\n';
    });
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "zuzz-activity.csv";
    a.click();
    URL.revokeObjectURL(url);
    toast("exported", "ok");
  }

  function wire() {
    $("refresh-activity").addEventListener("click", load);
    $("export-activity").addEventListener("click", exportCSV);
    $("filter-cat").addEventListener("change", load);
    $("filter-since").addEventListener("change", load);
  }

  document.addEventListener("DOMContentLoaded", function () {
    wire();
    load();
  });
})(window);
