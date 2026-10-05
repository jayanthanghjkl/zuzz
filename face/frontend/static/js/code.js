/* Code page — the five agents, their proposals, and the terminal.

   Everything here goes through /api/code. The browser never reads a workspace
   directly and never writes one, and it has no editor on purpose: the primary
   content of this page is a diff, and the only thing the user can do with a diff
   is read it and decide. An editable buffer here would be an editable buffer with
   no gate on it — a second write path that does not go through Body/Fingers, which
   is the one thing this integration exists not to have.

   The proposal flow is three steps and the page keeps them visibly separate:

     propose  ->  a diff appears, nothing on disk has changed
     approve  ->  a 202 popup, naming the folder and both decisions
     apply    ->  bytes move through Body/Fingers, and only then

   Apply and Rollback are the only two buttons here that change the workspace, and
   they are the only two that go through the popup. Every other button either asks
   a model to write text or reads a gauge. */

(function (global) {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const state = {
    /* The 202 we are answering, if any. Held so the Approve button can re-post
       the original body with the confirmation id rather than rebuilding it — a
       rebuilt body could name a different workspace than the one that was
       approved, and the server would (correctly) refuse it. */
    pending: null,
    status: null,
    workspace: "",
  };

  // -- API -------------------------------------------------------------

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

  function explain(result) {
    const detail = result.data && result.data.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      /* A refusal arrives as {error, reason} for some categories and as a plain
         string for others. Both are shown as text, never as JSON. */
      return detail.reason || detail.error || JSON.stringify(detail);
    }
    if (Array.isArray(detail) && detail.length) {
      return detail
        .map(function (item) {
          return (item.loc || []).slice(1).join(".") + ": " + item.msg;
        })
        .join("; ");
    }
    if (result.status === 503) return "the coding agents are not attached to this app";
    return "request failed (" + result.status + ")";
  }

  function escapeHtml(value) {
    return String(value === undefined || value === null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function bytes(value) {
    const n = Number(value || 0);
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }

  function when(stamp) {
    if (!stamp) return "";
    try {
      return new Date(stamp * 1000).toLocaleString();
    } catch (err) {
      return String(stamp);
    }
  }

  let toastTimer = null;

  function toast(message, kind) {
    const node = $("toast");
    node.textContent = message;
    node.className = "toast" + (kind ? " " + kind : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      node.className = "toast hidden";
    }, 7000);
  }

  /* -- the 202 --------------------------------------------------------

     One popup answers both of an apply's decisions. It names the folder and says
     plainly that files are involved, because a dialog showing only a diff id
     would be a dialog that has not told the user what they are agreeing to. */

  function openConfirm(data, path, body, operation) {
    state.pending = { data: data, path: path, body: body, operation: operation };
    $("confirm-target").textContent = data.target || data.diff_id || "";
    const facts = [];
    if (data.file_count !== undefined && data.file_count !== null) {
      facts.push(data.file_count + " file(s)");
      facts.push(bytes(data.total_size_bytes));
    }
    facts.push("threat score " + (data.threat && data.threat.score !== undefined ? data.threat.score : "?"));
    facts.push("needs trust level " + (data.required_trust_level || "?"));
    if (data.reversible !== undefined) facts.push(data.reversible ? "reversible" : "not reversible");
    $("confirm-facts").innerHTML = facts
      .map(function (fact) {
        return "<li>" + escapeHtml(fact) + "</li>";
      })
      .join("");

    const write = !!data.write_action;
    $("confirm-write").className = "modal-write" + (write ? "" : " hidden");
    const asked = $("confirm-workspace");
    asked.className = "modal-workspace" + (data.needs_workspace_confirmation ? "" : " hidden");
    if (data.needs_workspace_confirmation) {
      asked.textContent =
        "You have not told ZUZZ about this folder before: " +
        (data.workspace_confirmation || data.workspace || "") +
        ". Approving registers it as a workspace.";
    }
    $("confirm-dry").checked = false;
    $("confirm-modal").classList.remove("hidden");
  }

  function closeConfirm() {
    state.pending = null;
    $("confirm-modal").classList.add("hidden");
  }

  /* Run a gated call, handling the 202 itself so no caller has to.

     Returns the payload on success and null when the user declined or the call
     failed, so a caller can chain a refresh off "did that work" without
     re-inspecting the response. */
  async function call(operation, path, body, onDone) {
    body = body || {};
    let result = await post(path, body);
    if (result.status === 202) {
      openConfirm(result.data, path, body, operation);
      return null;
    }
    if (result.status !== 200) {
      toast(explain(result), "bad");
      return null;
    }
    if (onDone) onDone(result.data);
    return result.data;
  }

  /* -- rendering ------------------------------------------------------ */

  function renderAgents(status) {
    const catalogue = status.agents || {};
    const list = catalogue.agents || [];
    const writers = catalogue.writers || [];
    $("agent-list").innerHTML = list
      .map(function (agent) {
        const role = agent.can_write ? "proposes files" : "reports only";
        return (
          '<div class="agent-row' +
          (agent.can_write ? " writer" : "") +
          '"><span class="agent-name">' +
          escapeHtml(agent.name) +
          '</span><span class="agent-role" title="' +
          escapeHtml(agent.role) +
          '">' +
          escapeHtml(role) +
          '</span><span class="agent-model">' +
          escapeHtml((agent.model || "").split("/").pop()) +
          (agent.cloud_available ? "" : " (unconfigured)") +
          "</span></div>"
        );
      })
      .join("");

    /* Routing is reported per agent rather than once, because each agent can name a
       different model and a different fallback. The one row worth surfacing is
       whether the cloud is configured at all, since that decides whether a rate
       limit becomes a slower answer or a failed one. */
    const unavailable = list.filter(function (agent) {
      return !agent.cloud_available;
    }).length;
    $("routing").innerHTML =
      chip("cloud", unavailable ? unavailable + " agent(s) unconfigured" : "groq reachable") +
      chip("local fallback", (list[0] && list[0].fallback) || "none") +
      chip("writers", writers.join(", ") || "none");

    const space = status.workspace || {};
    const git = status.git || {};
    $("policy").innerHTML = [
      chip("scratch root", space.scratch_root || "—"),
      chip("pending proposals", status.pending_diffs || 0),
      chip("commands allowed", (status.terminal || {}).allowed ? status.terminal.allowed.join(" ") : "—"),
      chip("always refused", (git.refused || []).join(" ") || "none"),
      chip("never writable", (space.core_directories || []).join(" ")),
      status.stopped ? chip("stopped", "yes") : "",
    ].join("");
  }

  function chip(label, value) {
    return '<span class="kind-chip">' + escapeHtml(label) + "<b>" + escapeHtml(value) + "</b></span>";
  }

  function renderTree(record) {
    const tree = record.tree || {};
    const space = record.workspace || tree.workspace || {};
    $("ws-head").textContent = space.root || "";
    const entries = tree.entries || [];
    $("ws-tree").innerHTML = entries.length
      ? entries
          .map(function (entry) {
            return '<div class="tree-row"><span>' + escapeHtml(entry) + "</span></div>";
          })
          .join("")
      : '<span class="muted">nothing here yet</span>';
    if (tree.skipped) {
      $("ws-tree").innerHTML +=
        '<div class="tree-row muted"><span>' +
        escapeHtml(tree.skipped) +
        " entries not shown — the cap is there so a large project cannot fill the page</span></div>";
    }

    const git = record.git || {};
    if (git.error) {
      $("ws-git").innerHTML = '<span class="muted">no repository here — ' + escapeHtml(git.error) + "</span>";
    } else if (git.is_repository) {
      $("ws-git").innerHTML =
        chip("repository", git.repository || "") +
        chip("branch", git.branch || "?") +
        chip(git.clean ? "clean" : "changed", git.changed) +
        (git.entries || [])
          .slice(0, 20)
          .map(function (entry) {
            return '<div class="tree-row"><span class="muted">' + escapeHtml(entry.code) + "</span><span>" + escapeHtml(entry.path) + "</span></div>";
          })
          .join("");
    } else {
      $("ws-git").innerHTML = '<span class="muted">not a git repository</span>';
    }
  }

  function renderDiff(change) {
    const rows = change.side_by_side || [];
    const body = rows.length
      ? rows
          .map(function (row) {
            const kind = row[0] === "+" ? "add" : "del";
            return '<div class="diff-row ' + kind + '"><span class="diff-sign">' + escapeHtml(row[0]) + "</span><span class=\"diff-text\">" + escapeHtml(row[1]) + "</span></div>";
          })
          .join("")
      : '<pre class="diff-pre">' + escapeHtml(change.unified || "") + "</pre>";
    return (
      '<div class="diff-file"><div class="diff-head"><span class="diff-path">' +
      escapeHtml(change.path) +
      '</span><span class="diff-stat"><b class="add">+' +
      change.additions +
      '</b><b class="del">−' +
      change.deletions +
      '</b><span class="muted">' +
      escapeHtml(change.status) +
      "</span></span></div>" +
      '<div class="diff-body">' +
      body +
      "</div></div>"
    );
  }

  function renderDiffs(payload) {
    const diffs = (payload && payload.diffs) || (payload && payload.diff_id ? [payload] : []);
    $("diff-count").textContent = diffs.length + " pending";
    if (!diffs.length) {
      $("diff-list").innerHTML =
        '<span class="muted">nothing proposed yet. Ask for something on the left.</span>';
      return;
    }
    $("diff-list").innerHTML = diffs
      .map(function (diff) {
        const state_ = diff.pending ? "pending" : diff.applied ? "applied" : "rolled back";
        return (
          '<div class="diff-card"><div class="diff-card-head">' +
          '<span class="diff-id">' +
          escapeHtml(diff.diff_id) +
          "</span>" +
          '<span class="muted">' +
          escapeHtml(diff.agent || "") +
          " · " +
          escapeHtml(diff.files) +
          " file(s) · " +
          escapeHtml(state_) +
          " · " +
          escapeHtml(when(diff.created)) +
          "</span>" +
          '<span class="diff-card-actions">' +
          (diff.pending
            ? '<button class="send-btn" data-apply="' +
              escapeHtml(diff.diff_id) +
              '" type="button">Apply</button>'
            : "") +
          (diff.pending
            ? '<button class="ghost-btn" data-reject="' +
              escapeHtml(diff.diff_id) +
              '" type="button">Reject</button>'
            : "") +
          (diff.applied
            ? '<button class="ghost-btn" data-rollback="' +
              escapeHtml(diff.diff_id) +
              '" type="button">Roll back</button>'
            : "") +
          "</span></div>" +
          (diff.changes || []).map(renderDiff).join("") +
          "</div>"
        );
      })
      .join("");
  }

  function renderFindings(node, payload) {
    const findings = payload.findings || [];
    if (!findings.length) {
      node.innerHTML =
        '<span class="muted">' +
        escapeHtml(payload.verdict || "nothing flagged") +
        ". The static checks found no bug they can name, which is not the same as " +
        "saying the file is correct.</span>";
      return;
    }
    node.innerHTML = findings
      .map(function (finding) {
        return (
          '<div class="finding ' +
          escapeHtml(finding.severity || "info") +
          '"><span class="finding-loc">' +
          escapeHtml(finding.where || "") +
          '</span><span class="finding-msg">' +
          escapeHtml(finding.message || "") +
          "</span></div>"
        );
      })
      .join("");
  }

  function renderTerm(node, payload) {
    const out = payload.stdout || "";
    const err = payload.stderr || "";
    const code = payload.returncode;
    const parsed = payload.parsed || {};
    node.innerHTML =
      (out ? '<pre class="term-stdout">' + escapeHtml(out) + "</pre>" : "") +
      (err ? '<pre class="term-stderr">' + escapeHtml(err) + "</pre>" : "") +
      '<span class="muted">' +
      escapeHtml((payload.command || []).join(" ") || "") +
      " · exit " +
      escapeHtml(code === undefined ? "?" : code) +
      (parsed.passed !== undefined
        ? " · " + escapeHtml(parsed.passed) + " passed, " + escapeHtml(parsed.failed || 0) + " failed"
        : "") +
      "</span>";
  }

  /* -- gauges --------------------------------------------------------- */

  async function refreshStatus() {
    const result = await get("/api/code/agents-status");
    if (result.status !== 200) {
      toast(explain(result), "bad");
      return;
    }
    state.status = result.data.result || {};
    renderAgents(state.status);

    const agentSelect = $("gen-agent");
    const all = (status.agents || {}).agents || [];
    if (agentSelect.options.length !== all.length) {
      agentSelect.innerHTML = all
        .map(function (agent) {
          return (
            '<option value="' +
            escapeHtml(agent.name) +
            '"' +
            (agent.can_write ? "" : " disabled") +
            ">" +
            escapeHtml(agent.name) +
            (agent.can_write ? "" : " (reports only)") +
            "</option>"
          );
        })
        .join("");
      const firstWriter = all.find(function (agent) {
        return agent.can_write;
      });
      if (firstWriter) agentSelect.value = firstWriter.name;
    }

    $("workspace-list").innerHTML = (status.workspaces || [])
      .map(function (space) {
        return '<option value="' + escapeHtml(space.name || space.root) + '"></option>';
      })
      .join("");
  }

  async function refreshWorkspace() {
    const path = $("ws-path").value.trim();
    const result = await get("/api/code/workspace?path=" + encodeURIComponent(path));
    if (result.status !== 200) {
      $("ws-head").textContent = "";
      $("ws-tree").innerHTML = '<span class="muted bad">' + escapeHtml(explain(result)) + "</span>";
      $("ws-git").innerHTML = "";
      return;
    }
    renderTree(result.data);
    state.workspace = path;
  }

  async function refreshDiffs() {
    const result = await post("/api/code/diff", {});
    if (result.status === 200) renderDiffs(result.data.result);
  }

  /* After anything that changes a proposal, every gauge is stale. Refreshed
     together rather than one at a time so the page cannot show a file tree from
     before an apply and a proposal list from after it. */
  async function refreshAll() {
    await Promise.all([refreshStatus(), refreshWorkspace(), refreshDiffs()]);
  }

  // -- wiring ----------------------------------------------------------

  function on(id, handler) {
    const node = $(id);
    if (node) node.addEventListener("click", handler);
  }

  function wire() {
    on("status-refresh", refreshStatus);
    on("tree-refresh", refreshWorkspace);
    on("ws-path", function () {
      state.workspace = $("ws-path").value.trim();
    });

    on("gen-go", async function () {
      const description = $("gen-description").value.trim();
      if (!description) {
        toast("say what to build", "warn");
        return;
      }
      const button = $("gen-go");
      button.disabled = true;
      $("gen-cancel").classList.remove("hidden");
      $("gen-design").classList.add("hidden");
      try {
        const payload = await call(
          "generate",
          "/api/code/generate",
          {
            description: description,
            workspace: $("gen-workspace").value.trim(),
            agent: $("gen-agent").value || "coder",
            plan_only: $("gen-plan-only").checked,
          },
          function (data) {
            const result = data.result || {};
            const design = $("gen-design");
            if (result.design) {
              design.textContent = result.design;
              design.classList.remove("hidden");
            }
            toast(
              result.diff_id
                ? "proposed — " + (result.files || 0) + " file(s). Nothing written yet."
                : "described, but produced no files to show you",
              "ok"
            );
          }
        );
        if (payload) await refreshDiffs();
      } finally {
        button.disabled = false;
        $("gen-cancel").classList.add("hidden");
      }
    });

    on("gen-cancel", function () {
      toast("the model call cannot be cancelled once it is running — it is one request", "warn");
    });

    on("do-review", async function () {
      const path = $("file-path").value.trim();
      if (!path) {
        toast("name the file", "warn");
        return;
      }
      const payload = await call(
        "review",
        "/api/code/review",
        { path: path, workspace: $("gen-workspace").value.trim() },
        function (data) {
          renderFindings($("file-out"), data.result || {});
        }
      );
      if (!payload) $("file-out").innerHTML = "";
    });

    on("do-refactor", async function () {
      const path = $("file-path").value.trim();
      const goal = $("file-goal").value.trim();
      if (!path || !goal) {
        toast("name the file and what better means for it", "warn");
        return;
      }
      const payload = await call(
        "refactor",
        "/api/code/refactor",
        { path: path, goal: goal, workspace: $("gen-workspace").value.trim() },
        function (data) {
          toast("proposed — nothing written yet", "ok");
        }
      );
      if (payload) await refreshDiffs();
    });

    on("do-tests", async function () {
      const path = $("file-path").value.trim();
      if (!path) {
        toast("name the file to write tests for", "warn");
        return;
      }
      const payload = await call(
        "test",
        "/api/code/test",
        {
          path: path,
          goal: $("file-goal").value.trim(),
          workspace: $("gen-workspace").value.trim(),
        },
        function () {
          toast("tests proposed — run them after you apply", "ok");
        }
      );
      if (payload) await refreshDiffs();
    });

    on("do-debug", async function () {
      const error = $("err-text").value.trim();
      if (!error) {
        toast("paste the error", "warn");
        return;
      }
      const payload = await call(
        "debug",
        "/api/code/debug",
        {
          error: error,
          code: $("err-code").value,
          workspace: $("gen-workspace").value.trim(),
        },
        function (data) {
          const result = data.result || {};
          $("err-out").innerHTML =
            '<pre class="finding-explain">' + escapeHtml(result.explanation || "") + "</pre>";
          toast(
            result.proposed
              ? "cause found, and a fix was proposed"
              : "cause explained; no file-level fix to propose",
            "ok"
          );
        }
      );
      if (payload) await refreshDiffs();
    });

    on("do-run", async function () {
      const payload = await call(
        "run",
        "/api/code/run",
        {
          path: $("run-path").value.trim() || "tests",
          workspace: $("run-workspace").value.trim(),
        },
        function (data) {
          renderTerm($("run-out"), data.result || {});
        }
      );
      if (!payload) $("run-out").innerHTML = "";
    });

    /* The three proposal buttons, bound by delegation because the cards are
       rebuilt from JSON and there are no stable ids to bind to. */
    $("diff-list").addEventListener("click", async function (event) {
      const applyId = event.target.getAttribute("data-apply");
      const rejectId = event.target.getAttribute("data-reject");
      const rollbackId = event.target.getAttribute("data-rollback");

      if (applyId) {
        const payload = await call("apply", "/api/code/apply", { diff_id: applyId }, function () {
          toast("written through Body/Fingers", "ok");
        });
        if (payload) await refreshAll();
      } else if (rejectId) {
        /* Rejecting is never gated and never will be: the user saying no is the
           system working, not a permission the user had to be granted. */
        const result = await post("/api/code/diff", { diff_id: rejectId, reject: true });
        if (result.status !== 200) toast(explain(result), "bad");
        await refreshDiffs();
      } else if (rollbackId) {
        const payload = await call(
          "rollback",
          "/api/code/rollback",
          { diff_id: rollbackId },
          function () {
            toast("rolled back — anything you edited since is gone", "warn");
          }
        );
        if (payload) await refreshAll();
      }
    });

    on("confirm-yes", async function () {
      const pending = state.pending;
      if (!pending) return;
      const button = $("confirm-yes");
      button.disabled = true;
      const result = await post(pending.path, {
        ...pending.body,
        confirmed: true,
        confirmation_id: pending.data.confirmation_id,
        dry_run: $("confirm-dry").checked,
      });
      button.disabled = false;
      if (result.status === 200) {
        const dry = $("confirm-dry").checked;
        closeConfirm();
        toast(dry ? "described — nothing was performed" : "done", "ok");
        await refreshAll();
      } else if (result.status === 202) {
        /* Two prompts in a row is the workspace question arriving after the gate
           question. Both are shown, neither is merged: they are different
           questions and a merged dialog would answer neither honestly. */
        openConfirm(result.data, pending.path, pending.body, pending.operation);
      } else {
        closeConfirm();
        toast(explain(result), "bad");
      }
    });

    on("confirm-no", function () {
      closeConfirm();
      toast("not approved — nothing happened", "ok");
    });

    global.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeConfirm();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    wire();
    refreshAll();
  });

  global.ZuzzCode = { refreshAll: refreshAll, refreshDiffs: refreshDiffs };
})(window);