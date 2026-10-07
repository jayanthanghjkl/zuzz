/* Settings page — trust, models, personality, voice, theme. */

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  async function getJSON(path) {
    const res = await fetch(path);
    return await res.json();
  }

  async function postJSON(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return { status: res.status, data: await res.json().catch(() => ({})) };
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

  function explain(result) {
    const detail = result.data && result.data.detail;
    if (typeof detail === "string") return detail;
    return "request failed (" + result.status + ")";
  }

  async function loadSettings() {
    const data = await getJSON("/api/settings");
    if (!data) return;
    if (data.trust_level != null) $("trust-level").value = String(data.trust_level);
    if (data.settings) {
      const s = data.settings;
      if (s.streaming != null) $("streaming-pref").value = String(s.streaming);
    }
    const personality = await getJSON("/api/personality/settings");
    if (personality && personality.status === 200) {
      const p = personality.data;
      if (p.style) $("style-sel").value = p.style;
      if (p.humor_level) $("humor-sel").value = p.humor_level;
      if (p.verbosity) $("verb-sel").value = p.verbosity;
    }
  }

  async function saveTrust() {
    const level = parseInt($("trust-level").value, 10);
    const result = await postJSON("/api/safety/trust", {
      level: level,
      confirmed: true,
      typed_word: "RESET",
      acknowledged_cooldown: true,
    });
    if (result.status === 200) {
      toast("trust level updated to L" + level, "ok");
      $("trust-warn").textContent = "";
    } else {
      toast(explain(result), "bad");
      $("trust-warn").textContent = explain(result);
    }
  }

  async function saveModels() {
    const payload = {
      streaming: $("streaming-pref").value === "true",
    };
    const result = await postJSON("/api/settings", payload);
    if (result.status === 200) toast("model settings saved", "ok");
    else toast(explain(result), "bad");
  }

  async function savePersonality() {
    const changes = {};
    const style = $("style-sel").value;
    const humor = $("humor-sel").value;
    const verb = $("verb-sel").value;
    if (style) changes.style = style;
    if (humor) changes.humor_level = humor;
    if (verb) changes.verbosity = verb;
    if (!Object.keys(changes).length) {
      toast("nothing changed", "warn");
      return;
    }
    const result = await postJSON("/api/personality/settings", { settings: changes });
    if (result.status === 200) toast("personality saved", "ok");
    else toast(explain(result), "bad");
  }

  async function saveVoice() {
    const changes = {
      voice_id: $("voice-sel").value,
      speed: Number($("voice-speed").value),
    };
    const result = await postJSON("/api/personality/settings", { settings: changes });
    if (result.status === 200) toast("voice saved", "ok");
    else toast(explain(result), "bad");
  }

  function applyTheme() {
    const theme = $("theme-sel").value;
    const root = document.documentElement;
    if (theme === "light") {
      root.style.setProperty("--bg", "#f6f8fa");
      root.style.setProperty("--panel", "#ffffff");
      root.style.setProperty("--panel-2", "#f3f4f6");
      root.style.setProperty("--border", "#d0d7de");
      root.style.setProperty("--text", "#1f2328");
      root.style.setProperty("--muted", "#656d76");
    } else if (theme === "high-contrast") {
      root.style.setProperty("--bg", "#000000");
      root.style.setProperty("--panel", "#0a0a0a");
      root.style.setProperty("--panel-2", "#141414");
      root.style.setProperty("--border", "#ffffff");
      root.style.setProperty("--text", "#ffffff");
      root.style.setProperty("--muted", "#ffff00");
    } else {
      root.style.setProperty("--bg", "#0d1117");
      root.style.setProperty("--panel", "#161b22");
      root.style.setProperty("--panel-2", "#1c2430");
      root.style.setProperty("--border", "#2b3644");
      root.style.setProperty("--text", "#e6edf3");
      root.style.setProperty("--muted", "#8b98a5");
    }
    localStorage.setItem("zuzz_theme", theme);
  }

  function wire() {
    $("trust-save").addEventListener("click", saveTrust);
    $("model-save").addEventListener("click", saveModels);
    $("personality-save").addEventListener("click", savePersonality);
    $("voice-save").addEventListener("click", saveVoice);
    $("theme-save").addEventListener("click", function () {
      applyTheme();
      toast("theme applied", "ok");
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    wire();
    loadSettings();
    const saved = localStorage.getItem("zuzz_theme");
    if (saved) $("theme-sel").value = saved;
  });
})(window);
