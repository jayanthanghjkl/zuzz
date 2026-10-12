/* Application wiring for the ZUZZ web UI.
   Connects the socket, the chat log and the composer controls. */
(function () {
  "use strict";

  const logElement = document.getElementById("chat-log");
  const input = document.getElementById("input");
  const sendBtn = document.getElementById("send-btn");
  const clearBtn = document.getElementById("clear-btn");
  const statusBtn = document.getElementById("status-btn");
  const connBadge = document.getElementById("conn-badge");
  const levelBadge = document.getElementById("level-badge");
  const levelSelect = document.getElementById("level-select");
  const hint = document.getElementById("hint");
  const statusPanel = document.getElementById("status-panel");
  const statusJson = document.getElementById("status-json");
  const providerDots = document.getElementById("provider-dots");

  const chat = new ChatLog(logElement);
  let activeBubble = null;
  let finishedBubble = null;
  let startedAt = 0;
  let stopped = false;

  /* Answer a confirmation and settle the row the user clicked. The outcome —
     executed, or blocked again by a fresh decision — replaces the buttons, so a
     re-block after approval is visible rather than silent. */
  function answerConfirmation(node, confirmationId, approval) {
    postConfirmation(confirmationId, approval).then((outcome) => {
      if (!outcome.ok) {
        ConfirmationPopup.resolve(node, {
          status: "error",
          reason: (outcome.data && outcome.data.detail) || "the approval could not be sent"
        });
        return;
      }
      const actions = outcome.data || {};
      const settled = (actions.executed || [])[0] || (actions.blocked || [])[0] ||
        (actions.errors || [])[0] || { status: approval ? "executed" : "declined" };
      ConfirmationPopup.resolve(node, settled);
      const summary = document.createElement("div");
      summary.className = "action-summary";
      summary.textContent = actions.summary || "";
      if (summary.textContent) {
        node.actions.appendChild(summary);
      }
    });
  }

  const socket = new BrainSocket({
    status: onSocketStatus,
    event: onBrainEvent
  });

  /* The kill switch control reports whether ZUZZ is stopped, so the composer can
     be disabled rather than letting a doomed request be sent. */
  const killSwitch = new KillSwitchControl({
    button: document.getElementById("stop-btn"),
    badge: document.getElementById("kill-badge"),
    banner: document.getElementById("stop-banner"),
    bannerText: document.getElementById("stop-banner-text"),
    resetButton: document.getElementById("stop-reset-btn"),
    wordInput: document.getElementById("stop-word"),
    cooldown: document.getElementById("stop-cooldown"),
    onChange: (isStopped) => {
      stopped = isStopped;
      sendBtn.disabled = isStopped;
      input.disabled = isStopped;
    }
  });

  function setConnection(state) {
    const labels = { open: "live", closed: "reconnecting", error: "offline" };
    connBadge.textContent = labels[state] || state;
    connBadge.className = "badge " + (state === "open" ? "badge-ok" : "badge-warn");
  }

  function onSocketStatus(payload) {
    setConnection(payload.state);
  }

  function onBrainEvent(event) {
    if (event.type === "token") {
      if (!activeBubble) {
        activeBubble = chat.addBot();
      }
      chat.appendText(activeBubble, event.content);
    } else if (event.type === "thinking") {
      /* A reasoning step arrived mid-stream: update the pulsing
         indicator on the bubble being filled. */
      if (EnhancementEvents.thinking) {
        EnhancementEvents.thinking(chat, activeBubble, event);
      }
    } else if (event.type === "confidence") {
      EnhancementEvents.confidence(chat, activeBubble || chat.addBot(), event);
    } else if (event.type === "suggestion") {
      EnhancementEvents.suggestion(
        chat,
        activeBubble || finishedBubble || chat.addBot(),
        event
      );
    } else if (event.type === "mood") {
      EnhancementEvents.mood(
        chat,
        activeBubble || finishedBubble || chat.addBot(),
        event
      );
    } else if (event.type === "done") {
      const ms = startedAt ? Math.round(performance.now() - startedAt) : undefined;
      chat.finish(activeBubble || chat.addBot(), {
        level: event.streamed_level || event.level,
        ms: ms,
        streamed: true
      });
      /* The reasoning is over: settle the indicator rather than
         leaving a pulsing dot on a finished reply. */
      if (activeBubble) {
        chat.showThinkingIndicator(activeBubble, { text: "", kind: "" }, "done");
      }
      levelBadge.textContent = "L" + event.level;
      hint.textContent = event.reason || "Streaming via WebSocket";
      if (activeBubble) {
        /* Held rather than cleared: the actions event arrives next and needs the
           same bubble to hang its badges off. */
        finishedBubble = activeBubble;
      }
      setBusy(false);
    } else if (event.type === "actions") {
      const node = activeBubble || finishedBubble || chat.addBot();
      if (typeof event.prose === "string") {
        chat.setProse(node, event.prose);
      }
      chat.addActions(node, event, (id, approval) =>
        answerConfirmation(node, id, approval)
      );
      if (event.summary) {
        hint.textContent = event.summary;
      }
      finishedBubble = null;
    } else if (event.type === "refused") {
      chat.addError(
        (event.content || "ZUZZ is stopped") +
          " — type RESET in the stop panel to restart it."
      );
      hint.textContent = "Stopped";
      activeBubble = null;
      finishedBubble = null;
      setBusy(false);
    } else if (event.type === "error") {
      if (activeBubble) {
        chat.appendText(activeBubble, "");
        activeBubble = null;
      } else {
        chat.addError(event.content);
      }
      finishedBubble = null;
      hint.textContent = "Error: " + event.content;
      setBusy(false);
    }
  }

  function setBusy(busy) {
    sendBtn.disabled = busy || stopped;
    input.disabled = stopped;
  }

  function selectedLevel() {
    const value = levelSelect.value;
    return value === "auto" ? null : parseInt(value, 10);
  }

  function send() {
    if (stopped) {
      hint.textContent = "ZUZZ is stopped. Type RESET to restart it.";
      return;
    }
    const message = input.value.trim();
    if (!message) {
      return;
    }
    chat.addUser(message);
    input.value = "";
    autoGrow();
    startedAt = performance.now();
    activeBubble = chat.addBot();
    finishedBubble = null;

    if (!socket.isOpen()) {
      hint.textContent = "Waiting for connection...";
      return;
    }
    if (!socket.send(message, selectedLevel())) {
      chat.appendText(activeBubble, "(message could not be sent)");
      activeBubble = null;
    }
    setBusy(true);
  }

  function autoGrow() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 180) + "px";
  }

  async function refreshStatus() {
    statusJson.textContent = "loading...";
    try {
      const response = await fetch("/api/brain/status");
      const data = await response.json();
      statusJson.textContent = JSON.stringify(data, null, 2);
      if (data.heartbeat) {
        hint.textContent =
          "uptime " + data.heartbeat.uptime_seconds + "s · RAM " +
          data.heartbeat.ram_mb + " MB · ollama " +
          (data.heartbeat.ollama_up ? "up" : "down");
      }
    } catch (err) {
      statusJson.textContent = "failed to load status: " + err;
    }
  }

  async function refreshProviders() {
    try {
      const response = await fetch("/api/providers");
      const data = await response.json();
      renderProviderDots(data.providers || []);
    } catch (err) {
      providerDots.innerHTML = '<span class="dot-label">providers offline</span>';
    }
  }

  function renderProviderDots(providers) {
    if (!providers.length) {
      providerDots.innerHTML = '<span class="dot-label">no providers</span>';
      return;
    }
    const html = providers
      .map((provider) => {
        const title = `${provider.name}: ${provider.state} - ${provider.detail}`;
        return (
          '<span class="dot-label">' + provider.name + "</span>" +
          '<span class="dot dot-' + provider.state + '" title="' + title + '"></span>'
        );
      })
      .join("");
    providerDots.innerHTML = html;
  }

  sendBtn.addEventListener("click", send);
  clearBtn.addEventListener("click", () => {
    chat.clear();
    chat.addBot();
  });
  statusBtn.addEventListener("click", () => {
    statusPanel.classList.toggle("hidden");
    refreshStatus();
  });
  input.addEventListener("input", autoGrow);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });

  socket.connect();
  killSwitch.start();
  refreshStatus();
  refreshProviders();
  setInterval(refreshProviders, 15000);
})();
