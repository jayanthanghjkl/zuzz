/* Kill switch controls for the ZUZZ web UI.

   The Stop button exists because a global hotkey is easy to forget, and the reset
   dialog exists because re-arming the machine is the one action that must never
   be a single click. */
(function (global) {
  "use strict";

  const POLL_MS = 5000;

  class KillSwitchControl {
    constructor(options) {
      this.button = options.button;
      this.badge = options.badge;
      this.banner = options.banner;
      this.bannerText = options.bannerText;
      this.resetButton = options.resetButton;
      this.wordInput = options.wordInput;
      this.cooldown = options.cooldown;
      this.onChange = options.onChange || function () {};
      this.state = null;
    }

    start() {
      this.button.addEventListener("click", () => this.stop());
      this.resetButton.addEventListener("click", () => this.reset());
      this.refresh();
      setInterval(() => this.refresh(), POLL_MS);
    }

    async refresh() {
      try {
        const response = await fetch("/api/kill-switch");
        this.render(await response.json());
      } catch (err) {
        this.setBadge("offline", "stop offline");
      }
    }

    render(state) {
      this.state = state;
      if (!state.engaged) {
        this.setBadge("badge-ok", "running");
        this.banner.classList.add("hidden");
        this.button.disabled = false;
        this.button.textContent = "Stop";
        this.resetButton.classList.add("hidden");
        this.onChange(false);
        return;
      }
      this.setBadge("badge-err", "stopped");
      this.showBanner(state);
      this.button.textContent = "Stopped";
      this.resetButton.classList.remove("hidden");
      this.onChange(true);
    }

    showBanner(state) {
      const source = state.source || "unknown";
      const reason = state.reason || "emergency stop engaged";
      this.bannerText.textContent = "ZUZZ is stopped (" + source + "): " + reason;
      const waiting = state.cooldown_remaining || 0;
      if (waiting > 0) {
        this.cooldown.textContent = "reset available in " + waiting + "s";
      } else {
        this.cooldown.textContent = state.reset_instruction || "";
      }
      this.banner.classList.remove("hidden");
    }

    setBadge(className, label) {
      this.badge.className = "badge " + className;
      this.badge.textContent = label;
    }

    async stop() {
      this.button.disabled = true;
      try {
        await fetch("/api/kill-switch/stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: "stopped from the UI", source: "ui" })
        });
      } finally {
        await this.refresh();
      }
    }

    async reset() {
      const word = this.wordInput.value.trim();
      try {
        const response = await fetch("/api/kill-switch/reset", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ typed_word: word, confirmed: true })
        });
        if (!response.ok) {
          const body = await response.json();
          this.cooldown.textContent = body.detail || "reset refused";
          return;
        }
        this.wordInput.value = "";
      } catch (err) {
        this.cooldown.textContent = "reset failed: " + err;
      }
      await this.refresh();
    }
  }

  global.KillSwitchControl = KillSwitchControl;
})(window);
