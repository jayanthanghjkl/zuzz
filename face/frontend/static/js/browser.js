/* Browser page — the window onto ZUZZ's own browser.
 *
 * Nothing here drives a browser. Every control on this page posts an
 * operation to the API and renders whatever comes back, which means the
 * permission flow lives in one place (face/api/browser_support.py) and this
 * file cannot grow a second, looser path into a page.
 *
 * The confirmation flow is the Files and Desktop pages' flow: POST an
 * operation, a 202 means the gate wants a human, show the popup, re-POST
 * with confirmed:true and the confirmation_id. A 403 is the end of the
 * road — the Body refused on its own judgement, and no amount of
 * re-posting changes that.
 *
 * Two 202s need to look different, because they are different questions.
 * An ordinary prompt is asking permission for an act. A prompt carrying
 * `needs_domain_confirmation` is the allowlist asking whether a site the
 * user has never seen should be reachable at all — which is why it gets
 * the "remember this site" checkbox, and why answering it approves the
 * domain as well as the operation.
 *
 * Three panels are deliberately unguarded, because they read ZUZZ's own
 * state and not a page's: session status, the tab list and the holding
 * pen. They name things. Reading a page is still a gated act below.
 */

(function (global) {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const state = {
    stopped: false,
    pending: null,
    sessionId: "",
    tabs: [],
    selected: "",
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

  async function unwrap(response) {
    let data = {};
    try {
      data = await response.json();
    } catch (err) {
      // A screenshot is a PNG, not JSON. Reporting that as a parse failure
      // would make every successful capture look broken.
      return { status: response.status, data: {}, blob: await response.blob() };
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

  /* One place that turns an HTTP status into English, so the refusal
     reasons the Body worked to produce reach the user instead of
     collapsing into "error". 403 is not a failure to try again: it is
     the answer. */
  function explain(result) {
    const detail = (result.data && result.data.detail) || "";
    if (result.status === 403) return detail || "refused";
    if (result.status === 404) return detail || "no such session, tab or element";
    if (result.status === 409) return detail || "the kill switch is engaged";
    if (result.status === 413) return detail || "that file is over the size cap";
    if (result.status === 400) return detail || "that request is not valid";
    if (result.status === 502) return detail || "the page would not load";
    if (result.status === 503) return detail || "Playwright or Chromium is not installed";
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

  function show(title, payload) {
    $("out-title").textContent = title;
    $("out-body").textContent =
      typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
    $("out").classList.remove("hidden");
  }

  function bytes(size) {
    if (!size && size !== 0) return "";
    if (size < 1024) return size + " B";
    if (size < 1048576) return Math.round(size / 1024) + " KB";
    return (size / 1048576).toFixed(1) + " MB";
  }

  // -- the confirmation popup ----------------------------------------

  function addDetail(list, label, value) {
    if (value === undefined || value === null || value === "" || value === false) return;
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = Array.isArray(value) ? value.join(", ") : String(value);
    list.appendChild(term);
    list.appendChild(detail);
  }

  function openModal(pending) {
    state.pending = pending;
    const payload = pending.data || {};

    $("modal-title").textContent = pending.domain
      ? "Confirm a site for the first time"
      : "Confirm browser action";

    /* The payload is the one the Files and Desktop popups already render, so the
       keys are flat: operation, target, trust_level, decision, reason, and the
       threat assessment. Reading it as a nested decision object would show an empty
       target and an empty reason in a prompt the user is being asked to trust. */
    const details = $("modal-details");
    details.innerHTML = "";
    addDetail(details, "Action", pending.operation || payload.operation);
    addDetail(details, "Target", payload.target || pending.target);
    addDetail(details, "Decision", payload.decision);
    addDetail(details, "Trust level", payload.trust_level);
    addDetail(details, "Session", pending.body.session_id);
    addDetail(details, "Detail", payload.detail);
    addDetail(details, "Reads the page", payload.reads_page === true);
    addDetail(details, "Acts on the page", payload.acts_on_page === true);
    addDetail(details, "Writes to disk", payload.writes_disk === true);
    if (payload.threat) addDetail(details, "Threat", payload.threat.level);
    addDetail(details, "Reversible", payload.reversible);
    addDetail(details, "Files", payload.file_count);
    addDetail(details, "Size", payload.total_size_human);

    /* A download needs both of its decisions named. Showing only the URL would
       leave the write invisible, and "write C:\...\quarantine\x.pdf" is exactly the
       line a user needs in order to say yes to a download. */
    if (payload.write_action) {
      addDetail(details, "Also asked", payload.write_action.category + " " +
        payload.write_action.target);
    }
    if (payload.destination) addDetail(details, "Will be written to", payload.destination);
    if (payload.classification) addDetail(details, "Classified as", payload.classification);

    $("modal-reason").textContent = payload.reason || "";

    $("modal-domain-row").classList.toggle("hidden", !pending.domain);
    $("modal-domain").checked = true;
    $("modal-domain-label").textContent = pending.domain
      ? "remember " + pending.domain + " — it will not ask again"
      : "remember this site";
    $("modal-persist").parentElement.classList.toggle("hidden", !pending.domain);
    $("modal-persist").checked = false;
    $("modal-dry-run").checked = false;
    $("modal").classList.remove("hidden");
  }

  function closeModal() {
    state.pending = null;
    $("modal").classList.add("hidden");
  }

  /* One operation, three possible answers: done, needs a human, or
     refused. */
  async function run(path, body) {
    const result = await post(path, body);
    if (result.status === 200) {
      closeModal();
      afterSuccess(path, result);
      return result;
    }
    if (result.status === 202) {
      openModal({
        path: path,
        body: body,
        operation: result.data.operation || "",
        target: result.data.target || "",
        domain: result.data.domain || "",
        data: result.data,
      });
      return result;
    }
    const reason = explain(result);
    toast(reason, "bad");
    warn(reason);
    return result;
  }

  /* Screenshots come back as bytes, so they cannot go through `run`'s
     JSON path. The 202 branch is the same prompt, though — a capture of a
     logged-in page is precisely the thing a user should be asked about
     before it exists. */
  async function capture(body) {
    const response = await fetch("/api/browser/screenshot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (response.status === 202) {
      const payload = await response.json();
      openModal({
        path: "/api/browser/screenshot",
        body: body,
        operation: "screenshot",
        target: payload.target || "",
        domain: "",
        data: payload,
      });
      return;
    }
    if (response.status !== 200) {
      const reason = explain({ status: response.status, data: await safeJson(response) });
      toast(reason, "bad");
      warn(reason);
      return;
    }
    closeModal();
    const stored = response.headers.get("X-Zuzz-Stored") === "1";
    const size = response.headers.get("X-Zuzz-Bytes");
    $("out-title").textContent = stored ? "Screenshot (also filed)" : "Screenshot";
    const node = $("out-body");
    node.textContent = "";
    const img = document.createElement("img");
    img.alt = "the browser page, as ZUZZ sees it";
    img.src = URL.createObjectURL(await response.blob());
    node.appendChild(img);
    const note = document.createElement("div");
    note.className = "dl-meta";
    note.textContent = size + " of PNG" +
      (stored ? ", kept in the screenshot store" : ", held in memory only");
    node.appendChild(note);
    $("out").classList.remove("hidden");
  }

  async function safeJson(response) {
    try {
      return await response.json();
    } catch (err) {
      return {};
    }
  }

  /* What to refresh after something succeeded. Cheap: three gauges. */
  async function afterSuccess(path) {
    if (path.indexOf("/open") > -1 || path.indexOf("/close") > -1) {
      toast("done", "ok");
    }
    await refreshAll();
  }

  // -- status ---------------------------------------------------------

  function fillSession(data) {
    const sessions = data.sessions || [];
    state.sessionId = sessions.length ? sessions[0].session_id : "";
    $("session-summary").textContent = sessions.length
      ? sessions.length + (sessions.length === 1 ? " session" : " sessions") +
        " · " + (sessions[0].headless ? "hidden" : "visible") +
        " · " + (data.open_tabs || 0) + " tab" + (data.open_tabs === 1 ? "" : "s")
      : "no session";

    const policy = data.tabs || {};
    $("tab-summary").textContent = (data.open_tabs || 0) + " of " +
      (policy.hard_cap || 0) + " tabs";

    /* The cap is not advice. It says when ZUZZ is about to warn and when it
       is about to refuse, so the number on screen is the number the Body
       will act on rather than a number that changes in the code. */
    const note = $("tab-note");
    if (!state.sessionId) {
      note.textContent = "Start a browser to open tabs.";
    } else if ((data.open_tabs || 0) >= (policy.soft_warning_at || 3) &&
               (data.open_tabs || 0) < (policy.hard_cap || 5)) {
      note.textContent = "Warning territory. At " + policy.hard_cap +
        " tabs ZUZZ refuses, and a confirmed override is written to the audit log.";
    } else if ((data.open_tabs || 0) >= (policy.hard_cap || 5)) {
      note.textContent = "At the hard cap. A new tab needs its own confirmed decision.";
    } else {
      note.textContent = policy.override_requires_separate_confirmation
        ? "Past the cap, an override needs its own confirmation and is audited."
        : "This session cannot raise the cap.";
    }

    const vision = data.extraction || {};
    $("vision-note").textContent = "reads capped at " +
      (bytes(vision.max_text_chars) || "?") + " of text";
    $("headless").disabled = !data.headless_allowed;
    $("headless").title = data.headless_allowed
      ? "Runs without a window. ZUZZ will not show you what it is looking at."
      : "Disabled: headless runs are refused by configuration.";

    $("dl-summary").textContent = (data.quarantine || 0) +
      " held · cap " + (data.downloads || {}).max_mb + " MB";
  }

  function fillTabs(data) {
    state.tabs = data.sessions || [];
    const list = $("tab-list");
    list.innerHTML = "";
    if (!state.tabs.length) {
      const empty = document.createElement("li");
      empty.className = "empty-note";
      empty.textContent = state.sessionId ? "no tabs" : "no browser session";
      list.appendChild(empty);
      return;
    }
    state.tabs.forEach(function (entry) {
      const row = document.createElement("li");
      if (entry.session_id === state.selected) row.className = "selected";
      if (!entry.active) row.className += " unread";

      const name = document.createElement("span");
      name.className = "tab-title";
      name.textContent = entry.title || "(untitled)";
      name.title = entry.url || "";

      const host = document.createElement("span");
      host.className = "tab-host";
      host.textContent = host_of(entry.url);

      const pick = document.createElement("button");
      pick.className = "ghost-btn";
      pick.type = "button";
      pick.textContent = "select";
      pick.addEventListener("click", function () {
        state.selected = entry.tab_id;
        state.sessionId = entry.session_id;
        refreshTabs();
      });

      row.appendChild(name);
      row.appendChild(host);
      row.appendChild(pick);
      list.appendChild(row);
    });
    if (!state.selected) state.selected = state.tabs[0].tab_id;
  }

  function host_of(url) {
    if (!url) return "";
    try {
      return new URL(url).host;
    } catch (err) {
      return url.slice(0, 40);
    }
  }

  function fillAllowlist(data) {
    const list = $("allow-list");
    list.innerHTML = "";
    const approved = [].concat(data.presets || [], data.user_added || []);
    if (!approved.length) {
      const empty = document.createElement("li");
      empty.className = "empty-note";
      empty.textContent = "nothing approved yet";
      list.appendChild(empty);
      return;
    }
    approved.forEach(function (domain) {
      const row = document.createElement("li");
      const name = document.createElement("span");
      name.className = "tab-title";
      name.textContent = domain;

      const meta = document.createElement("span");
      meta.className = "allow-meta";
      const kept = (data.persist_domains || []).indexOf(domain) > -1;
      meta.textContent = (data.user_added || []).indexOf(domain) > -1 ? "added by you" : "preset";
      if (kept) meta.textContent += " · cookies kept";

      const drop = document.createElement("button");
      drop.className = "ghost-btn danger";
      drop.type = "button";
      drop.textContent = "forget";
      drop.title = "Remove the approval and delete any cookies stored for it";
      drop.addEventListener("click", function () {
        forgetDomain(domain);
      });

      row.appendChild(name);
      row.appendChild(meta);
      row.appendChild(drop);
      list.appendChild(row);
    });
  }

  function fillDownloads(data) {
    const list = $("dl-list");
    list.innerHTML = "";
    if (!data.downloads.length) {
      const empty = document.createElement("li");
      empty.className = "empty-note";
      empty.textContent = "the holding pen is empty";
      list.appendChild(empty);
      return;
    }
    data.downloads.forEach(function (item) {
      const row = document.createElement("li");
      const name = document.createElement("span");
      name.className = "dl-name";
      name.textContent = item.name;
      name.title = item.name;

      const meta = document.createElement("span");
      meta.className = "dl-meta";
      const verdict = (item.classification || {});
      meta.textContent = bytes(item.bytes) +
        (verdict.executable ? " · executable, will not run" : "") +
        (verdict.category ? " · " + verdict.category : "");

      row.appendChild(name);
      row.appendChild(meta);
      list.appendChild(row);
    });
  }

  async function refreshAll() {
    const status = await get("/api/browser/session-status");
    if (status.status === 200) {
      const data = status.data;
      fillSession(data);
      blocked(data.stopped ? "stop" : "");
      await refreshTabs();
      fillAllowlist(data.allowlist || {});
    } else if (status.status === 503) {
      $("session-summary").textContent = "the browser is not available here";
      toast(explain(status), "bad");
    } else {
      blocked("stop");
    }
    const held = await get("/api/browser/downloads");
    if (held.status === 200) fillDownloads(held.data);
  }

  async function refreshTabs() {
    const query = state.sessionId ? "?session_id=" + encodeURIComponent(state.sessionId) : "";
    const result = await get("/api/browser/tabs" + query);
    if (result.status === 200) fillTabs(result.data);
  }

  async function pollStop() {
    const result = await get("/api/kill-switch");
    /* Fail closed. An unreadable switch is reported as engaged rather than as
       running, because a false "running" is the direction that hurts: the
       server enforces the real stop either way, so the cost of guessing wrong
       here is a tab that clears on the next poll. */
    blocked(result.status === 200 && result.data.engaged ? "stop" : result.status === 200 ? "" : "stop");
  }

  async function forgetDomain(domain) {
    const result = await post("/api/browser/forget-domain", { domain: domain });
    if (result.status === 200) {
      toast("forgot " + domain + (result.data.result && result.data.result.state_deleted
        ? ", and deleted the state stored for it" : ""), "ok");
      refreshAll();
    } else {
      toast(explain(result), "bad");
    }
  }

  // -- forms ----------------------------------------------------------

  /* "handle = value", one per line. Split on the first "=" only, because a
     value is far more likely to contain one than a handle is — splitting on
     the last one loses a base64 padding character and mangles a password. */
  function parseForm(text) {
    const out = {};
    text.split("\n").forEach(function (line) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) return;
      const cut = trimmed.indexOf("=");
      if (cut < 1) return;
      out[trimmed.slice(0, cut).trim()] = trimmed.slice(cut + 1).trim();
    });
    return out;
  }

  // -- wiring ---------------------------------------------------------

  function on(id, handler) {
    const node = $(id);
    if (node) node.addEventListener("click", handler);
  }

  function number(id) {
    const raw = $(id).value.trim();
    if (raw === "") return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  }

  function session() {
    return state.sessionId;
  }

  function wire() {
    on("op-open", function () {
      run("/api/browser/open", { headless: $("headless").checked });
    });

    on("op-close", function () {
      run("/api/browser/close", { session_id: session() });
    });

    on("status-refresh", refreshAll);
    on("tabs-refresh", refreshTabs);
    on("allow-refresh", refreshAll);

    on("op-go", function () {
      const url = $("url").value.trim();
      if (!url) return toast("put a URL in the box first", "bad");
      warn("");
      /* A URL typed by hand is an explicit user command, so the gate is told
         so. It still asks — that is what explicit_command_always_asks means —
         but the prompt can then say "you asked for this" rather than guessing. */
      run("/api/browser/navigate", {
        session_id: session(),
        url: url,
        explicit_user_command: true,
      });
    });

    on("op-newtab", function () {
      run("/api/browser/new-tab", { session_id: session(), url: $("url").value.trim() });
    });

    $("url").addEventListener("keydown", function (event) {
      if (event.key === "Enter") $("op-go").click();
    });

    on("op-search", function () {
      const query = $("query").value.trim();
      if (!query) return toast("put something in the search box first", "bad");
      warn("");
      run("/api/browser/search", {
        session_id: session(),
        query: query,
        engine: $("engine").value,
      });
    });

    $("query").addEventListener("keydown", function (event) {
      if (event.key === "Enter") $("op-search").click();
    });

    /* Reading the page. Every mode is the same gated act with a different
       shape of answer, so one function with the mode as an argument. */
    const read = function (mode, label) {
      on("op-" + mode, function () {
        warn("");
        run("/api/browser/extract", { session_id: session(), mode: mode }).then(
          function (result) {
            if (result.status === 200) show(label, result.data.result);
          }
        );
      });
    };
    read("text", "Page text");
    read("links", "Links");
    read("title", "Title and URL");
    read("fields", "Form fields");
    read("table", "Tables");
    read("meta", "Metadata");
    read("html", "HTML");

    on("op-shot", function () {
      capture({ session_id: session(), full_page: false });
    });
    on("op-shot-full", function () {
      capture({ session_id: session(), full_page: true });
    });

    on("op-click", function () {
      const selector = $("click-text").value.trim();
      if (!selector) return toast("give a button's text or a CSS selector", "bad");
      warn("");
      run("/api/browser/click", {
        session_id: session(),
        selector: selector,
        description: "click " + selector,
      }).then(function (result) {
        if (result.status === 200) refreshTabs();
      });
    });

    on("op-clickxy", function () {
      const x = number("px");
      const y = number("py");
      if (x === null || y === null) return toast("give both x and y", "bad");
      const confidence = number("pconf");
      warn("");
      run("/api/browser/click", {
        session_id: session(),
        x: x,
        y: y,
        confidence: confidence,
        description: "click at (" + x + ", " + y + ")",
      }).then(function (result) {
        if (result.status === 200) refreshTabs();
      });
    });

    const fill = function (submit) {
      const formData = parseForm($("formdata").value);
      if (!Object.keys(formData).length) {
        return toast("write at least one 'handle = value' line", "bad");
      }
      warn("");
      run("/api/browser/fill-form", {
        session_id: session(),
        form_data: formData,
        submit: submit,
      }).then(function (result) {
        if (result.status === 200) {
          toast(submit ? "filled and submitted" : "filled — nothing was submitted",
               submit ? "warn" : "ok");
        }
      });
    };

    on("op-fill", function () {
      fill(false);
    });
    on("op-submit", function () {
      fill(true);
    });

    on("op-switch", function () {
      if (!state.selected) return toast("pick a tab first", "bad");
      run("/api/browser/switch-tab", { session_id: session(), tab_id: state.selected });
    });

    on("op-closetab", function () {
      if (!state.selected) return toast("pick a tab first", "bad");
      run("/api/browser/close-tab", { session_id: session(), tab_id: state.selected });
    });

    on("op-download", function () {
      const url = $("dl-url").value.trim();
      if (!url) return toast("put a file URL in the box first", "bad");
      warn("");
      run("/api/browser/download", { session_id: session(), url: url });
    });

    on("out-copy", async function () {
      try {
        await navigator.clipboard.writeText($("out-body").textContent || "");
        toast("copied", "ok");
      } catch (err) {
        toast("the browser would not let the page copy that", "bad");
      }
    });

    on("out-close", function () {
      $("out").classList.add("hidden");
    });

    on("modal-cancel", closeModal);

    on("modal-confirm", async function () {
      const pending = state.pending;
      if (!pending) return;
      const domain = $("modal-domain").checked && pending.domain;
      const result = await post(pending.path, {
        ...pending.body,
        confirmed: true,
        confirmation_id: pending.data.confirmation_id,
        dry_run: $("modal-dry-run").checked,
        allow_domain: domain,
        remember_domain: true,
        persist_domain: $("modal-persist").checked,
      });
      if (result.status === 200) {
        closeModal();
        toast($("modal-dry-run").checked ? "dry run — nothing was sent" : "done", "ok");
        if (pending.path.indexOf("/extract") > -1) show("Extraction", result.data.result);
        await refreshAll();
      } else if (result.status === 202) {
        /* The write decision of a download asks again on its own. Answering
           it here rather than silently retrying is deliberate: the second
           question is about a path on disk, and it deserves its own look. */
        openModal({
          path: pending.path,
          body: pending.body,
          operation: pending.operation,
        target: result.data.target || "",
          domain: result.data.domain || "",
          data: result.data,
        });
      } else {
        toast(explain(result), "bad");
        closeModal();
      }
    });

    on("stop-btn", async function () {
      const result = await post("/api/kill-switch/stop", {
        reason: "pressed in the Browser tab",
        source: "ui",
      });
      if (result.status === 200) {
        blocked("stop");
        toast("stopped — the browser is closed and nothing can be sent to a page", "warn");
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
    refreshAll();
    setInterval(pollStop, 5000);
  });

  global.ZuzzBrowser = { state: state, refresh: refreshAll };
})(window);
