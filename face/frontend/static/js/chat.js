/* Chat rendering for the ZUZZ web UI.
   Owns the message log, streaming bubble updates, action badges, the
   confirmation popup and small markdown touches. */
(function (global) {
  "use strict";

  /* An action block is protocol, never prose. The server strips every block
     before it returns a reply, but tokens arrive one at a time, so the raw JSON
     is on screen between the opening fence and the closing one. Hiding it while
     it streams is what keeps that from being visible; the authoritative prose
     arrives in the "actions" event and replaces the bubble wholesale. */
  const ACTION_FENCE = /```\s*(?:action|tool|tool_call|tool call|action json)?[^\n]*\n[\s\S]*?(?:```|$)/gi;
  const CLOSED_FENCE = /```([\s\S]*?)```/g;

  function escapeHtml(text) {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  /* Remove anything that looks like an action block, closed or not. */
  function stripActionFences(text) {
    return String(text || "").replace(ACTION_FENCE, "").replace(/\n{3,}/g, "\n\n").trim();
  }

  function renderMarkdown(text) {
    const blocks = [];
    const withBlocks = text.replace(CLOSED_FENCE, (match, code) => {
      blocks.push(code);
      return `\u0000${blocks.length - 1}\u0000`;
    });
    let html = escapeHtml(withBlocks);
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    blocks.forEach((code, index) => {
      html = html.replace(`\u0000${index}\u0000`, `<pre>${escapeHtml(code)}</pre>`);
    });
    return html;
  }

  /* One icon per tool family, so a badge is readable at a glance. */
  const TOOL_ICONS = {
    mouse_click: "🖱️",
    mouse_move: "🖱️",
    mouse_scroll: "🖱️",
    keyboard_type: "⌨️",
    keyboard_hotkey: "⌨️",
    clipboard_read: "📋",
    clipboard_write: "📋",
    open_app: "⚙️",
    close_app: "⚙️",
    file_read: "📄",
    file_write: "📄",
    file_delete: "🗑️",
    file_list: "📁",
    file_search: "🔍",
    get_system_resources: "📊"
  };

  const STATUS_MARKS = {
    executed: "✅",
    pending: "⏸️",
    blocked: "🚫",
    declined: "🚫",
    error: "⚠️"
  };

  const STATUS_WORDS = {
    executed: "done",
    pending: "waiting for you",
    blocked: "refused",
    declined: "declined",
    error: "failed"
  };

  function iconFor(tool) {
    return TOOL_ICONS[tool] || "⚙️";
  }

  /* A one-line label for one action: "open_app — chrome.exe". */
  function labelFor(entry) {
    const label = entry.target || entry.explanation || entry.reason || "";
    return label.length > 80 ? `${label.slice(0, 79)}…` : label;
  }

  class ChatLog {
    constructor(element) {
      this.element = element;
    }

    addUser(text) {
      return this._add("user", escapeHtml(text), false);
    }

    addBot() {
      return this._add("bot", "", true);
    }

    addError(text) {
      return this._add("bot", escapeHtml(text), false, "message-error");
    }

    _add(who, html, streaming, extraClass) {
      const wrapper = document.createElement("div");
      wrapper.className = `message message-${who} ${extraClass || ""}`.trim();

      const bubble = document.createElement("div");
      bubble.className = "bubble";
      bubble.innerHTML = html;
      if (streaming) {
        bubble.classList.add("cursor");
      }
      wrapper.appendChild(bubble);

      /* The action rail sits outside the bubble on purpose: a chat bubble reads
         as something ZUZZ said, and an action is something ZUZZ did. Keeping
         them in separate boxes means the user is never left wondering whether
         "Notepad is open" is a claim or a result. */
      const actions = document.createElement("div");
      actions.className = "action-rail";
      wrapper.appendChild(actions);

      const meta = document.createElement("div");
      meta.className = "meta";
      wrapper.appendChild(meta);

      this.element.appendChild(wrapper);
      this.scrollToBottom();
      return { wrapper: wrapper, bubble: bubble, actions: actions, meta: meta };
    }

    appendText(node, text) {
      const current = node.bubble.dataset.raw || "";
      const next = current + text;
      node.bubble.dataset.raw = next;
      node.bubble.innerHTML = renderMarkdown(stripActionFences(next));
      this.scrollToBottom();
    }

    /* Replace the streamed text with the server's authoritative prose. Called
       when the actions event lands, so any fence the stripper left visible while
       streaming is gone before the message is finished. */
    setProse(node, prose) {
      node.bubble.dataset.raw = prose;
      node.bubble.innerHTML = renderMarkdown(prose);
      this.scrollToBottom();
    }

    finish(node, info) {
      node.bubble.classList.remove("cursor");
      const parts = [];
      if (info && info.level) {
        parts.push(`level ${info.level}`);
      }
      if (info && info.ms !== undefined) {
        parts.push(`${info.ms} ms`);
      }
      if (info && info.streamed) {
        parts.push("streamed");
      }
      node.meta.textContent = parts.join(" · ");
    }

    /* Render one action into its own badge inside the message's action rail. */
    addActionBadge(node, entry, onConfirm) {
      const status = entry.status || "error";
      const row = document.createElement("div");
      row.className = `action-badge action-${status}`;

      const mark = document.createElement("span");
      mark.className = "action-mark";
      mark.textContent = STATUS_MARKS[status] || "•";

      const name = document.createElement("span");
      name.className = "action-name";
      name.textContent = entry.tool || "action";

      const detail = document.createElement("span");
      detail.className = "action-detail";
      detail.textContent = labelFor(entry);

      const state = document.createElement("span");
      state.className = "action-state";
      state.textContent = STATUS_WORDS[status] || status;

      row.appendChild(mark);
      row.appendChild(name);
      row.appendChild(detail);
      row.appendChild(state);

      if (entry.reason && status !== "executed") {
        row.title = entry.reason;
        const why = document.createElement("span");
        why.className = "action-reason";
        why.textContent = entry.reason;
        row.appendChild(why);
      }
      (entry.warnings || []).forEach((warning) => {
        const note = document.createElement("span");
        note.className = "action-warn";
        note.textContent = warning;
        row.appendChild(note);
      });
      (entry.errors || []).forEach((error) => {
        const note = document.createElement("span");
        note.className = "action-err";
        note.textContent = error;
        row.appendChild(note);
      });

      if (status === "pending" && entry.confirmation_id) {
        row.classList.add("action-awaiting");
        row.appendChild(
          ConfirmationPopup.build(entry, onConfirm)
        );
      }

      node.actions.appendChild(row);
      this.scrollToBottom();
      return row;
    }

    /* Render a whole actions envelope: every executed, blocked, pending and
       failed action, then the summary line. */
    addActions(node, payload, onConfirm) {
      if (!payload) {
        return;
      }
      ["executed", "blocked", "pending", "errors"].forEach((key) => {
        (payload[key] || []).forEach((entry) => {
          this.addActionBadge(node, entry, onConfirm);
        });
      });
      (payload.warnings || []).forEach((warning) => {
        const row = document.createElement("div");
        row.className = "action-badge action-warn-only";
        row.textContent = `⚠️ ${warning}`;
        node.actions.appendChild(row);
      });
      if (payload.summary) {
        const summary = document.createElement("div");
        summary.className = "action-summary";
        summary.textContent = payload.summary;
        node.actions.appendChild(summary);
      }
      this.scrollToBottom();
    }

    /* Jarvis-style enhancement rendering (Phase 4.5).

       Four renderers, one per event the server emits alongside the
       tokens: a live thinking indicator while the model reasons, a
       confidence badge on the finished reply, dismissible suggestion
       cards, and a subtle mood indicator. Each attaches to the node
       the streaming bubble already created, so an enhancement is
       visibly part of the reply it describes. */

    /* One pulsing indicator per bubble, updated in place: a stream of
       steps is one thought process, not one badge per step. */
    showThinkingIndicator(node, step, status) {
      if (!node) {
        return;
      }
      let indicator = node.wrapper.querySelector(".thinking-indicator");
      if (!indicator) {
        indicator = document.createElement("div");
        indicator.className = "thinking-indicator";
        const dot = document.createElement("span");
        dot.className = "thinking-dot";
        indicator.appendChild(dot);
        const label = document.createElement("span");
        label.className = "thinking-text";
        indicator.appendChild(label);
        node.wrapper.insertBefore(indicator, node.meta);
      }
      const text = indicator.querySelector(".thinking-text");
      const kind = step && step.kind ? step.kind : "";
      const content = step && step.text ? step.text : "";
      indicator.dataset.kind = kind;
      indicator.dataset.status = status || "active";
      text.textContent = status === "done"
        ? "finished thinking"
        : (kind ? kind + " — " : "") + content;
      this.scrollToBottom();
      return indicator;
    }

    /* Confidence badge: green/yellow/red by tier. Category is the
       server's label ("high"/"medium"/"low") and score is 0–1. */
    attachConfidenceBadge(node, score, category) {
      if (!node) {
        return;
      }
      const tier = (category || "medium").toLowerCase();
      const rail = node.meta;
      const existing = rail.querySelector(".confidence-badge");
      if (existing) {
        existing.remove();
      }
      const badge = document.createElement("span");
      badge.className = `confidence-badge confidence-${tier}`;
      badge.dataset.score = String(score);
      badge.dataset.category = tier;
      badge.textContent = `${Math.round(Number(score) * 100)}% confidence`;
      badge.title = `Confidence tier: ${tier}`;
      rail.appendChild(badge);
      this.scrollToBottom();
      return badge;
    }

    /* Suggestion card: dismissible, sits in the action rail next to
       whatever the reply did. basedOn is what the suggestion was
       derived from (its category), shown as the card's context. */
    renderSuggestionCard(node, suggestion, basedOn) {
      if (!node || !suggestion) {
        return;
      }
      const card = document.createElement("div");
      card.className = "suggestion-card";
      card.dataset.category = suggestion.category || basedOn || "";

      const label = document.createElement("span");
      label.className = "suggestion-label";
      label.textContent = suggestion.text || "";
      card.appendChild(label);

      if (basedOn || suggestion.category) {
        const basis = document.createElement("span");
        basis.className = "suggestion-basis";
        basis.textContent = `based on ${suggestion.category || basedOn}`;
        card.appendChild(basis);
      }

      const confidence = Number(suggestion.confidence);
      if (!Number.isNaN(confidence)) {
        const meter = document.createElement("span");
        meter.className = "suggestion-confidence";
        meter.textContent = `${Math.round(confidence * 100)}%`;
        card.appendChild(meter);
      }

      const dismiss = document.createElement("button");
      dismiss.type = "button";
      dismiss.className = "suggestion-dismiss";
      dismiss.textContent = "×";
      dismiss.title = "Dismiss suggestion";
      dismiss.addEventListener("click", () => card.remove());
      card.appendChild(dismiss);

      node.actions.appendChild(card);
      this.scrollToBottom();
      return card;
    }

    /* Mood indicator: a subtle marker on the meta line showing the
       detected mood and how the reply was adjusted for it. */
    showMoodIndicator(node, detected, adjustment) {
      if (!node) {
        return;
      }
      const existing = node.meta.querySelector(".mood-indicator");
      if (existing) {
        existing.remove();
      }
      const indicator = document.createElement("span");
      indicator.className = `mood-indicator mood-${String(detected || "neutral").toLowerCase()}`;
      indicator.dataset.mood = detected || "neutral";
      indicator.textContent = detected && detected !== "neutral"
        ? `mood: ${detected}`
        : "";
      if (adjustment && detected && detected !== "neutral") {
        indicator.title = String(adjustment);
      }
      if (!indicator.textContent) {
        return indicator;
      }
      node.meta.appendChild(indicator);
      this.scrollToBottom();
      return indicator;
    }

    clear() {
      this.element.innerHTML = "";
    }

    scrollToBottom() {
      this.element.scrollTop = this.element.scrollHeight;
    }
  }

  /* The approval control for a `pending` action.
     Built as a DOM fragment rather than a floating dialog so it sits in the same
     row as the action it approves — a modal would hide which of several pending
     actions the button belongs to. */
  const ConfirmationPopup = {
    build(entry, onConfirm) {
      const box = document.createElement("span");
      box.className = "confirm-box";

      const approve = document.createElement("button");
      approve.type = "button";
      approve.className = "confirm-yes";
      approve.textContent = "Approve";

      const deny = document.createElement("button");
      deny.type = "button";
      deny.className = "confirm-no";
      deny.textContent = "Decline";

      const settle = (approval, button) => {
        approve.disabled = true;
        deny.disabled = true;
        button.textContent = approval ? "approved…" : "declined";
        if (typeof onConfirm === "function") {
          onConfirm(entry.confirmation_id, approval);
        }
      };

      approve.addEventListener("click", () => settle(true, approve));
      deny.addEventListener("click", () => settle(false, deny));

      box.appendChild(approve);
      box.appendChild(deny);
      return box;
    },

    /* Replace a settled confirmation row with what the server said happened, so
       the user sees the outcome rather than a button that stopped working. */
    resolve(node, entry) {
      const rail = node && node.actions;
      if (!rail) {
        return;
      }
      const row = rail.querySelector(".action-awaiting");
      if (!row) {
        rail.appendChild(document.createTextNode(" "));
        return;
      }
      row.classList.remove("action-awaiting");
      const status = entry.status || "executed";
      row.className = `action-badge action-${status}`;
      const mark = row.querySelector(".action-mark");
      const state = row.querySelector(".action-state");
      const box = row.querySelector(".confirm-box");
      if (box) {
        box.remove();
      }
      if (mark) {
        mark.textContent = STATUS_MARKS[status] || "•";
      }
      if (state) {
        state.textContent = STATUS_WORDS[status] || status;
      }
      if (status !== "executed" && entry.reason) {
        const why = document.createElement("span");
        why.className = "action-reason";
        why.textContent = entry.reason;
        row.appendChild(why);
      }
    }
  };

  /* POST an answer and hand the outcome back. One place so the fetch shape is
     defined once and the WebSocket and HTTP paths cannot disagree. */
  function postConfirmation(confirmationId, approval) {
    return fetch("/api/chat/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirmation_id: confirmationId, approval: approval })
    })
      .then((response) => response.json().then((data) => ({ ok: response.ok, data: data })))
      .catch((err) => ({
        ok: false,
        data: { errors: [{ tool: "confirm", status: "error", errors: [String(err)] }] }
      }));
  }

  /* Event handlers for the enhancement events the server emits
     alongside the token stream. Each takes the ChatLog, the node
     the streaming bubble created (app.js tracks it), and the raw
     event. */
  global.ChatLog = ChatLog;
  global.ConfirmationPopup = ConfirmationPopup;
  global.postConfirmation = postConfirmation;
  global.renderMarkdown = renderMarkdown;
  global.stripActionFences = stripActionFences;

  /* Enhancement event handlers, wired in app.js. They reach the
     ChatLog through the log element every handler is given, so
     they work for both the WebSocket and any future surface. */
  global.EnhancementEvents = {
    thinking: function (log, node, event) {
      const step = event.step || {
        text: event.text || event.content || "",
        kind: event.kind || "",
        index: event.index || 0,
      };
      return log.showThinkingIndicator(node, step, "active");
    },
    confidence: function (log, node, event) {
      return log.attachConfidenceBadge(
        node,
        event.score,
        event.label || event.category || "medium"
      );
    },
    suggestion: function (log, node, event) {
      const suggestion = {
        text: event.text,
        category: event.category,
        confidence: event.confidence,
        action: event.action,
      };
      return log.renderSuggestionCard(node, suggestion, event.basedOn);
    },
    mood: function (log, node, event) {
      return log.showMoodIndicator(
        node,
        event.detected || event.mood,
        event.adjustment
      );
    },
  };
})(window);