/* Contract tests for the actual browser application wiring, no DOM library needed. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function appHarness() {
  const elements = new Map();
  function element() {
    return {value: '', disabled: false, textContent: '', style: {}, scrollHeight: 20,
      classList: {toggle() {}}, handlers: {},
      addEventListener(name, callback) {this.handlers[name] = callback;}};
  }
  const log = {users: [], errors: [], prose: [], addUser(text) {this.users.push(text);},
    addBot() {return {};}, addError(text) {this.errors.push(text);},
    setProse(node, text) {this.prose.push(text);}, appendText() {}, finish() {},
    showThinkingIndicator() {}, addActions() {}};
  let socket;
  const context = {
    document: {getElementById(id) {if (!elements.has(id)) elements.set(id, element()); return elements.get(id);}},
    ChatLog: function() {return log;},
    BrainSocket: function(handlers) {socket = this; this.handlers = handlers; this.open = true;
      this.messages = []; this.isOpen = () => this.open; this.connect = () => {};
      this.send = message => {this.messages.push(message); return true;};},
    KillSwitchControl: function() {this.start = () => {};},
    EnhancementEvents: {}, performance: {now: () => 10}, setInterval() {},
    fetch: async () => ({ok: true, json: async () => ({providers: []})})
  };
  vm.runInNewContext(fs.readFileSync('face/frontend/static/js/app.js', 'utf8'), context);
  return {elements, log, socket, send(message) {
    elements.get('input').value = message;
    elements.get('send-btn').handlers.click();
  }};
}

test('disconnected send retains the draft and does not create an unsent turn', () => {
  const h = appHarness(); h.socket.open = false;
  h.send('do the task');
  assert.equal(h.elements.get('input').value, 'do the task');
  assert.equal(h.log.users.length, 0);
  assert.equal(h.socket.messages.length, 0);
});

test('Enter cannot submit another turn while actions are still pending processing', () => {
  const h = appHarness(); h.send('first'); h.send('second');
  assert.deepEqual(h.socket.messages, ['first']);
  h.socket.handlers.event({type: 'actions', summary: 'executed'});
  assert.equal(h.elements.get('send-btn').disabled, true);
  h.socket.handlers.event({type: 'done', level: 3, prose: 'Final prose'});
  assert.equal(h.elements.get('send-btn').disabled, false);
  assert.deepEqual(h.log.prose, ['Final prose']);
});

test('a backend failure remains visible even after partial tokens', () => {
  const h = appHarness(); h.send('task');
  h.socket.handlers.event({type: 'token', content: 'partial'});
  h.socket.handlers.event({type: 'error', content: 'Ollama not reachable'});
  assert.deepEqual(h.log.errors, ['Ollama not reachable']);
  assert.equal(h.elements.get('send-btn').disabled, false);
});

test('connection loss clears busy state without replaying an action', () => {
  const h = appHarness(); h.send('write file');
  h.socket.handlers.status({state: 'closed'});
  assert.equal(h.elements.get('send-btn').disabled, false);
  assert.equal(h.log.errors.length, 1);
  assert.deepEqual(h.socket.messages, ['write file']);
});

test('service worker does not intercept live API state', () => {
  let handler;
  const context = {URL, caches: {}, self: {location: {origin: 'https://example.test'},
    addEventListener(name, fn) {if (name === 'fetch') handler = fn;}}};
  vm.runInNewContext(fs.readFileSync('face/frontend/sw.js', 'utf8'), context);
  let intercepted = false;
  handler({request: {url: 'https://example.test/api/kill-switch', method: 'GET'},
    respondWith() {intercepted = true;}});
  assert.equal(intercepted, false);
});
