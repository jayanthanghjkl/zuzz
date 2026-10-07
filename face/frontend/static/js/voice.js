// ZUZZ Voice Interface JavaScript

class VoiceInterface {
    constructor() {
        this.ws = null;
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.isListening = false;
        this.isSpeaking = false;
        this.wakeWordEnabled = false;
        this.currentSessionId = null;
        this.volumeAnimationFrame = null;
        this.audioContext = null;
        this.analyser = null;

        this.initializeElements();
        this.bindEvents();
        this.loadVoices();
        this.connectWebSocket();
    }

    initializeElements() {
        this.micButton = document.getElementById('micButton');
        this.micStatus = document.getElementById('micStatus');
        this.volumeBar = document.getElementById('volumeBar');
        this.volumeStatus = document.getElementById('volumeStatus');
        this.wakeWordToggle = document.getElementById('wakeWordToggle');
        this.wakeWordStatusText = document.getElementById('wakeWordStatusText');
        this.voiceSelect = document.getElementById('voiceSelect');
        this.voiceStatus = document.getElementById('voiceStatus');
        this.emotionSelect = document.getElementById('emotionSelect');
        this.transcriptionDisplay = document.getElementById('transcriptionDisplay');
        this.partialTranscription = document.getElementById('partialTranscription');
        this.historyList = document.getElementById('historyList');
        this.wakeWordStatus = document.getElementById('wakeWordStatus');
        this.listeningStatus = document.getElementById('listeningStatus');
        this.speakingStatus = document.getElementById('speakingStatus');
    }

    bindEvents() {
        this.micButton.addEventListener('click', () => this.toggleListening());
        this.wakeWordToggle.addEventListener('change', () => this.toggleWakeWord());
        this.voiceSelect.addEventListener('change', () => this.setVoice());
        this.emotionSelect.addEventListener('change', () => this.setEmotion());
    }

    async connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/voice/stream`;

        try {
            this.ws = new WebSocket(wsUrl);
            this.ws.binaryType = 'arraybuffer';

            this.ws.onopen = () => {
                console.log('Voice WebSocket connected');
                this.updateStatus('connected');
            };

            this.ws.onmessage = (event) => {
                if (typeof event.data === 'string') {
                    this.handleMessage(JSON.parse(event.data));
                } else {
                    this.handleAudioData(event.data);
                }
            };

            this.ws.onclose = () => {
                console.log('Voice WebSocket disconnected');
                setTimeout(() => this.connectWebSocket(), 5000);
            };

            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };
        } catch (error) {
            console.error('Failed to connect WebSocket:', error);
        }
    }

    handleMessage(data) {
        switch (data.type) {
            case 'transcription':
                this.updateTranscription(data.text, data.is_final);
                break;
            case 'status':
                this.updateSystemStatus(data);
                break;
            case 'voices':
                this.populateVoices(data.voices);
                break;
            case 'history':
                this.addToHistory(data);
                break;
            case 'error':
                this.showError(data.message);
                break;
        }
    }

    handleAudioData(audioData) {
        // Play received audio
        this.playAudio(audioData);
    }

    async toggleListening() {
        if (this.isListening) {
            await this.stopListening();
        } else {
            await this.startListening();
        }
    }

    async startListening() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            this.setupAudioAnalysis(stream);

            this.mediaRecorder = new MediaRecorder(stream, {
                mimeType: 'audio/webm;codecs=opus'
            });

            this.mediaRecorder.ondataavailable = (e) => {
                if (e.data.size > 0) {
                    this.audioChunks.push(e.data);
                    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                        this.ws.send(e.data);
                    }
                }
            };

            this.mediaRecorder.onstop = async () => {
                const blob = new Blob(this.audioChunks, { type: 'audio/webm' });
                this.audioChunks = [];
                await this.finalizeTranscription(blob);
            };

            this.mediaRecorder.start(100);
            this.isListening = true;
            this.micButton.classList.add('listening');
            this.micStatus.textContent = 'Listening... Click to stop';
            this.listeningStatus.textContent = 'Listening: On';
            this.listeningStatus.className = 'status-badge listening';

            // Notify server
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({
                    type: 'start_listening',
                    language: 'en',
                    vad_enabled: true
                }));
            }

            this.startVolumeAnimation();
        } catch (error) {
            console.error('Failed to start listening:', error);
            this.showError('Microphone access denied');
        }
    }

    async stopListening() {
        if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
            this.mediaRecorder.stop();
        }

        this.mediaRecorder?.stream?.getTracks().forEach(track => track.stop());
        this.mediaRecorder = null;

        this.isListening = false;
        this.micButton.classList.remove('listening');
        this.micStatus.textContent = 'Click to start listening';
        this.listeningStatus.textContent = 'Listening: Off';
        this.listeningStatus.className = 'status-badge inactive';

        this.stopVolumeAnimation();
        this.volumeBar.style.width = '0%';

        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'stop_listening' }));
        }
    }

    setupAudioAnalysis(stream) {
        this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
        const source = this.audioContext.createMediaStreamSource(stream);
        this.analyser = this.audioContext.createAnalyser();
        this.analyser.fftSize = 256;
        source.connect(this.analyser);
    }

    startVolumeAnimation() {
        const animate = () => {
            if (!this.isListening || !this.analyser) return;

            const dataArray = new Uint8Array(this.analyser.frequencyBinCount);
            this.analyser.getByteFrequencyData(dataArray);

            const average = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
            const percentage = Math.min(100, (average / 128) * 100);

            this.volumeBar.style.width = `${percentage}%`;

            if (percentage > 50) {
                this.volumeStatus.textContent = 'Good level';
                this.volumeBar.style.background = 'linear-gradient(90deg, #4caf50, #00d9ff)';
            } else if (percentage > 20) {
                this.volumeStatus.textContent = 'Low level';
                this.volumeBar.style.background = 'linear-gradient(90deg, #ff9800, #ffc107)';
            } else {
                this.volumeStatus.textContent = 'Very quiet';
                this.volumeBar.style.background = 'linear-gradient(90deg, #f44336, #ff5252)';
            }

            this.volumeAnimationFrame = requestAnimationFrame(animate);
        };
        animate();
    }

    stopVolumeAnimation() {
        if (this.volumeAnimationFrame) {
            cancelAnimationFrame(this.volumeAnimationFrame);
            this.volumeAnimationFrame = null;
        }
        if (this.audioContext) {
            this.audioContext.close();
            this.audioContext = null;
        }
    }

    async finalizeTranscription(blob) {
        try {
            const formData = new FormData();
            formData.append('file', blob, 'recording.webm');

            const response = await fetch('/api/voice/transcribe', {
                method: 'POST',
                body: formData
            });

            const result = await response.json();
            if (result.text) {
                this.updateTranscription(result.text, true);
                this.addToHistory({
                    type: 'user',
                    text: result.text,
                    timestamp: new Date().toISOString()
                });
            }
        } catch (error) {
            console.error('Transcription failed:', error);
        }
    }

    updateTranscription(text, isFinal) {
        if (isFinal) {
            this.transcriptionDisplay.innerHTML = `<p>${this.escapeHtml(text)}</p>`;
            this.partialTranscription.textContent = '';
        } else {
            this.partialTranscription.textContent = text;
        }
    }

    async toggleWakeWord() {
        this.wakeWordEnabled = this.wakeWordToggle.checked;

        try {
            const response = await fetch('/api/voice/wake-word/' + (this.wakeWordEnabled ? 'enable' : 'disable'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ keywords: ['hey zuzz', 'okay zuzz'] })
            });

            const result = await response.json();
            this.updateWakeWordStatus(this.wakeWordEnabled);
        } catch (error) {
            console.error('Wake word toggle failed:', error);
            this.wakeWordToggle.checked = !this.wakeWordEnabled;
        }
    }

    updateWakeWordStatus(enabled) {
        this.wakeWordStatus.textContent = `Wake Word: ${enabled ? 'On' : 'Off'}`;
        this.wakeWordStatus.className = `status-badge ${enabled ? 'active' : 'inactive'}`;
        this.wakeWordStatusText.textContent = enabled ? 'Wake word enabled' : 'Wake word disabled';
    }

    async loadVoices() {
        try {
            const response = await fetch('/api/voice/available-voices');
            const data = await response.json();
            this.populateVoices(data.voices);
        } catch (error) {
            console.error('Failed to load voices:', error);
        }
    }

    populateVoices(voices) {
        this.voiceSelect.innerHTML = '<option value="">Select a voice...</option>';
        voices.forEach(voice => {
            const option = document.createElement('option');
            option.value = voice.id;
            option.textContent = `${voice.name} (${voice.language}, ${voice.gender}) - ${voice.engine}`;
            if (voice.current) option.selected = true;
            this.voiceSelect.appendChild(option);
        });
    }

    async setVoice() {
        const voiceId = this.voiceSelect.value;
        if (!voiceId) return;

        try {
            const response = await fetch('/api/voice/set-voice', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ voice_id: voiceId })
            });

            const result = await response.json();
            this.voiceStatus.textContent = result.success ? `Voice set to ${voiceId}` : 'Failed to set voice';
        } catch (error) {
            console.error('Set voice failed:', error);
        }
    }

    async setEmotion() {
        const emotion = this.emotionSelect.value;
        // Emotion is applied on next speak
        console.log('Emotion set to:', emotion);
    }

    async speak(text) {
        if (!text) return;

        this.isSpeaking = true;
        this.speakingStatus.textContent = 'Speaking: On';
        this.speakingStatus.className = 'status-badge active';

        try {
            const response = await fetch('/api/voice/speak', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text,
                    emotion: this.emotionSelect.value,
                    voice_id: this.voiceSelect.value || undefined
                })
            });

            const result = await response.json();
            this.addToHistory({
                type: 'assistant',
                text,
                emotion: this.emotionSelect.value,
                timestamp: new Date().toISOString()
            });
        } catch (error) {
            console.error('Speak failed:', error);
        } finally {
            this.isSpeaking = false;
            this.speakingStatus.textContent = 'Speaking: Off';
            this.speakingStatus.className = 'status-badge inactive';
        }
    }

    async interruptSpeaking() {
        try {
            await fetch('/api/voice/interrupt', { method: 'POST' });
            this.isSpeaking = false;
            this.speakingStatus.textContent = 'Speaking: Off';
            this.speakingStatus.className = 'status-badge inactive';
        } catch (error) {
            console.error('Interrupt failed:', error);
        }
    }

    playAudio(audioData) {
        const blob = new Blob([audioData], { type: 'audio/wav' });
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audio.play().catch(e => console.error('Audio play failed:', e));
    }

    updateSystemStatus(status) {
        if (status.listening) {
            this.updateWakeWordStatus(status.listening.wake_word_active);
        }
        if (status.speaking) {
            this.isSpeaking = status.speaking.speaking;
            this.speakingStatus.textContent = `Speaking: ${this.isSpeaking ? 'On' : 'Off'}`;
            this.speakingStatus.className = `status-badge ${this.isSpeaking ? 'active' : 'inactive'}`;
        }
    }

    addToHistory(item) {
        if (this.historyList.querySelector('.history-empty')) {
            this.historyList.innerHTML = '';
        }

        const li = document.createElement('li');
        li.className = 'history-item';

        const icon = document.createElement('div');
        icon.className = `history-icon ${item.type}`;
        icon.innerHTML = item.type === 'user'
            ? '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/></svg>'
            : '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H6l-2 2V4h16v12z"/></svg>';

        const content = document.createElement('div');
        content.className = 'history-content';

        const text = document.createElement('div');
        text.className = 'history-text';
        text.textContent = item.text;

        const time = document.createElement('div');
        time.className = 'history-time';
        time.textContent = new Date(item.timestamp).toLocaleTimeString();

        content.appendChild(text);
        content.appendChild(time);

        if (item.emotion) {
            const emotion = document.createElement('span');
            emotion.className = 'history-emotion';
            emotion.textContent = item.emotion;
            content.appendChild(emotion);
        }

        li.appendChild(icon);
        li.appendChild(content);
        this.historyList.insertBefore(li, this.historyList.firstChild);
    }

    updateStatus(status) {
        console.log('Status:', status);
    }

    showError(message) {
        this.micStatus.textContent = `Error: ${message}`;
        this.micStatus.style.color = '#f44336';
        setTimeout(() => {
            this.micStatus.style.color = '';
            this.micStatus.textContent = this.isListening ? 'Listening... Click to stop' : 'Click to start listening';
        }, 5000);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.voiceInterface = new VoiceInterface();
});

// Global functions for inline handlers
window.toggleListening = () => window.voiceInterface?.toggleListening();
window.speak = (text) => window.voiceInterface?.speak(text);
window.interruptSpeaking = () => window.voiceInterface?.interruptSpeaking();