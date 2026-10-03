/* WebSocket transport for the ZUZZ chat UI.
   Reconnects with backoff and reports connection state to the chat module. */
(function (global) {
  "use strict";

  const MAX_BACKOFF_MS = 15000;
  const BASE_BACKOFF_MS = 1000;

  function wsUrl() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    return `${scheme}://${location.host}/ws`;
  }

  class BrainSocket {
    constructor(handlers) {
      this.handlers = handlers || {};
      this.socket = null;
      this.backoff = BASE_BACKOFF_MS;
      this.closedByUser = false;
    }

    connect() {
      this.closedByUser = false;
      this.socket = new WebSocket(wsUrl());

      this.socket.onopen = () => {
        this.backoff = BASE_BACKOFF_MS;
        this.emit("status", { state: "open" });
      };

      this.socket.onmessage = (event) => {
        let payload;
        try {
          payload = JSON.parse(event.data);
        } catch (err) {
          return;
        }
        this.emit("event", payload);
      };

      this.socket.onclose = () => {
        this.emit("status", { state: "closed" });
        if (!this.closedByUser) {
          setTimeout(() => this.connect(), this.backoff);
          this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF_MS);
        }
      };

      this.socket.onerror = () => {
        this.emit("status", { state: "error" });
      };
    }

    send(message, level) {
      if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
        return false;
      }
      this.socket.send(JSON.stringify({ message: message, level: level || null }));
      return true;
    }

    isOpen() {
      return Boolean(this.socket && this.socket.readyState === WebSocket.OPEN);
    }

    close() {
      this.closedByUser = true;
      if (this.socket) {
        this.socket.close();
      }
    }

    emit(name, data) {
      const handler = this.handlers[name];
      if (typeof handler === "function") {
        handler(data);
      }
    }
  }

  global.BrainSocket = BrainSocket;
})(window);
