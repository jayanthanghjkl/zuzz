/* Files page — every operation goes through the API, which
   goes through the permission gate. Nothing here touches
   the filesystem directly.

   The confirmation flow: POST an operation. A 202 means the
   gate wants a human; show the popup with the threat score,
   file count and size, then re-POST with confirmed:true and
   the confirmation_id. A 403 is the end of the road — there
   is no dialog that can unlock a blocked action. */
(function (global) {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const state = {
    root: ".",
    path: ".",
    selected: null,
    includeHidden: false,
    stopped: false,
    pending: null,
    bulkTotal: 0,
    bulkDone: 0,
  };

  // -- API ------------------------------------------------------------

  async function api(op, body) {
    const response = await fetch("/api/files/" + op, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    let data = {};
    try {
      data = await response.json();
    } catch (err) {
      data = {};
    }
    return { status: response.status, data: data };
  }

  async function getJson(url) {
    const response = await fetch(url);
    return response.json();
  }

  // -- tree -----------------------------------------------------------

  async function loadTree() {
    const tree = $("tree");
    tree.innerHTML = "";
    const base = state.root;
    const response = await api("list", {
      path: base,
      limit: 500,
      include_hidden: state.includeHidden,
    });
    if (response.status !== 200) {
      tree.innerHTML = "<div class='indent-1'>" + base + "</div>";
      return;
    }
    const entries = (response.data.result && response.data.result.entries) || [];
    const rootRow = makeRow(base, true, 0, true);
    tree.appendChild(rootRow);
    for (const entry of entries.filter((e) => e.is_dir)) {
      tree.appendChild(await makeDirRow(entry, 1));
    }
  }

  async function makeDirRow(entry, depth) {
    const row = makeRow(entry.path, true, depth, false, entry);
    row.classList.add("dir");
    return row;
  }

  function makeRow(path, isDir, depth, isRoot, entry) {
    const button = document.createElement("button");
    button.className = "indent-" + Math.min(depth, 3);
    button.textContent = (isRoot ? "▸ " : "") + path.split(/[\\/]/).pop() || path;
    button.addEventListener("click", () => navigate(path));
    return button;
  }

  // -- file list ------------------------------------------------------

  async function navigate(path) {
    state.path = path;
    $("current-path").textContent = path;
    const response = await api("list", {
      path: path,
      limit: 1000,
      include_hidden: state.includeHidden,
    });
    const rows = $("file-rows");
    rows.innerHTML = "";
    if (response.status !== 200) {
      toast(response.data.detail || "cannot list " + path, true);
      return;
    }
    const result = response.data.result || {};
    for (const entry of result.entries || []) {
      const tr = document.createElement("tr");
      if (entry.is_dir) tr.className = "dir-row";
      tr.innerHTML =
        "<td>" + escapeHtml(entry.name) + "</td>" +
        "<td>" + (entry.is_dir ? "—" : formatBytes(entry.size)) + "</td>" +
        "<td>" + (entry.modified || "") + "</td>" +
        "<td>" + escapeHtml(entry.classification || "") + "</td>";
      tr.addEventListener("click", () => select(entry));
      tr.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        select(entry);
        showContextMenu(event.clientX, event.clientY);
      });
      rows.appendChild(tr);
    }
  }

  async function select(entry) {
    state.selected = entry;
    $("preview-title").textContent = entry.name;
    if (entry.is_dir) {
      $("preview-meta").textContent = "directory";
      $("preview-content").textContent = "";
      return;
    }
    const stat = await api("stat", { path: entry.path });
    $("preview-meta").textContent =
      stat.status === 200 ? JSON.stringify(stat.data.result, null, 2) : "stat unavailable";
    const read = await api("read", { path: entry.path });
    if (read.status === 202) {
      const confirmed = await confirmOperation(read.data, {
        path: entry.path,
      }, "read");
      if (!confirmed) return;
    }
    if (read.status === 200) {
      const text = String(read.data.result || "");
      const lines = text.split("\n");
      $("preview-content").textContent =
        lines.length > 100
          ? lines.slice(0, 50).join("\n") +
            "\n… (" + (lines.length - 100) + " lines hidden) …\n" +
            lines.slice(-50).join("\n")
          : text;
    } else if (read.status === 413) {
      $("preview-content").textContent = "file too large to preview — use stat";
    } else if (read.status === 400) {
      $("preview-content").textContent = "binary file — not previewable as text";
    } else {
      $("preview-content").textContent = read.data.detail || "cannot read";
    }
    loadBackups(entry.path);
  }

  // -- operations -----------------------------------------------------

  function showContextMenu(x, y) {
    const menu = $("context-menu");
    menu.classList.remove("hidden");
    menu.style.left = x + "px";
    menu.style.top = y + "px";
  }

  document.addEventListener("click", () => {
    $("context-menu").classList.add("hidden");
  });

  $("context-menu").addEventListener("click", (event) => {
    const op = event.target.getAttribute("data-op");
    if (!op || !state.selected) return;
    $("context-menu").classList.add("hidden");
    runMenuOperation(op, state.selected);
  });

  async function runMenuOperation(op, entry) {
    if (op === "read") {
      select(entry);
    } else if (op === "write") {
      const content = prompt("New content for " + entry.name + ":", "");
      if (content === null) return;
      const response = await api("write", { path: entry.path, content: content });
      await handleGated(response, { path: entry.path, content: content }, "write");
    } else if (op === "copy" || op === "move") {
      const dst = prompt("Destination for " + entry.name + ":", "");
      if (!dst) return;
      const response = await api(op, { src: entry.path, dst: dst });
      await handleGated(response, { src: entry.path, dst: dst }, op);
    } else if (op === "rename") {
      const name = prompt("New name for " + entry.name + ":", entry.name);
      if (!name) return;
      const response = await api("rename", { path: entry.path, new_name: name });
      await handleGated(response, { path: entry.path, new_name: name }, "rename");
    } else if (op === "delete") {
      const response = await api("delete", { path: entry.path });
      await handleGated(response, { path: entry.path }, "delete");
    }
    navigate(state.path);
  }

  // The one flow every gated operation follows: ask, maybe
  // confirm, then look at the answer.
  async function handleGated(response, payload, op) {
    if (response.status === 202) {
      const ok = await confirmOperation(response.data, payload, op);
      if (!ok) {
        toast("cancelled");
        return null;
      }
      const retry = await api(op, {
        ...payload,
        confirmed: true,
        confirmation_id: response.data.confirmation_id,
      });
      if (retry.status === 200) {
        toast(op + " confirmed and executed");
        return retry.data.result;
      }
      toast(retry.data.detail || op + " refused", true);
      return null;
    }
    if (response.status === 200 && response.data.status === "dry_run") {
      showPlan(response.data);
      return null;
    }
    if (response.status === 200) {
      toast(op + " done");
      return response.data.result;
    }
    toast(response.data.detail || op + " failed (HTTP " + response.status + ")", true);
    return null;
  }

  // -- confirmation popup ---------------------------------------------

  function confirmOperation(confirmation, payload, op) {
    return new Promise((resolve) => {
      const modal = $("modal");
      const details = $("modal-details");
      $("modal-title").textContent = "Confirm: " + confirmation.operation;
      const threat = confirmation.threat || {};
      const rows = [
        ["Operation", confirmation.operation],
        ["Target", confirmation.target],
        ["Files affected", confirmation.file_count == null ? "—" : confirmation.file_count],
        ["Total size", confirmation.total_size_human || "—"],
        ["Reversible", confirmation.reversible ? "yes (trash/backup)" : "no"],
        ["Threat score", threat.score + "/10 (" + threat.level + ")"],
        ["Trust level required", "L" + confirmation.trust_level],
        ["Why", confirmation.reason],
      ];
      details.innerHTML = rows
        .map(
          ([key, value]) =>
            "<dt>" + escapeHtml(key) + "</dt><dd class='" +
            (key === "Threat score" && (threat.level === "dangerous" || threat.level === "forbidden")
              ? "threat-" + threat.level
              : "") +
            "'>" + escapeHtml(String(value)) + "</dd>"
        )
        .join("");
      $("modal-plan").classList.add("hidden");
      $("modal-dry-run").checked = false;
      const confirmButton = $("modal-confirm");
      const cancelButton = $("modal-cancel");
      const dryRun = $("modal-dry-run");
      function finish(answer) {
        modal.classList.add("hidden");
        confirmButton.removeEventListener("click", onConfirm);
        cancelButton.removeEventListener("click", onCancel);
        dryRun.removeEventListener("change", onDryRun);
        resolve(answer);
      }
      function onConfirm() {
        if (dryRun.checked) {
          finish("dry-run");
          return;
        }
        finish(true);
      }
      function onCancel() {
        finish(false);
      }
      function onDryRun() {
        confirmButton.textContent = dryRun.checked ? "Preview" : "Confirm";
      }
      confirmButton.addEventListener("click", onConfirm);
      cancelButton.addEventListener("click", onCancel);
      dryRun.addEventListener("change", onDryRun);
      confirmButton.textContent = "Confirm";
      modal.classList.remove("hidden");
      // A dry run only asks; it never executes.
      modal.dataset.op = op;
      modal.dataset.payload = JSON.stringify(payload);
      modal.onceDryRun = async function () {
        if (dryRun.checked) {
          const plan = await api(op, { ...payload, dry_run: true });
          showPlan(plan.data);
        }
      };
    }).then(async (answer) => {
      if (answer === "dry-run") {
        const plan = await api(op, { ...payload, dry_run: true });
        showPlan(plan.data);
        return false;
      }
      return answer;
    });
  }

  function showPlan(plan) {
    const modal = $("modal");
    $("modal-title").textContent = "Dry run — nothing was executed";
    const details = $("modal-details");
    details.innerHTML =
      "<dt>Decision</dt><dd>" + escapeHtml(plan.decision.decision) + "</dd>" +
      "<dt>Reason</dt><dd>" + escapeHtml(plan.decision.reason) + "</dd>" +
      "<dt>Threat</dt><dd>" + plan.threat.score + "/10 (" + plan.threat.level + ")</dd>";
    $("modal-plan").textContent = JSON.stringify(plan.plan, null, 2);
    $("modal-plan").classList.remove("hidden");
    $("modal-confirm").textContent = "Close";
    $("modal-cancel").classList.add("hidden");
    $("modal-dry-run").classList.add("hidden");
    $("modal-confirm").onclick = () => {
      modal.classList.add("hidden");
      $("modal-cancel").classList.remove("hidden");
      $("modal-dry-run").classList.remove("hidden");
    };
    modal.classList.remove("hidden");
  }

  // -- bulk operations with progress ----------------------------------

  async function bulkDelete(paths) {
    state.bulkTotal = paths.length;
    state.bulkDone = 0;
    $("progress").classList.remove("hidden");
    for (const path of paths) {
      const response = await api("delete", { path: path });
      if (response.status === 202) {
        const ok = await confirmOperation(response.data, { path: path }, "delete");
        if (!ok) continue;
        await api("delete", {
          path: path,
          confirmed: true,
          confirmation_id: response.data.confirmation_id,
        });
      }
      state.bulkDone += 1;
      updateProgress();
    }
    $("progress").classList.add("hidden");
    navigate(state.path);
  }

  function updateProgress() {
    const percent =
      state.bulkTotal === 0
        ? 100
        : Math.round((state.bulkDone / state.bulkTotal) * 100);
    $("progress-bar").style.width = percent + "%";
    $("progress-label").textContent =
      state.bulkDone + " / " + state.bulkTotal + " (" + percent + "%)";
  }

  // -- trash ----------------------------------------------------------

  async function openTrash() {
    const data = await getJson("/api/files/trash");
    $("trash-stats").textContent =
      data.stats.count + " items, " +
      formatBytes(data.stats.bytes) +
      (data.stats.warn ? " — trash is over 1 GB, consider emptying it" : "");
    const list = $("trash-list");
    list.innerHTML = "";
    for (const item of data.items || []) {
      const li = document.createElement("li");
      const span = document.createElement("span");
      span.className = "path";
      span.textContent =
        item.original_path + " (" + formatBytes(item.size) + ", " +
        item.age_days + " days old)";
      const restore = document.createElement("button");
      restore.textContent = "Restore";
      restore.addEventListener("click", async () => {
        const response = await api("restore", { trash_id: item.trash_id });
        await handleGated(response, { trash_id: item.trash_id }, "restore");
        openTrash();
      });
      li.appendChild(span);
      li.appendChild(restore);
      list.appendChild(li);
    }
    $("trash-modal").classList.remove("hidden");
  }

  $("trash-btn").addEventListener("click", openTrash);
  $("trash-close").addEventListener("click", () =>
    $("trash-modal").classList.add("hidden"));
  $("empty-trash").addEventListener("click", async () => {
    const response = await api("empty-trash", { older_than_days: 0 });
    await handleGated(response, { older_than_days: 0 }, "empty-trash");
    openTrash();
  });

  // -- backups --------------------------------------------------------

  async function loadBackups(path) {
    const list = $("backups-list");
    list.innerHTML = "";
    const data = await getJson("/api/files/backups?path=" + encodeURIComponent(path));
    if (!data.backups || !data.backups.length) return;
    const heading = document.createElement("div");
    heading.textContent = "Backups (" + data.backups.length + ")";
    list.appendChild(heading);
    for (const backup of data.backups) {
      const row = document.createElement("div");
      row.textContent = backup.split(/[\\/]/).pop();
      const restore = document.createElement("button");
      restore.textContent = "Restore";
      restore.addEventListener("click", async () => {
        const version = parseInt(backup.split(".").pop(), 10);
        const response = await api("restore-backup", {
          path: path,
          backup_version: version,
        });
        await handleGated(response, { path: path, backup_version: version }, "restore-backup");
        loadBackups(path);
      });
      row.appendChild(restore);
      list.appendChild(row);
    }
  }

  $("backups-btn").addEventListener("click", () => {
    if (state.selected) loadBackups(state.selected.path);
  });

  // -- stop switch ----------------------------------------------------

  async function refreshStop() {
    try {
      const status = await getJson("/api/kill-switch");
      state.stopped = status.engaged;
      $("kill-badge").textContent = status.engaged ? "stopped" : "running";
      $("kill-badge").className =
        "badge " + (status.engaged ? "badge-err" : "badge-ok");
      $("stop-banner").classList.toggle("hidden", !status.engaged);
    } catch (err) {
      $("kill-badge").textContent = "offline";
    }
  }

  $("stop-btn").addEventListener("click", async () => {
    await fetch("/api/kill-switch/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "stopped from the Files page", source: "ui" }),
    });
    refreshStop();
  });
  setInterval(refreshStop, 5000);

  // -- wiring ---------------------------------------------------------

  $("tree-go").addEventListener("click", () => {
    state.root = $("tree-root").value || ".";
    loadTree();
  });
  $("show-hidden").addEventListener("change", (event) => {
    state.includeHidden = event.target.checked;
    navigate(state.path);
  });

  function escapeHtml(text) {
    return String(text).replace(
      /[&<>"']/g,
      (char) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char])
    );
  }

  function formatBytes(bytes) {
    if (bytes == null) return "—";
    for (const unit of ["B", "KB", "MB", "GB", "TB"]) {
      if (bytes < 1024 || unit === "TB") {
        return (Math.round(bytes * 10) / 10) + " " + unit;
      }
      bytes /= 1024;
    }
    return bytes + " TB";
  }

  function toast(message, isError) {
    const element = $("toast");
    element.textContent = message;
    element.classList.toggle("error", Boolean(isError));
    element.classList.remove("hidden");
    setTimeout(() => element.classList.add("hidden"), 4000);
  }

  refreshStop();
  loadTree();
  navigate(".");
})(window);
