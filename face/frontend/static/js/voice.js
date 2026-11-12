/* Push-to-talk: browser recognition when supported, otherwise real recording upload.
   Speech may use browser voices (no server TTS install) or configured server TTS. */
class VoiceInterface {
    constructor() {
        for (const id of ['micButton', 'micStatus', 'volumeBar', 'volumeStatus', 'wakeWordToggle',
            'wakeWordStatusText', 'voiceSelect', 'voiceStatus', 'emotionSelect', 'transcriptionDisplay',
            'partialTranscription', 'historyList', 'wakeWordStatus', 'listeningStatus', 'speakingStatus']) {
            this[id] = document.getElementById(id);
        }
        this.isListening = false;
        this.busy = false;
        this.stopped = false;
        this.nativeVoices = [];
        this.micButton.onclick = () => this.toggleListening();
        this.wakeWordToggle.disabled = true;
        this.wakeWordStatusText.textContent = 'Use push-to-talk. Custom wake word requires additional server configuration.';
        this.voiceSelect.onchange = () => this.voiceStatus.textContent = 'Selected voice applies to the next reply';
        this.loadVoices();
        if (window.speechSynthesis) window.speechSynthesis.addEventListener('voiceschanged', () => this.loadVoices());
        this.checkStop();
        this.stopTimer = setInterval(() => this.checkStop(), 2000);
        window.addEventListener('pagehide', () => { clearInterval(this.stopTimer); this.interruptSpeaking(); this.stopListening(); });
    }

    async loadVoices() {
        const previous = this.voiceSelect.value;
        this.voiceSelect.replaceChildren();
        if (window.speechSynthesis) {
            this.nativeVoices = speechSynthesis.getVoices();
            this.voiceSelect.add(new Option('Browser default voice', 'browser:default'));
            this.nativeVoices.forEach((voice, index) => this.voiceSelect.add(new Option(`${voice.name} (${voice.lang})`, `browser:${index}`)));
        }
        try {
            const response = await fetch('/api/voice/available-voices');
            if (!response.ok) throw new Error('Voice status unavailable');
            const data = await response.json();
            for (const voice of data.voices || []) this.voiceSelect.add(new Option(`${voice.name} — ${voice.engine}`, voice.id));
            if (previous) this.voiceSelect.value = previous;
            if (!this.voiceSelect.value && this.voiceSelect.options.length) this.voiceSelect.selectedIndex = 0;
        } catch (error) { this.showError(error.message); }
    }

    async checkStop() {
        try {
            const response = await fetch('/api/kill-switch', {cache: 'no-store'});
            if (!response.ok) return;
            const status = await response.json();
            this.stopped = Boolean(status.killed || status.engaged);
            if (this.stopped) {
                this.abort?.abort();
                this.recognition?.abort();
                this.stopListening();
                this.audio?.pause();
                window.speechSynthesis?.cancel();
                this.micStatus.textContent = 'ZUZZ is stopped. Reset it from the main chat stop panel.';
            }
        } catch (_) { /* request failures surface when a turn is submitted */ }
    }

    async toggleListening() {
        if (this.isListening) return this.stopListening();
        if (this.busy || this.stopped) return;
        const Recognition = !this.recognitionUnavailable && (window.SpeechRecognition || window.webkitSpeechRecognition);
        try {
            if (Recognition) {
                this.recognition = new Recognition();
                this.recognition.lang = navigator.language || 'en-US';
                this.recognition.interimResults = true;
                this.recognition.onresult = event => {
                    let finalText = '', partial = '';
                    for (let i = event.resultIndex; i < event.results.length; i++) {
                        if (event.results[i].isFinal) finalText += event.results[i][0].transcript;
                        else partial += event.results[i][0].transcript;
                    }
                    this.partialTranscription.textContent = partial;
                    if (finalText.trim()) { this.stopListening(); this.processText(finalText); }
                };
                this.recognition.onerror = event => {
                    if (['network', 'service-not-allowed', 'language-not-supported'].includes(event.error)) this.recognitionUnavailable = true;
                    this.showError(`Speech recognition: ${event.error}. Next attempt uses server recording if the browser speech service is unavailable.`);
                };
                this.recognition.onend = () => this.setListening(false);
                this.recognition.start();
                this.volumeStatus.textContent = 'Browser recognition may use an online speech service';
            } else {
                if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error('Microphone requires HTTPS/localhost and a supported browser');
                this.stream = await navigator.mediaDevices.getUserMedia({audio: true});
                const type = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].find(t => MediaRecorder.isTypeSupported(t));
                if (!type) throw new Error('No supported recording format in this browser');
                this.recorder = new MediaRecorder(this.stream, {mimeType: type});
                const chunks = [];
                this.recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
                this.recorder.onstop = () => {
                    const blob = new Blob(chunks, {type});
                    if (!this.stopped) this.transcribe(blob).catch(error => this.showError(error.message));
                };
                this.recorder.start();
                this.volumeStatus.textContent = 'Recording; server transcription needs Whisper + ffmpeg';
            }
            this.setListening(true);
            this.recordingTimeout = setTimeout(() => this.stopListening(), 60000);
        } catch (error) { this.stream?.getTracks().forEach(track => track.stop()); this.setListening(false); this.showError(error.message); }
    }

    setListening(active) {
        this.isListening = active;
        this.micButton.classList.toggle('listening', active);
        this.listeningStatus.textContent = `Listening: ${active ? 'On' : 'Off'}`;
        this.micStatus.textContent = active ? 'Listening… click to stop' : 'Click to talk';
    }

    stopListening() {
        clearTimeout(this.recordingTimeout);
        this.recognition?.stop();
        if (this.recorder?.state === 'recording') this.recorder.stop();
        this.stream?.getTracks().forEach(track => track.stop());
        this.setListening(false);
    }

    async transcribe(blob, confirmationId = '') {
        this.busy = true;
        try {
            const query = new URLSearchParams({language: (navigator.language || 'en').split('-')[0], confirmation_id: confirmationId});
            const response = await fetch(`/api/voice/transcribe?${query}`, {method: 'POST', headers: {'Content-Type': blob.type}, body: blob});
            const data = await response.json();
            if (response.status === 202) {
                if (window.confirm(`Allow transcription of this recording? ${data.detail}`)) return await this.transcribe(blob, data.confirmation_id);
                return;
            }
            if (!response.ok) throw new Error(data.detail || 'Transcription failed');
            if (!data.text?.trim()) throw new Error('No speech detected; please try again');
            await this.processText(data.text, true);
        } finally { this.busy = false; }
    }

    async processText(text, fromRecording = false) {
        if (this.busy && !fromRecording) return;
        this.busy = true;
        this.transcriptionDisplay.textContent = text;
        this.addToHistory('You', text);
        this.abort = new AbortController();
        try {
            const response = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: text}), signal: this.abort.signal});
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'AI request failed');
            const actions = data.actions || {};
            const answer = [data.response, actions.summary].filter(Boolean).join('\n');
            this.addToHistory('ZUZZ', answer);
            for (const item of [...(actions.blocked || []), ...(actions.errors || []), ...(actions.executed || [])]) this.addToHistory(item.tool, JSON.stringify(item.result ?? item.errors ?? item.reason));
            for (const pending of actions.pending || []) {
                const approval = window.confirm(`Allow ${pending.tool}: ${pending.target}?`);
                const confirmed = await fetch('/api/chat/confirm', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({confirmation_id: pending.confirmation_id, approval})});
                const outcome = await confirmed.json();
                if (!confirmed.ok) throw new Error(outcome.detail || 'Confirmation failed');
                this.addToHistory('Action result', JSON.stringify(outcome));
            }
            await this.speak(answer);
        } catch (error) { this.showError(error.message); }
        finally { this.busy = false; }
    }

    async speak(text) {
        if (!text || this.stopped) return;
        this.speakingStatus.textContent = 'Speaking: On';
        const selected = this.voiceSelect.value;
        try {
            if (selected.startsWith('browser:')) {
                const utterance = new SpeechSynthesisUtterance(text);
                const voice = this.nativeVoices[Number(selected.split(':')[1])];
                if (voice) utterance.voice = voice;
                await new Promise((resolve, reject) => {
                    utterance.onend = resolve;
                    utterance.onerror = event => reject(new Error(`Playback: ${event.error}`));
                    speechSynthesis.speak(utterance);
                });
            } else {
                const response = await fetch('/api/voice/speak', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text, voice_id: selected || undefined, emotion: this.emotionSelect.value})});
                if (!response.ok) throw new Error((await response.json()).detail || 'Speech generation failed');
                const url = URL.createObjectURL(await response.blob());
                try { this.audio = new Audio(url); await this.audio.play(); await new Promise((resolve, reject) => { this.audio.onended = resolve; this.audio.onpause = resolve; this.audio.onerror = () => reject(new Error('The browser could not decode generated speech')); }); }
                finally { URL.revokeObjectURL(url); }
            }
        } finally { this.speakingStatus.textContent = 'Speaking: Off'; }
    }

    interruptSpeaking() {
        this.audio?.pause();
        window.speechSynthesis?.cancel();
        fetch('/api/voice/interrupt', {method: 'POST'}).catch(() => {});
    }

    addToHistory(who, text) {
        this.historyList.querySelector('.history-empty')?.remove();
        const item = document.createElement('li');
        item.className = 'history-item'; item.textContent = `${who}: ${text}`;
        this.historyList.prepend(item);
    }

    showError(message) { this.micStatus.textContent = message; this.addToHistory('Error', message); }
}

document.addEventListener('DOMContentLoaded', () => { window.voiceInterface = new VoiceInterface(); });
window.toggleListening = () => window.voiceInterface?.toggleListening();
window.speak = text => window.voiceInterface?.speak(text);
window.interruptSpeaking = () => window.voiceInterface?.interruptSpeaking();
