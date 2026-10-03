/* Chat rendering for the ZUZZ web UI.
   Owns the message log, streaming bubble updates and small markdown touches. */
(function (global) {
  "use strict";

  function escapeHtml(text) {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function renderMarkdown(text) {
    const blocks = [];
    const withBlocks = text.replace(/```([\s\S]*?)```/g, (match, code) => {
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

      const meta = document.createElement("div");
      meta.className = "meta";
      wrapper.appendChild(meta);

      this.element.appendChild(wrapper);
      this.scrollToBottom();
      return { wrapper: wrapper, bubble: bubble, meta: meta };
    }

    appendText(node, text) {
      const current = node.bubble.dataset.raw || "";
      const next = current + text;
      node.bubble.dataset.raw = next;
      node.bubble.innerHTML = renderMarkdown(next);
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

    clear() {
      this.element.innerHTML = "";
    }

    scrollToBottom() {
      this.element.scrollTop = this.element.scrollHeight;
    }
  }

  global.ChatLog = ChatLog;
  global.renderMarkdown = renderMarkdown;
})(window);
