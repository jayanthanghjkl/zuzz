/* Voice UI contracts: transcription -> real chat -> results and playable audio. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness(fetch) {
  const calls = [];
  const played = [];
  const context = vm.createContext({
    window: {}, document: {addEventListener() {}},
    fetch: async (url, options) => {calls.push({url, options}); return fetch(url, options);},
    AbortController, URL: {createObjectURL: () => 'blob:generated-audio', revokeObjectURL() {}},
    Audio: class {constructor(url) {this.url = url;} async play() {played.push(this.url); setImmediate(() => this.onended());}},
    console,
  });
  vm.runInContext(fs.readFileSync('face/frontend/static/js/voice.js', 'utf8'), context);
  const Voice = vm.runInContext('VoiceInterface', context);
  const voice = Object.create(Voice.prototype);
  voice.voiceSelect = {value: 'server-voice'};
  voice.emotionSelect = {value: 'neutral'};
  voice.speakingStatus = {};
  voice.transcriptionDisplay = {};
  voice.micStatus = {};
  voice.history = [];
  voice.errors = [];
  voice.addToHistory = (who, text) => voice.history.push({who, text});
  voice.showError = message => voice.errors.push(message);
  return {voice, calls, played};
}

const response = data => ({ok: true, json: async () => data});

test('recognized words are sent to chat and actual action outcomes are displayed', async () => {
  const h = harness(async url => {
    assert.equal(url, '/api/chat');
    return response({response: 'Creating a note.', actions: {summary: '1 action executed',
      executed: [{tool: 'file_write', result: {bytes: 12}}]}});
  });
  const spoken = [];
  h.voice.speak = async text => spoken.push(text);
  await h.voice.processText('create a note');
  assert.equal(JSON.parse(h.calls[0].options.body).message, 'create a note');
  assert.equal(h.voice.transcriptionDisplay.textContent, 'create a note');
  assert.equal(h.voice.history.find(item => item.who === 'file_write').text, '{"bytes":12}');
  assert.equal(spoken.length, 1);
  assert.equal(h.voice.busy, false);
});

test('voice never speaks a success when the chat backend returned 503', async () => {
  const h = harness(async () => ({ok: false, json: async () => ({detail: 'No model configured'})}));
  let spoken = false;
  h.voice.speak = async () => {spoken = true;};
  await h.voice.processText('open notes');
  assert.deepEqual(h.voice.errors, ['No model configured']);
  assert.equal(spoken, false);
});

test('server TTS response is actually played and status settles after playback', async () => {
  const h = harness(async () => ({ok: true, blob: async () => 'encoded-audio'}));
  await h.voice.speak('Hello');
  assert.equal(h.calls[0].url, '/api/voice/speak');
  assert.deepEqual(h.played, ['blob:generated-audio']);
  assert.equal(h.voice.speakingStatus.textContent, 'Speaking: Off');
});

test('busy recognition does not submit a duplicate final transcript', async () => {
  const h = harness(async () => {throw new Error('must not be called');});
  h.voice.busy = true;
  await h.voice.processText('duplicate transcript');
  assert.equal(h.calls.length, 0);
});

test('kill-switch polling uses the real endpoint and stops browser audio', async () => {
  const h = harness(async url => {assert.equal(url, '/api/kill-switch'); return response({engaged: true});});
  let stopped = false, paused = false, aborted = false;
  h.voice.stopListening = () => {stopped = true;};
  h.voice.audio = {pause() {paused = true;}};
  h.voice.abort = {abort() {aborted = true;}};
  await h.voice.checkStop();
  assert.equal(h.voice.stopped, true);
  assert.ok(stopped && paused && aborted);
});
