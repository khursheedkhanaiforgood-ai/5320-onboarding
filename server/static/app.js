/* 5320 Onboarding Agent — Web UI */

const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/ui`;
const MAX_CONSOLE_LINES = 2000;

let ws = null;
let autoscroll = true;
let consoleLines = [];
let currentPromptId = null;

// ── WebSocket ────────────────────────────────────────────────────────────────

function connect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    setLog('Connected to agent server.');
  };

  ws.onclose = () => {
    setLog('Server disconnected. Reconnecting in 3s...');
    setTimeout(connect, 3000);
  };

  ws.onerror = () => {
    setLog('WebSocket error.');
  };

  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    handleMessage(msg);
  };
}

function handleMessage(msg) {
  switch (msg.type) {

    case 'bridge_status':
      const badge = document.getElementById('bridge-badge');
      if (msg.status === 'connected') {
        badge.textContent = '● Bridge Connected';
        badge.classList.add('connected');
        document.getElementById('waiting-msg').style.display = 'none';
      } else {
        badge.textContent = '● Bridge Disconnected';
        badge.classList.remove('connected');
        document.getElementById('waiting-msg').style.display = 'block';
        document.getElementById('instruction-card').style.display = 'none';
      }
      break;

    case 'bridge_hello':
      setLog(`Bridge connected from ${msg.bridge_id || 'unknown'} — port ${msg.port || '?'}`);
      break;

    case 'console_line':
      appendConsoleLine(msg.line);
      break;

    case 'state_update':
      updateStateBadge(msg.state, msg.os);
      break;

    case 'instruction':
      showInstruction(msg);
      break;

    case 'prompt':
      showPrompt(msg);
      break;

    case 'xiq_status':
      setLog(msg.connected
        ? '✓ Switch is CONNECTED to ExtremeCloud IQ'
        : '✗ Switch is NOT confirmed on ExtremeCloud IQ');
      break;

    case 'report_ready':
      showReportDownload(msg.html, msg.filename);
      break;

    case 'session_complete':
      setLog('Session complete.');
      break;

    case 'log':
      setLog(msg.text);
      break;
  }
}

// ── Console output ───────────────────────────────────────────────────────────

function appendConsoleLine(line) {
  consoleLines.push(line);
  if (consoleLines.length > MAX_CONSOLE_LINES) {
    consoleLines.shift();
  }
  const el = document.getElementById('console-out');
  el.textContent = consoleLines.join('\n');
  if (autoscroll) {
    el.scrollTop = el.scrollHeight;
  }
}

function toggleAutoscroll() {
  autoscroll = !autoscroll;
  document.getElementById('btn-autoscroll').textContent =
    `Auto-scroll ${autoscroll ? 'ON' : 'OFF'}`;
}

// ── State badge ──────────────────────────────────────────────────────────────

const STATE_CLASS = {
  EXOS_LOGGED_IN: 'exos',
  EXOS_BOOT: 'exos',
  EXOS_SETUP_WIZARD: 'exos',
  ONBOARDED: 'onboarded',
  EXOS_SAVE_CONFIG: 'onboarded',
  FE_LOGIN_BLOCKED: 'error',
};

function updateStateBadge(state, os) {
  const el = document.getElementById('state-badge');
  el.textContent = state.replace(/_/g, ' ');
  el.className = 'state-badge ' + (STATE_CLASS[state] || (os === 'EXOS' ? 'exos' : 'active'));
}

// ── Instruction card ─────────────────────────────────────────────────────────

function showInstruction(msg) {
  document.getElementById('waiting-msg').style.display = 'none';
  const card = document.getElementById('instruction-card');
  card.style.display = 'flex';

  document.getElementById('instr-action').textContent = msg.action || '';

  const cmdWrap = document.getElementById('instr-command-wrap');
  if (msg.command) {
    document.getElementById('instr-command').textContent = msg.command;
    cmdWrap.style.display = 'flex';
  } else {
    cmdWrap.style.display = 'none';
  }

  document.getElementById('instr-explanation').textContent = msg.explanation || '';

  const tags = document.getElementById('instr-tags');
  tags.innerHTML = '';
  if (msg.wait) tags.innerHTML += '<span class="tag wait">Wait</span>';
  if (msg.physical) tags.innerHTML += '<span class="tag physical">Physical Action</span>';
}

function copyCommand() {
  const cmd = document.getElementById('instr-command').textContent;
  navigator.clipboard.writeText(cmd).catch(() => {});
}

// ── Prompt ───────────────────────────────────────────────────────────────────

function showPrompt(msg) {
  currentPromptId = msg.prompt_id;
  document.getElementById('prompt-text').textContent = msg.text;
  const opts = document.getElementById('prompt-options');
  opts.innerHTML = '';
  (msg.options || ['yes', 'no']).forEach(opt => {
    const btn = document.createElement('button');
    btn.textContent = opt;
    btn.onclick = () => respondPrompt(opt);
    opts.appendChild(btn);
  });
  document.getElementById('prompt-area').style.display = 'block';
}

function respondPrompt(value) {
  if (!currentPromptId) return;
  ws.send(JSON.stringify({ type: 'prompt_response', prompt_id: currentPromptId, value }));
  document.getElementById('prompt-area').style.display = 'none';
  currentPromptId = null;
}

// ── Report download ──────────────────────────────────────────────────────────

function showReportDownload(html, filename) {
  const blob = new Blob([html], { type: 'text/html' });
  const url = URL.createObjectURL(blob);
  const link = document.getElementById('report-link');
  link.href = url;
  link.download = filename;
  link.textContent = `Download ${filename}`;
  document.getElementById('report-overlay').style.display = 'flex';
}

// ── Manual command input ─────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('manual-cmd');
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const cmd = input.value.trim();
      if (cmd && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'manual_command', command: cmd }));
        input.value = '';
      }
    }
  });
  connect();
});

// ── Log strip ────────────────────────────────────────────────────────────────

function setLog(text) {
  document.getElementById('log-strip').textContent = text;
}
