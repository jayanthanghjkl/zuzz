/* Memory page — what ZUZZ remembers, and how it speaks.

   Everything on this page is read or write through the API. The browser
   never touches storage/memory or config/personality.json directly.

   Forgetting is the part with a real flow. The date box is required and
   has no default, because the server refuses a forget without one and a
   client-side default of "today" would be a lie about intent. The first
   click is always a dry run: the server reports what matches, this page
   shows it, and only then does the second button arm the real delete.
   The real delete is only ever enabled from a dry run that matched
   something — so there is no path from this page to an empty store that
   did not first show the user the count. */

(function (global) {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const state = {
    threshold: 0.5,
    forgetArmed: false,
    lastDryRun: null,
    voice: null,
  };

  // -- API ------------------------------------------------------------

  async function send(method, path, body) {
    let response;
    try {
      response = await fetch(path, {
        method: method,
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (err) {
      return { status: 0, data: { detail: "could not reach ZUZZ — is it running?" } };
    }
    let data = {};
    try {
      data = await response.json();
    } catch (err) {
      data = {};
    }
    return { status: response.status, data: data };
  }

  const get = (path) => send("GET", path);
  const post = (path, body) => send("POST", path, body || {});
  const del = (path, body) => send("DELETE", path, body);

  function explain(result) {
    const detail = result.data && result.data.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length) {
      /* FastAPI's validation errors are a list of {loc, msg}. Showing the raw
         array would put JSON in front of the user, so it is flattened here. */
      return detail
        .map(function (item) {
          return (item.loc || []).slice(1).join(".") + ": " + item.msg;
        })
        .join("; ");
    }
    if (result.status === 503) return "the memory store could not be read";
    return "request failed (" + result.status + ")";
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
    }, 6000);
  }

  // -- the two tiers --------------------------------------------------

  function fillTier(node, label, value, note, byKind) {
    node.querySelector(".tier-label").textContent = label;
    node.querySelector(".tier-value").textContent = String(value);
    node.querySelector(".tier-note").textContent = note;
    const chips = node.querySelector(".kind-row");
    chips.innerHTML = Object.keys(byKind || {})
      .filter(function (kind) {
        return byKind[kind] > 0;
      })
      .map(function (kind) {
        return (
          '<span class="kind-chip">' +
          escapeHtml(kind) +
          "<b>" +
          byKind[kind] +
          "</b></span>"
        );
      })
      .join("");
  }

  function fillProfile(payload) {
    const profile = payload.profile || {};
    const shortTerm = payload.short_term || {};
    const longTerm = payload.long_term || {};
    const held = shortTerm.held || 0;
    const kept = Object.keys(longTerm).reduce(function (sum, kind) {
      return sum + (longTerm[kind] || 0);
    }, 0);

    fillTier(
      $("tier-ram"),
      "in memory only",
      held,
      "can still be lost — " + (shortTerm.capacity || 0) + " item capacity, mean importance " +
        Number(shortTerm.mean_importance || 0).toFixed(2),
      shortTerm.by_kind
    );
    fillTier(
      $("tier-disk"),
      "written to disk",
      kept,
      payload.directory || "",
      longTerm
    );

    $("profile-total").textContent = String(profile.total || 0);
    $("profile-oldest").textContent = profile.oldest
      ? new Date(profile.oldest * 1000).toLocaleDateString()
      : "nothing yet";
    $("profile-topics").innerHTML = (profile.topics || [])
      .map(function (topic) {
        return '<span class="kind-chip">' + escapeHtml(topic) + "</span>";
      })
      .join("");
    $("profile-threshold").textContent = Number(payload.threshold || 0).toFixed(2);
  }

  // -- memory lists ---------------------------------------------------

  function memoryRow(entry, score) {
    const meta = [];
    meta.push(escapeHtml(entry.kind));
    if (entry.key) meta.push(escapeHtml(entry.key + " = " + (entry.value || "")));
    if (entry.age_days !== undefined) {
      meta.push(
        entry.age_days < 1
          ? "today"
          : Math.round(entry.age_days) + (Math.round(entry.age_days) === 1 ? " day" : " days") +
              " old"
      );
    }
    if (score) {
      meta.push("importance " + Number(entry.importance || 0).toFixed(2));
    }

    let bar = "";
    if (score && typeof score.score === "number") {
      /* The tick is the promotion threshold and the fill is the score. They are
         drawn in the same box so the comparison needs no words. */
      const pct = Math.max(0, Math.min(1, score.score)) * 100;
      const tick = Math.max(0, Math.min(1, score.threshold)) * 100;
      bar =
        '<div class="score-bar">' +
        '<div class="score-fill' +
        (score.eligible ? " eligible" : "") +
        '" style="width:' +
        pct.toFixed(1) +
        '%"></div>' +
        '<div class="score-tick" style="left:' +
        tick.toFixed(1) +
        '%"></div>' +
        "</div>" +
        '<div class="mem-why">' +
        (score.eligible
          ? "scores " + score.score.toFixed(2) + ", above the " + score.threshold.toFixed(2) +
            " threshold — consolidate to keep it"
          : "scores " + score.score.toFixed(2) + ", below the " + score.threshold.toFixed(2) +
            " threshold — not eligible for the disk yet") +
        "</div>";
    }

    return (
      '<div class="mem-item' +
      (score && !score.eligible ? " pending" : "") +
      '">' +
      '<div class="mem-top">' +
      '<div class="mem-summary"><span class="mem-kind">' +
      escapeHtml(entry.kind) +
      "</span>" +
      escapeHtml(entry.summary || entry.text || "(no summary)") +
      "</div>" +
      '<div class="mem-meta">' +
      escapeHtml(meta.join(" · ")) +
      "</div>" +
      "</div>" +
      bar +
      "</div>"
    );
  }

  function fillList(id, entries, scores) {
    const node = $(id);
    if (!entries.length) {
      node.innerHTML = '<p class="prose">Nothing here yet.</p>';
      return;
    }
    const byId = {};
    (scores || []).forEach(function (score) {
      byId[score.id] = score;
    });
    node.innerHTML = entries
      .map(function (entry) {
        return memoryRow(entry, byId[entry.id]);
      })
      .join("");
  }

  // -- forget ---------------------------------------------------------

  function resetForget() {
    state.forgetArmed = false;
    state.lastDryRun = null;
    $("forget-commit").disabled = true;
    $("forget-commit").textContent = "Forget for good";
    $("forget-outcome").className = "forget-outcome";
    $("forget-outcome").innerHTML = "";
  }

  function showForget(className, html) {
    const node = $("forget-outcome");
    node.className = "forget-outcome " + className;
    node.innerHTML = html;
  }

  async function previewForget() {
    const before = $("forget-before").value.trim();
    if (!before) {
      /* Checked here as well as on the server so the user is not made to wait
         for a round trip to be told what they can see from an empty box. */
      showForget("dry", "Type a date first. There is no default, on purpose.");
      return;
    }
    resetForget();
    const result = await del("/api/memory/forget", { before: before, dry_run: true });
    if (result.status !== 200) {
      showForget("dry", escapeHtml(explain(result)));
      return;
    }
    const count = result.data.would_forget || 0;
    state.lastDryRun = result.data;
    showForget(
      "dry",
      "<strong>" +
        count +
        (count === 1 ? " memory" : " memories") +
        "</strong> are older than <code>" +
        escapeHtml(result.data.before) +
        "</code>. Nothing has been removed yet."
    );
    if (count > 0) {
      state.forgetArmed = true;
      $("forget-commit").disabled = false;
    }
  }

  async function commitForget() {
    if (!state.forgetArmed || !state.lastDryRun) return;
    const result = await del("/api/memory/forget", {
      before: state.lastDryRun.before,
      dry_run: false,
    });
    if (result.status !== 200) {
      showForget("dry", escapeHtml(explain(result)));
      resetForget();
      return;
    }
    showForget(
      "done",
      "Removed <strong>" +
        (result.data.removed || 0) +
        "</strong> memories older than <code>" +
        escapeHtml(result.data.before) +
        "</code>. This cannot be undone."
    );
    state.forgetArmed = false;
    $("forget-commit").disabled = true;
    $("forget-commit").textContent = "Forget for good";
    await refresh();
  }

  // -- consolidation --------------------------------------------------

  async function consolidate(dryRun) {
    const result = await post("/api/memory/consolidate?dry_run=" + (dryRun ? "true" : "false"), {});
    if (result.status !== 200) {
      toast(explain(result), "bad");
      return;
    }
    const report = result.data;
    toast(
      dryRun
        ? report.promoted + " would be promoted, " + report.skipped + " skipped"
        : report.promoted + " promoted to disk, " + report.skipped + " skipped",
      "ok"
    );
    $("consolidate-detail").textContent =
      "threshold " +
      Number(report.threshold || state.threshold).toFixed(2) +
      " · " +
      (report.dry_run ? "dry run, nothing written" : "written to disk");
    await refresh();
  }

  // -- voice ----------------------------------------------------------

  function fillVoice(data) {
    state.voice = data;
    const values = data.allowed_values || {};
    $("voice-style").innerHTML = options(values.style || []);
    $("voice-humor").innerHTML = options(values.humor_level || []);
    $("voice-verbosity").innerHTML = options(values.verbosity || []);

    $("voice-style").value = data.style || "";
    $("voice-humor").value = data.humor_level || "";
    $("voice-verbosity").value = data.verbosity || "";
    $("voice-greeting").value = data.greeting || "";
    $("voice-signoff").value = data.signoff || "";

    $("voice-tone").textContent = (data.tone || []).join(", ") || "—";
    $("voice-max").textContent = String(data.max_sentences_per_reply || "—");
    $("voice-quirks").textContent = String((data.quirks || []).length);
    $("voice-path").textContent = data.path || "";
    $("greeting-preview").innerHTML =
      '<span class="who">as it will greet you</span>' +
      escapeHtml(data.greeting || "(no greeting configured)");
    $("signoff-preview").innerHTML =
      '<span class="who">as it will sign off</span>' +
      escapeHtml(data.signoff || "(no sign-off configured)");

    /* The three presets are the only keys with a closed vocabulary. Showing the
       list the server returned, rather than a hardcoded copy in the page, means
       a value added to the config next year appears here with no edit to this
       file — and a value removed cannot be selected by a stale page. */
    const editable = (data.editable_keys || []).join(", ");
    $("voice-editable").textContent = editable;
  }

  function options(list) {
    return list
      .map(function (value) {
        return '<option value="' + escapeHtml(value) + '">' + escapeHtml(value) + "</option>";
      })
      .join("");
  }

  async function saveVoice() {
    const changes = {};
    const style = $("voice-style").value;
    const humor = $("voice-humor").value;
    const verbosity = $("voice-verbosity").value;
    /* Only send a field that actually differs. Sending the whole configuration
       back on every save would write name, owner and wake_word too — which the
       server refuses anyway, but the request should not be asking. */
    if (style && style !== state.voice.style) changes.style = style;
    if (humor && humor !== state.voice.humor_level) changes.humor_level = humor;
    if (verbosity && verbosity !== state.voice.verbosity) changes.verbosity = verbosity;

    if (!Object.keys(changes).length) {
      toast("nothing changed", "warn");
      return;
    }
    const result = await post("/api/personality/settings", { settings: changes });
    if (result.status !== 200) {
      toast(explain(result), "bad");
      return;
    }
    toast("changed " + result.data.applied.join(", ") + " — takes effect on the next reply", "ok");
    await loadVoice();
  }

  async function detectMood() {
    const message = $("mood-message").value.trim();
    if (!message) {
      toast("say something first", "warn");
      return;
    }
    const result = await post("/api/personality/detect-mood", { message: message });
    if (result.status !== 200) {
      toast(explain(result), "bad");
      return;
    }
    const data = result.data;
    $("mood-badge").textContent = data.mood;
    $("mood-badge").className = "mood-badge " + data.mood;
    $("mood-instruction").textContent = data.instruction + "  " + data.formality_instruction;
    $("mood-reply").innerHTML =
      '<span class="who">reply shaped this way</span>' +
      escapeHtml(data.reply || "(nothing to shape)") +
      (data.changed ? " <em>(the plain reply would have been longer)</em>" : "");
  }

  // -- refresh --------------------------------------------------------

  async function loadVoice() {
    const result = await get("/api/personality/settings");
    if (result.status === 200) fillVoice(result.data);
  }

  async function refresh() {
    const profile = await get("/api/memory/profile");
    if (profile.status === 200) {
      state.threshold = profile.data.threshold || 0.5;
      fillProfile(profile.data);
    }

    const candidates = await get("/api/memory/candidates");
    const scores = candidates.status === 200 ? candidates.data.entries : [];
    if (candidates.status === 200) {
      const eligible = scores.filter(function (score) {
        return score.eligible;
      }).length;
      $("candidate-count").textContent =
        scores.length + " held · " + eligible + " above the threshold";
    }

    const history = await get("/api/memory/history?limit=60");
    if (history.status === 200) {
      fillList("history-list", history.data.memories, scores);
    }
  }

  async function doRecall() {
    const query = $("recall-query").value.trim();
    if (!query) {
      toast("ask something first", "warn");
      return;
    }
    const result = await post("/api/memory/recall", {
      query: query,
      memory_type: $("recall-kind").value,
      limit: 20,
    });
    if (result.status !== 200) {
      toast(explain(result), "bad");
      return;
    }
    $("recall-count").textContent =
      result.data.count + (result.data.count === 1 ? " match" : " matches");
    fillList("recall-list", result.data.memories, null);
  }

  async function doRemember() {
    const kind = $("remember-kind").value;
    const text = $("remember-text").value.trim();
    if (!text) {
      toast("there is nothing to remember yet", "warn");
      return;
    }
    const body = {
      kind: kind,
      text: text,
      summary: $("remember-summary").value.trim(),
      key: $("remember-key").value.trim(),
      value: $("remember-value").value.trim(),
      context: "memory page",
      importance: Number($("remember-importance").value),
    };
    const result = await post("/api/memory/remember", body);
    if (result.status !== 200) {
      toast(explain(result), "bad");
      return;
    }
    toast("remembered, importance " + result.data.importance.toFixed(2), "ok");
    $("remember-text").value = "";
    $("remember-summary").value = "";
    await refresh();
  }

  // -- wiring ---------------------------------------------------------

  function on(id, handler) {
    const node = $(id);
    if (node) node.addEventListener("click", handler);
  }

  function wire() {
    on("recall-go", doRecall);
    on("remember-go", doRemember);
    on("history-refresh", refresh);
    on("candidates-refresh", refresh);
    on("consolidate-dry", function () {
      consolidate(true);
    });
    on("consolidate-go", function () {
      consolidate(false);
    });
    on("forget-preview", previewForget);
    on("forget-commit", commitForget);
    /* Any edit to the date invalidates the armed state. Otherwise the second
       click would forget everything older than a *different* date than the one
       that was previewed — the count on screen would no longer be the count. */
    $("forget-before").addEventListener("input", resetForget);
    on("voice-save", saveVoice);
    on("mood-detect", detectMood);
    $("recall-query").addEventListener("keydown", function (event) {
      if (event.key === "Enter") doRecall();
    });
    $("mood-message").addEventListener("keydown", function (event) {
      if (event.key === "Enter") detectMood();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    wire();
    resetForget();
    refresh();
    loadVoice();
  });

  global.ZuzzMemory = { state: state, refresh: refresh };
})(window);