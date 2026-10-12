/** Agents page — Multi-Agent Orchestrator & Mirror Neurons UI. */

const API = '/api';

async function apiFetch(path, options = {}) {
    const res = await fetch(API + path, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
}

// --- Deploy Team Wizard ---
async function deployTeam() {
    const prompt = document.getElementById('team-prompt').value.trim();
    const contextText = document.getElementById('team-context').value.trim();
    const resultDiv = document.getElementById('deploy-result');
    const btn = document.getElementById('deploy-team-btn');

    if (!prompt) { alert('Enter a task description'); return; }

    let context = {};
    if (contextText) {
        try { context = JSON.parse(contextText); }
        catch { alert('Invalid JSON in context'); return; }
    }

    btn.disabled = true;
    btn.textContent = 'Deploying…';
    resultDiv.classList.add('hidden');
    resultDiv.textContent = '';

    try {
        const result = await apiFetch('/agents/deploy-team', {
            method: 'POST',
            body: JSON.stringify({ prompt, context }),
        });
        resultDiv.classList.remove('hidden');
        resultDiv.innerHTML = `<h4>Result</h4><pre>${JSON.stringify(result, null, 2)}</pre>`;
    } catch (e) {
        resultDiv.classList.remove('hidden');
        resultDiv.innerHTML = `<h4 style="color:red">Error</h4><pre>${e.message}</pre>`;
    } finally {
        btn.disabled = false;
        btn.textContent = 'Deploy Team';
    }
}

// --- Load Available Roles ---
async function loadRoles() {
    try {
        const { roles } = await apiFetch('/agents/available-roles');
        const grid = document.getElementById('roles-grid');
        grid.innerHTML = roles.map(role => `
            <div class="role-card" data-role="${role}">
                <h4>${role.charAt(0).toUpperCase() + role.slice(1)}</h4>
                <p>${getRoleDescription(role)}</p>
                <button class="btn secondary test-role-btn" data-role="${role}">Test This Role</button>
            </div>
        `).join('');
        // Attach test buttons
        grid.querySelectorAll('.test-role-btn').forEach(btn => {
            btn.addEventListener('click', () => testRole(btn.dataset.role));
        });
    } catch (e) {
        console.error('Failed to load roles:', e);
    }
}

function getRoleDescription(role) {
    const desc = {
        researcher: 'Web research & information retrieval',
        writer: 'Content drafting & prose creation',
        analyst: 'Data analysis & evaluation',
        planner: 'Strategic roadmapping & task planning',
        editor: 'Text refinement, proofreading & grammar',
        translator: 'Language translation',
        summarizer: 'Document & conversation summarization',
        fact_checker: 'Fact checking & claim validation',
        brainstormer: 'Idea generation & ideation',
        scheduler: 'Timeline planning & calendar scheduling',
    };
    return desc[role] || 'Specialist agent';
}

async function testRole(role) {
    const task = prompt(`Enter a task for the ${role} agent:`);
    if (!task) return;

    try {
        const result = await apiFetch(`/agents/${role}/task`, {
            method: 'POST',
            body: JSON.stringify({ task }),
        });
        alert(`Result:\n${JSON.stringify(result, null, 2).slice(0, 2000)}`);
    } catch (e) {
        alert(`Error: ${e.message}`);
    }
}

// --- Mirror Neurons: Observation ---
async function startObservation() {
    try {
        const result = await apiFetch('/mirror/observe/start', { method: 'POST' });
        updateObservationUI(true, result.session_id);
        document.getElementById('obs-result').classList.add('hidden');
    } catch (e) {
        alert(`Failed to start observation: ${e.message}`);
    }
}

async function stopObservation() {
    try {
        const result = await apiFetch('/mirror/observe/stop', { method: 'POST' });
        updateObservationUI(false);
        showResult('obs-result', result);
    } catch (e) {
        alert(`Failed to stop observation: ${e.message}`);
    }
}

function updateObservationUI(active, sessionId = null) {
    const indicator = document.getElementById('obs-status-indicator');
    const text = document.getElementById('obs-status-text');
    const startBtn = document.getElementById('obs-start-btn');
    const stopBtn = document.getElementById('obs-stop-btn');

    if (active) {
        indicator.classList.add('active');
        text.textContent = `Observing… (${sessionId})`;
        startBtn.disabled = true;
        stopBtn.disabled = false;
    } else {
        indicator.classList.remove('active');
        text.textContent = 'Not observing';
        startBtn.disabled = false;
        stopBtn.disabled = true;
    }
}

// --- Patterns ---
async function loadPatterns() {
    try {
        const { patterns } = await apiFetch('/mirror/patterns');
        const list = document.getElementById('patterns-list');
        if (!patterns.length) {
            list.innerHTML = '<p class="empty">No patterns detected yet. Start an observation session and perform some repeated actions.</p>';
            return;
        }
        list.innerHTML = patterns.map(p => `
            <div class="pattern-card">
                <div class="pattern-header">
                    <strong>${p.id}</strong>
                    <span class="badge">${p.occurrences} occurrences</span>
                </div>
                <div class="pattern-steps">
                    ${p.steps.map((s, i) => `<div class="step">${i + 1}. ${s.tool} → ${s.target || '(no target)'}</div>`).join('')}
                </div>
                <div class="pattern-meta">
                    Context: ${p.context || 'unknown'} | First: ${new Date(p.first_seen * 1000).toLocaleTimeString()} | Last: ${new Date(p.last_seen * 1000).toLocaleTimeString()}
                </div>
                <button class="btn secondary create-macro-btn" data-pattern-id="${p.id}">Create Macro</button>
            </div>
        `).join('');

        list.querySelectorAll('.create-macro-btn').forEach(btn => {
            btn.addEventListener('click', () => createMacro(btn.dataset.patternId));
        });
    } catch (e) {
        console.error('Failed to load patterns:', e);
    }
}

async function createMacro(patternId) {
    const name = prompt('Enter a name for the macro:');
    if (!name) return;

    try {
        const result = await apiFetch('/mirror/create-macro', {
            method: 'POST',
            body: JSON.stringify({ pattern_id: patternId, name }),
        });
        alert(`Macro created:\n${JSON.stringify(result, null, 2)}`);
    } catch (e) {
        alert(`Failed to create macro: ${e.message}`);
    }
}

async function suggestAutomation() {
    try {
        const result = await apiFetch('/mirror/suggest', { method: 'POST', body: JSON.stringify({ threshold: 5 }) });
        if (result.suggestions?.length) {
            alert(`Automation suggested:\n${JSON.stringify(result.suggestions[0], null, 2)}`);
        } else {
            alert('No patterns meet the threshold for automation suggestions.');
        }
    } catch (e) {
        alert(`Failed to suggest automation: ${e.message}`);
    }
}

// --- Sessions ---
async function loadSessions() {
    try {
        const { sessions } = await apiFetch('/agents/sessions');
        const list = document.getElementById('sessions-list');
        if (!sessions.length) {
            list.innerHTML = '<p class="empty">No active sessions.</p>';
            return;
        }
        list.innerHTML = sessions.map(s => `
            <div class="session-card">
                <div class="session-header">
                    <strong>${s.session_id}</strong>
                    <span class="badge">${s.status}</span>
                </div>
                <div class="session-meta">
                    Team: ${s.team_name} | Roles: ${s.team_roles.join(', ')} | Subtasks: ${s.subtask_count} | Completed: ${s.completed_count}
                </div>
                <pre>${JSON.stringify(s.context, null, 2)}</pre>
            </div>
        `).join('');
    } catch (e) {
        console.error('Failed to load sessions:', e);
    }
}

// --- Result Display ---
function showResult(elementId, data) {
    const div = document.getElementById(elementId);
    div.classList.remove('hidden');
    div.innerHTML = `<h4>Result</h4><pre>${JSON.stringify(data, null, 2)}</pre>`;
}

// --- Init ---
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('deploy-team-btn').addEventListener('click', deployTeam);
    document.getElementById('obs-start-btn').addEventListener('click', startObservation);
    document.getElementById('obs-stop-btn').addEventListener('click', stopObservation);
    document.getElementById('refresh-patterns-btn').addEventListener('click', loadPatterns);
    document.getElementById('suggest-automation-btn').addEventListener('click', suggestAutomation);
    document.getElementById('refresh-sessions-btn').addEventListener('click', loadSessions);

    loadRoles();
    loadPatterns();
    loadSessions();
});