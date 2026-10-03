/* Desktop page — every operation goes through the API, which
   goes through the permission gate. Nothing here moves the mouse
   or sends a key: the browser cannot, and that is the point.

   The confirmation flow is the Files page's flow. POST an
   operation. A 202 means the gate wants a human; show the popup
   with the description and the reason, then re-POST with
   confirmed:true and the confirmation_id. A 403 is the end of the
   road — the Body refused on its own classification, and no
   amount of re-posting changes that.

   The canvas preview is a *drawing aid*, not a remote control:
   clicking it picks a coordinate to send to the gate, and the
   coordinate is validated against every attached monitor before
   anything moves. It never reports the live cursor position,
   because the browser is not permitted to ask. */

(function (global) {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const state = {
    layout: null,
    point: { x: 0, y: 0 },
    stopped: false,
    pending: null,
    clipboard: "",
  };

  // -- API ------------------------------------------------------------

  async function post(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return unwrap(response);
  }

  async function get(path) {
    return unwrap(await fetch(path));
  }

  async function del(path) {
    return unwrap(await fetch(path, { method: "DELETE" }));
  }

  async function unwrap(response) {
    let data = {};
    try {
      data = await response.json();
    } catch (err) {
      data = {};
    }
    return { status: response.status, data: data };
  }

  // -- chrome ---------------------------------------------------------

  let toastTimer = null;

  function toast(message, kind) {
    const node = $("toast");
    node.textContent = message;
    node.className = "toast" + (kind ? " " + kind : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      node.className = "toast hidden";
    }, 6000);
  }

  function explain(result) {
    /* One place that turns an HTTP status into English, so the
       refusal reasons the Body worked to produce actually reach
       the user instead of collapsing into "error". */
    const detail = (result.data && result.data.detail) || "";
    if (result.status === 200) return null;
    if (result.status === 202) return null;
    if (result.status === 403) return detail || "refused";
    if (result.status === 404) return detail || "nothing matched";
    if (result.status === 409) return detail || "the kill switch is engaged";
    if (result.status === 503) return detail || "the desktop refused";
    if (result.status === 400) return detail || "that request is not valid";
    return detail || "request failed (" + result.status + ")";
  }

  function blocked(kind) {
    state.stopped = kind === "stop";
    $("stop-banner").classList.toggle("hidden", !state.stopped);
    $("kill-badge").textContent = state.stopped ? "stopped" : "running";
    $("kill-badge").className = "badge " + (state.stopped ? "badge-stop" : "badge-ok");
  }

  function warn(message) {
    const node = $("warn-banner");
    node.classList.toggle("hidden", !message);
    $("warn-text").textContent = message || "";
  }

  // -- the confirmation popup ----------------------------------------

  function openModal(pending) {
    state.pending = pending;
    $("modal-title").textContent = "Confirm desktop action";
    const details = $("modal-details");
    details.innerHTML = "";
    const plan = pending.data.plan || {};
    addDetail(details, "Action", plan.description || pending.target);
    addDetail(details, "Target", pending.target || "");
    addDetail(details, "Decision", (pending.data.decision || {}).decision || "ask");
    addDetail(details, "Trust level", String((pending.data.decision || {}).trust_level || ""));
    $("modal-reason").textContent = (pending.data.decision || {}).reason || "";
    $("modal-plan").classList.add("hidden");
    $("modal-dry-run").checked = false;
    $("modal").classList.remove("hidden");
  }

  function addDetail(list, label, value) {
    if (!value) return;
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = value;
    list.appendChild(term);
    list.appendChild(detail);
  }

  function closeModal() {
    state.pending = null;
    $("modal").classList.add("hidden");
  }

  /* Run one operation, and handle the three possible answers. */
  async function run(path, body) {
    const result = await post(path, body);
    if (result.status === 200) {
      closeModal();
      toast("done", "ok");
      await refresh();
      return result;
    }
    if (result.status === 202) {
      openModal({
        path: path,
        body: body,
        target: (result.data.decision || {}).target || "",
        data: result.data,
      });
      return result;
    }
    const reason = explain(result);
    toast(reason, "bad");
    warn(reason);
    return result;
  }

  // -- the screen preview --------------------------------------------

  function drawScreen() {
    const canvas = $("screen");
    if (!state.layout || !canvas.getContext) return;
    const ctx = canvas.getContext("2d");
    const desktop = state.layout.desktop;
    canvas.width = 960;
    canvas.height = Math.max(120, Math.round((960 * desktop.height) / desktop.width));

    ctx.fillStyle = "#12141a";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    /* Every monitor is drawn, so a point on a secondary display is
       visibly a point on a second screen rather than something off
       the edge of a drawing. */
    state.layout.monitors.forEach(function (monitor) {
      const sx = ((monitor.left - desktop.left) / desktop.width) * canvas.width;
      const sy = ((monitor.top - desktop.top) / desktop.height) * canvas.height;
      const sw = (monitor.width / desktop.width) * canvas.width;
      const sh = (monitor.height / desktop.height) * canvas.height;
      ctx.fillStyle = monitor.primary ? "#1b1f2a" : "#191d26";
      ctx.fillRect(sx, sy, sw, sh);
      ctx.strokeStyle = monitor.primary ? "#3a4257" : "#2c3242";
      ctx.lineWidth = 2;
      ctx.strokeRect(sx + 1, sy + 1, sw - 2, sh - 2);
    });

    const px = ((state.point.x - desktop.left) / desktop.width) * canvas.width;
    const py = ((state.point.y - desktop.top) / desktop.height) * canvas.height;
    ctx.strokeStyle = "#5b9cff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(px - 12, py);
    ctx.lineTo(px + 12, py);
    ctx.moveTo(px, py - 12);
    ctx.lineTo(px, py + 12);
    ctx.stroke();
    $("pos-value").textContent = state.point.x + ", " + state.point.y;
  }

  function pickPoint(event) {
    if (!state.layout) return;
    const canvas = $("screen");
    const rect = canvas.getBoundingClientRect();
    const desktop = state.layout.desktop;
    const fx = (event.clientX - rect.left) / rect.width;
    const fy = (event.clientY - rect.top) / rect.height;
    const x = Math.round(desktop.left + fx * desktop.width);
    const y = Math.round(desktop.top + fy * desktop.height);
    state.point = { x: x, y: y };
    drawScreen();
  }

  // -- policy panel ---------------------------------------------------

  function fillPolicy(policy) {
    const fill = function (id, items) {
      $(id).innerHTML = (items || [])
        .map(function (item) {
          return "<code>" + escapeHtml(String(item)) + "</code>";
        })
        .join("");
    };
    fill("policy-hotkeys", policy.hotkeys.always_blocked);
    fill("policy-reserved", policy.hotkeys.reserved_by_zuzz);
    fill("policy-never", policy.apps.never_launch);
    fill("policy-processes", policy.windows.protected_processes);
  }

  function escapeHtml(value) {
    return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // -- refresh -------------------------------------------------------

  async function refresh() {
    const layout = await get("/api/hands/layout");
    if (layout.status === 200) {
      state.layout = layout.data.layout;
      const monitors = state.layout.monitors.length;
      $("layout-summary").textContent =
        state.layout.primary.width +
        "x" +
        state.layout.primary.height +
        " · " +
        monitors +
        (monitors === 1 ? " monitor" : " monitors");
      drawScreen();
    }
    const policy = await get("/api/hands/policy");
    if (policy.status === 200) fillPolicy(policy.data.policy);
  }

  async function refreshPosition() {
    const result = await get("/api/hands/position");
    if (result.status === 200 && Array.isArray(result.data.result)) {
      state.point = { x: result.data.result[0], y: result.data.result[1] };
      drawScreen();
    } else if (result.status === 403) {
      warn("reading the cursor needs the read_files permission at this trust level");
    }
  }

  async function pollStop() {
    // The route is `/api/kill-switch`, not `/api/kill-switch/status`. The suffix
    // was never a route, so this 404'd and the stop state never showed — the
    // Emergency Stop button on this tab appeared to do nothing.
    const result = await get("/api/kill-switch");
    if (result.status === 200) {
      blocked(result.data.engaged ? "stop" : "");
    } else {
      // Fail closed. An unreadable switch is reported as engaged rather than as
      // running, because a false "running" indicator is the direction that hurts:
      // the server enforces the real stop either way, so the cost of guessing
      // wrong here is an unusable tab that clears on the next poll.
      blocked("stop");
    }
  }

  // -- wiring ---------------------------------------------------------

  function on(id, handler) {
    const node = $(id);
    if (node) node.addEventListener("click", handler);
  }

  function keys() {
    return $("keys")
      .value.split(",")
      .map(function (key) {
        return key.trim();
      })
      .filter(Boolean);
  }

  function wire() {
    $("screen").addEventListener("click", pickPoint);

    on("pos-refresh", refreshPosition);

    on("op-move", function () {
      run("/api/hands/move", { x: state.point.x, y: state.point.y });
    });
    on("op-click", function () {
      run("/api/hands/click", { x: state.point.x, y: state.point.y, clicks: 1 });
    });
    on("op-double", function () {
      run("/api/hands/click", { x: state.point.x, y: state.point.y, clicks: 2 });
    });
    on("op-right", function () {
      run("/api/hands/click", { x: state.point.x, y: state.point.y, button: "right" });
    });
    on("op-type", function () {
      run("/api/hands/type", { text: $("text").value });
    });
    on("op-hotkey", function () {
      run("/api/hands/hotkey", { keys: keys() });
    });
    on("op-press", function () {
      run("/api/hands/press", { key: keys()[0] || "" });
    });
    on("op-scroll", function () {
      run("/api/hands/scroll", { clicks: -3 });
    });
    on("op-open", function () {
      run("/api/hands/open-app", { name: $("app").value });
    });
    on("op-close", function () {
      run("/api/hands/close-app", { name: $("app").value });
    });
    on("op-focus", function () {
      run("/api/hands/focus-window", { window: $("window").value });
    });

    on("op-clip-get", async function () {
      const result = await get("/api/hands/clipboard");
      if (result.status === 200) {
        state.clipboard = result.data.text;
        $("text").value = result.data.text;
        toast(
          result.data.credential_finding
            ? "clipboard holds " + result.data.credential_finding.replace(/_/g, " ")
            : "clipboard read",
          result.data.credential_finding ? "warn" : "ok"
        );
      } else {
        const reason = explain(result);
        toast(reason, "bad");
        warn(reason);
      }
    });

    on("op-clip-set", function () {
      run("/api/hands/clipboard", { text: $("text").value });
    });

    on("policy-refresh", refresh);

    /* The one endpoint that asks for nothing. It exists because a
       stuck modifier key is a safety problem, and requiring a fresh
       decision to undo damage ZUZZ caused would be backwards. */
    on("release-keys", async function () {
      const result = await post("/api/hands/release-keys", {});
      if (result.status === 200) {
        toast(
          result.data.released.length
            ? "released " + result.data.released.join(", ")
            : "no keys were held",
          "ok"
        );
      } else {
        toast(explain(result), "bad");
      }
    });

    on("modal-cancel", closeModal);
    on("modal-confirm", async function () {
      const pending = state.pending;
      if (!pending) return;
      const dryRun = $("modal-dry-run").checked;
      const result = await post(pending.path, {
        ...pending.body,
        confirmed: true,
        confirmation_id: pending.data.confirmation_id,
        dry_run: dryRun,
      });
      if (result.status === 200) {
        closeModal();
        toast(dryRun ? "dry run complete, nothing was sent" : "done", "ok");
        if (dryRun) {
          $("modal-plan").textContent = JSON.stringify(result.data.plan || {}, null, 2);
        }
      } else {
        toast(explain(result), "bad");
        closeModal();
      }
    });

    on("stop-btn", async function () {
      // `trigger` was never a route either — the real one is `stop`. So the
      // Emergency Stop button on this tab was posting into a 404 and the machine
      // was never actually stopped. `confirmed` is not part of the request body;
      // triggering deliberately needs no confirmation.
      const result = await post("/api/kill-switch/stop", {
        reason: "pressed in the Desktop tab",
        source: "ui",
      });
      if (result.status === 200) {
        blocked("stop");
        toast("stopped — move the mouse into a screen corner to abort anything in flight", "warn");
      } else {
        toast(explain(result), "bad");
      }
    });

    global.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeModal();
    });
  }

  // -- start ----------------------------------------------------------

  document.addEventListener("DOMContentLoaded", function () {
    wire();
    refresh();
    pollStop();
    setInterval(pollStop, 5000);
  });

  global.ZuzzHands = { state: state, refresh: refresh };
})(window);