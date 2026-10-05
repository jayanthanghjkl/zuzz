/* Habits page — the routines ZUZZ can run, and what running one would do.

   The plan is the important half of this page. Pressing "Plan" does not
   run anything; it asks the server what every step would be judged as at
   the current trust level, and shows all of it. That is deliberate: a
   routine with five steps whose third one needs confirming should not
   have steps one and two already applied by the time you find out.

   Nothing on this page executes a routine. The server does not offer an
   endpoint that does, and the plan says so — "run each step as its own
   tool call" means going back to chat and asking for them one at a time,
   each going through the same gate as any other action. Confirming a
   routine's steps here is not a shortcut into that gate. */

(function (global) {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const state = {
    routines: [],
    plan: null,
    confirmed: {},
    patterns: [],
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
  const patch = (path, body) => send("PATCH", path, body || {});
  const del = (path) => send("DELETE", path);

  function explain(result) {
    const detail = result.data && result.data.detail;
    if (typeof detail === "string") return detail;
    if (detail && detail.reason) {
      return detail.reason + (detail.note ? " — " + detail.note : "");
    }
    if (Array.isArray(detail) && detail.length) {
      return detail
        .map(function (item) {
          return (item.loc || []).slice(1).join(".") + ": " + item.msg;
        })
        .join("; ");
    }
    if (result.status === 503) return "routines are not attached to ZUZZ";
    if (result.status === 404) return "there is no such routine";
    if (result.status === 409) return "that routine is switched off";
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

  // -- routine list ---------------------------------------------------

  function renderRoutines(payload) {
    const list = $("routine-list");
    $("routine-count").textContent =
      payload.count + " routine" + (payload.count === 1 ? "" : "s") +
      " · " + payload.enabled + " armed";

    if (!payload.routines.length) {
      list.innerHTML = '<p class="prose">No routines saved yet.</p>';
      return;
    }

    const due = payload.due_now || [];
    list.innerHTML = payload.routines
      .map(function (routine) {
        const armed = routine.enabled;
        const steps = routine.steps || [];
        const stepList = steps.length
          ? '<ol class="step-list">' +
            steps
              .map(function (step) {
                return (
                  "<li><code>" +
                  escapeHtml(step.tool) +
                  "</code>" +
                  (step.why ? " — " + escapeHtml(step.why) : "") +
                  "</li>"
                );
              })
              .join("") +
            "</ol>"
          : "";

        return (
          '<div class="routine ' +
          (armed ? "armed" : "disarmed") +
          '" data-name="' +
          escapeHtml(routine.name) +
          '">' +
          '<div class="routine-top">' +
          '<span class="routine-name">' +
          escapeHtml(routine.name) +
          "</span>" +
          '<span class="muted">' +
          steps.length +
          " step" +
          (steps.length === 1 ? "" : "s") +
          "</span>" +
          "</div>" +
          (routine.description
            ? '<p class="routine-why">' + escapeHtml(routine.description) + "</p>"
            : "") +
          '<div class="routine-meta">' +
          '<span class="pill ' +
          (armed ? "armed" : "disarmed") +
          '">' +
          (armed ? "armed" : "disarmed") +
          "</span>" +
          '<span class="pill">' +
          escapeHtml((routine.trigger || {}).description || "manual") +
          "</span>" +
          (due.indexOf(routine.name) >= 0 ? '<span class="pill due">due now</span>' : "") +
          "</div>" +
          stepList +
          '<div class="btn-row">' +
          '<button class="ghost-btn" type="button" data-act="plan" data-name="' +
          escapeHtml(routine.name) +
          '">Plan</button>' +
          '<button class="ghost-btn" type="button" data-act="toggle" data-name="' +
          escapeHtml(routine.name) +
          '">' +
          (armed ? "Disarm" : "Arm") +
          "</button>" +
          '<button class="ghost-btn danger" type="button" data-act="delete" data-name="' +
          escapeHtml(routine.name) +
          '">Delete</button>' +
          "</div>" +
          "</div>"
        );
      })
      .join("");
  }

  async function refresh() {
    const result = await get("/api/habits/routines?include_steps=true");
    if (result.status !== 200) {
      toast(explain(result), "bad");
      return;
    }
    state.routines = result.data.routines || [];
    renderRoutines(result.data);
    $("routines-path").textContent = result.data.path || "";
  }

  // -- the plan -------------------------------------------------------

  function renderPlan(plan) {
    const pending = plan.pending || [];
    const refused = plan.refused || [];
    const steps = plan.steps || [];

    const verdict = refused.length
      ? { cls: "refused", text: "Blocked at this trust level" }
      : pending.length
        ? { cls: "pending", text: "Needs confirming" }
        : { cls: "runnable", text: "Every step would be allowed" };

    const sub = [];
    sub.push("trust level " + plan.trust_level);
    sub.push(steps.length + " step" + (steps.length === 1 ? "" : "s"));
    if (pending.length) {
      sub.push(pending.length + " pending");
    }
    if (refused.length) sub.push(refused.length + " refused");

    const rows = steps
      .map(function (step) {
        const v = step.verdict || "ask";
        const blocked = v === "blocked";
        const confirmBox =
          v === "ask"
            ? '<label class="step-confirm" title="Mark this step as approved">' +
              '<input type="checkbox" data-step="' +
              step.step +
              '"' +
              (state.confirmed[step.step] ? " checked" : "") +
              "></label>"
            : '<span class="step-num"></span>';
        return (
          '<div class="plan-step">' +
          (blocked ? '<span class="step-num"></span>' : confirmBox) +
          '<div class="step-body">' +
          '<div class="step-tool">' +
          step.step +
          ". " +
          escapeHtml(step.tool) +
          "</div>" +
          (step.why || step.reason
            ? '<div class="step-why">' +
              escapeHtml(step.why || step.reason) +
              "</div>"
            : "") +
          (step.target
            ? '<div class="step-why">target: ' + escapeHtml(step.target) + "</div>"
            : "") +
          "</div>" +
          '<span class="step-verdict ' +
          v +
          '">' +
          escapeHtml(v) +
          "</span>" +
          "</div>"
        );
      })
      .join("");

    $("plan").innerHTML =
      '<div class="plan-head">' +
      '<div class="plan-verdict ' +
      verdict.cls +
      '">' +
      verdict.text +
      "</div>" +
      '<p class="plan-sub">' +
      escapeHtml(plan.next || "") +
      " &middot; " +
      escapeHtml(sub.join(" · ")) +
      "</p>" +
      "</div>" +
      '<div class="plan-steps">' +
      rows +
      "</div>" +
      '<div class="plan-head" style="border-bottom:none;border-top:1px solid var(--line,#262b36)">' +
      '<p class="plan-sub">Nothing has run. ZUZZ has no endpoint that executes a whole routine — ' +
      "run the permitted steps as ordinary tool calls and each one goes through the gate on its own.</p>" +
      "</div>";
  }

  async function planRoutine(name) {
    const confirmed = Object.keys(state.confirmed)
      .filter(function (step) {
        return state.confirmed[step];
      })
      .map(Number);

    const result = await post("/api/habits/execute", {
      name: name,
      confirm_steps: confirmed,
    });
    if (result.status !== 200) {
      $("plan").innerHTML =
        '<div class="plan-head"><div class="plan-verdict refused">Cannot plan</div>' +
        '<p class="plan-sub">' +
        escapeHtml(explain(result)) +
        "</p></div>";
      return;
    }
    state.plan = result.data;
    renderPlan(result.data);
  }

  /* Re-planning on every checkbox keeps the confirmed list honest: the plan
     sent to the server is always the one the user can currently see. */
  function onConfirmChange(event) {
    const box = event.target;
    if (!box.matches || !box.matches('input[data-step]')) return;
    state.confirmed[box.getAttribute("data-step")] = box.checked;
    if (state.plan) planRoutine(state.plan.routine.name);
  }

  // -- creating -------------------------------------------------------

  /* One step per line:

         tool | key=value, key=value | why

     A pipe, then the tool's parameters as ``key=value``, then a pipe and a
     reason. Deliberately not JSON: asking a user to type valid JSON with
     nested objects is how you get a routine that stores fine and cannot run.
     The parameters are not optional in practice — most tools ZUZZ has require
     one, and the server refuses a routine whose parameters its tool's schema
     will not accept, so a step line that omitted them could never be saved.
     Values are sent as strings and coerced by the tool's own schema, which is
     what the chat path does too.
  */
  function parseSteps(raw) {
    const lines = raw
      .split("\n")
      .map(function (line) {
        return line.trim();
      })
      .filter(Boolean);
    const steps = [];
    const problems = [];
    lines.forEach(function (line, index) {
      const parts = line.split("|").map(function (part) {
        return part.trim();
      });
      const tool = parts[0] || "";
      if (!tool) return;
      if (!/^[a-z0-9_]+$/.test(tool)) {
        problems.push("line " + (index + 1) + ": '" + tool + "' is not a tool name");
        return;
      }
      const params = {};
      (parts[1] || "").split(",").forEach(function (pair) {
        const text = pair.trim();
        if (!text) return;
        const split = text.indexOf("=");
        if (split < 1) {
          problems.push(
            "line " + (index + 1) + ": '" + text + "' is not key=value"
          );
          return;
        }
        params[text.slice(0, split).trim()] = text.slice(split + 1).trim();
      });
      steps.push({ tool: tool, params: params, why: parts[2] || "" });
    });
    return { steps: steps, problems: problems };
  }

  async function createRoutine() {
    const name = $("new-name").value.trim();
    const description = $("new-description").value.trim();
    const triggerKind = $("new-trigger").value;
    const triggerValue = $("new-trigger-value").value.trim();
    const parsed = parseSteps($("new-steps").value);

    if (!/^[a-z0-9_]+$/.test(name)) {
      toast("a routine name may use only lowercase letters, digits and underscores", "bad");
      return;
    }
    if (!parsed.steps.length) {
      toast(
        parsed.problems.length ? parsed.problems.join("; ") : "add at least one step",
        "bad"
      );
      return;
    }
    if (parsed.problems.length) {
      toast(parsed.problems.join("; "), "bad");
      return;
    }

    const trigger = { kind: triggerKind };
    if (triggerKind === "time") trigger.at = triggerValue;
    if (triggerKind === "app") trigger.app = triggerValue;
    if (triggerKind === "command") trigger.command = triggerValue;

    const result = await post("/api/habits/routine", {
      name: name,
      description: description,
      steps: parsed.steps,
      trigger: trigger,
    });
    if (result.status !== 200) {
      toast(explain(result), "bad");
      return;
    }
    toast(
      "saved '" + name + "' — stored disarmed, arm it when you have read the steps",
      "ok"
    );
    $("new-name").value = "";
    $("new-description").value = "";
    $("new-steps").value = "";
    $("new-trigger-value").value = "";
    state.confirmed = {};
    await refresh();
  }

  // -- patterns -------------------------------------------------------

  function renderPatterns(payload) {
    const list = $("pattern-list");
    $("pattern-count").textContent =
      payload.count + " pattern" + (payload.count === 1 ? "" : "s") +
      " seen " + payload.min_repetitions + "+ times";
    if (!payload.patterns.length) {
      list.innerHTML =
        '<p class="prose">No repeated sequence yet. Detection needs the same sequence at least ' +
        payload.min_repetitions +
        " times.</p>";
      return;
    }
    list.innerHTML = payload.patterns
      .map(function (pattern) {
        return (
          '<div class="pattern">' +
          '<div class="pattern-head">' +
          '<span class="pattern-seq">' +
          escapeHtml((pattern.sequence || []).join(" → ")) +
          "</span>" +
          '<span class="pill">' +
          pattern.repetitions +
          "&times;</span>" +
          "</div>" +
          '<div class="pattern-why">' +
          escapeHtml(pattern.why || "") +
          "</div>" +
          "</div>"
        );
      })
      .join("");
  }

  async function refreshPatterns() {
    const min = $("pattern-min").value || 5;
    const result = await get("/api/habits/patterns?min_repetitions=" + encodeURIComponent(min));
    if (result.status === 200) {
      state.patterns = result.data.patterns || [];
      renderPatterns(result.data);
    }
  }

  // -- actions --------------------------------------------------------

  async function onRoutineAction(event) {
    const button = event.target.closest("button[data-act]");
    if (!button) return;
    const name = button.getAttribute("data-name");
    const act = button.getAttribute("data-act");

    if (act === "plan") {
      state.confirmed = {};
      await planRoutine(name);
      return;
    }
    if (act === "toggle") {
      const routine = state.routines.filter(function (item) {
        return item.name === name;
      })[0];
      const arming = !(routine && routine.enabled);
      // ``/api/habits/arm``, not a PATCH carrying ``enabled``. Arming is the one
      // edit on this page with consequences, so it has a route of its own — and
      // that route answers with the plan, which is worth showing: pressing "Arm"
      // should be the moment the user learns what that actually permits at the
      // trust level they are currently at.
      const result = await post("/api/habits/arm", { name: name, enabled: arming });
      if (result.status !== 200) {
        toast(explain(result), "bad");
        return;
      }
      const plan = result.data && result.data.plan;
      if (arming && plan && plan.steps) {
        const pending = (plan.pending || []).length;
        const refused = (plan.refused || []).length;
        toast(
          "'" + name + "' armed — " + plan.steps + " step(s) at trust level " +
            plan.trust_level + ", " + plan.runnable + " permitted, " +
            pending + " need confirmation, " + refused + " refused",
          refused ? "warn" : "ok"
        );
      } else {
        toast(arming ? "'" + name + "' armed — it will run on its trigger" : "'" + name + "' disarmed", "ok");
      }
      await refresh();
      return;
    }
    if (act === "delete") {
      /* Two steps, because a delete has no dry_run to preview it. Confirm()
       is weak on its own — it is a single keypress that dismisses the tab if
       it is not the focused element — so the name is typed instead. */
      const typed = global.prompt(
        "Delete the routine '" + name + "'? This cannot be undone. Type its name to confirm:"
      );
      if (typed === null) return;
      if (typed.trim() !== name) {
        toast("the name did not match, so nothing was deleted", "warn");
        return;
      }
      const result = await del("/api/habits/routine/" + encodeURIComponent(name));
      if (result.status !== 200) {
        toast(explain(result), "bad");
        return;
      }
      toast("deleted '" + name + "'", "ok");
      if (state.plan && state.plan.routine.name === name) {
        state.plan = null;
        $("plan").innerHTML = "";
      }
      await refresh();
    }
  }

  function on(id, handler) {
    const node = $(id);
    if (node) node.addEventListener("click", handler);
  }

  function wire() {
    $("routine-list").addEventListener("click", onRoutineAction);
    $("plan").addEventListener("change", onConfirmChange);
    on("routines-refresh", refresh);
    on("routine-create", createRoutine);
    on("patterns-refresh", refreshPatterns);
    on("patterns-scan", refreshPatterns);
  }

  document.addEventListener("DOMContentLoaded", function () {
    wire();
    refresh();
    refreshPatterns();
  });

  global.ZuzzHabits = { state: state, refresh: refresh, plan: planRoutine };
})(window);