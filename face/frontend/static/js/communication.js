/* Communication page */

(function (global) {
  "use strict";

  const $ = (id) => document.getElementById(id);

  async function api(method, path, body) {
    const response = await fetch(path, {
      method: method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try { data = await response.json(); } catch (err) { data = {}; }
    return { status: response.status, data };
  }

  function showResult(id, text) {
    const el = $(id);
    el.textContent = text;
    el.classList.remove("hidden");
  }

  async function loadStatus() {
    const res = await api("GET", "/api/comm/channels/status");
    if (res.status === 200) {
      const data = res.data;
      $("telegram-status").textContent = data.telegram ? "configured" : "not configured";
      $("email-status").textContent = data.email ? "configured" : "not configured";
      $("push-status").textContent = data.push ? "configured" : "not configured";
    }
  }

  async function sendMessage() {
    const channel = $("send-channel").value;
    const message = $("send-message").value.trim();
    if (!message) return;
    const path = channel === "auto" ? "/api/comm/send" : "/api/comm/" + channel + "/send";
    const res = await api("POST", path, { message: message });
    if (res.status === 200) {
      showResult("send-result", "Sent via " + (res.data.channel || channel));
      $("send-message").value = "";
    } else {
      showResult("send-result", "Failed: " + (res.data.detail || res.status));
    }
  }

  async function escalate() {
    const channel = $("escalate-channel").value;
    const message = $("escalate-message").value.trim();
    if (!message) return;
    const res = await api("POST", "/api/comm/escalate", {
      message: message,
      start_channel: channel,
    });
    if (res.status === 200) {
      showResult("escalate-result", "Escalated via " + res.data.channel);
      $("escalate-message").value = "";
    } else {
      showResult("escalate-result", "Failed: " + (res.data.detail || res.status));
    }
  }

  async function savePrefs() {
    const prefs = {
      quiet_hours_start: $("quiet-start").value,
      quiet_hours_end: $("quiet-end").value,
    };
    const res = await api("POST", "/api/comm/preferences", prefs);
    if (res.status === 200) {
      showResult("prefs-result", "Preferences saved");
    } else {
      showResult("prefs-result", "Failed: " + (res.data.detail || res.status));
    }
  }

  $("refresh-status").addEventListener("click", loadStatus);
  $("send-btn").addEventListener("click", sendMessage);
  $("escalate-btn").addEventListener("click", escalate);
  $("save-prefs").addEventListener("click", savePrefs);

  loadStatus();
})(this);
