/* ============================================================
   Realtime STT Comparison — Frontend App
   ============================================================ */

// ───── DOM helpers ─────
const $ = (id) => document.getElementById(id);

const els = {
  btnToggle:       $('btn-toggle'),
  openaiKey:       $('openai-key'),
  openaiModel:     $('openai-model'),
  openaiStatus:    $('openai-status'),
  openaiLatency:   $('openai-latency'),
  openaiStreaming:  $('openai-streaming'),
  openaiAccumulated: $('openai-accumulated'),
  openaiLog:       $('openai-log'),

  googleKey:         $('google-key'),
  googleStatus:      $('google-status'),
  googleStatusBadge: $('google-status-badge'),
  googleModel:       $('google-model'),
  googleLatency:     $('google-latency'),
  googleStreaming:    $('google-streaming'),
  googleAccumulated: $('google-accumulated'),
  googleLog:         $('google-log'),

  audioMeter:      $('audio-meter'),
  micSelect:       $('mic-select'),
};

// ───── State ─────
let isRecording = false;
let micStream   = null;

// OpenAI
let openaiWS          = null;
let openaiAudioCtx    = null;
let openaiWorklet     = null;
let openaiTurnDeltas  = '';
let openaiAccText     = '';
let openaiSpeechStart = null;
let hasBackendOpenAIKey = false;

// Google
let googleWS           = null;
let googleAudioCtx     = null;
let googleWorklet      = null;
let googleCurrentInter = '';
let googleAccText      = '';
let googleSpeechStart  = null;
let hasBackendGoogleKey = false;

let googleAvailable = false;

// Audio meter
let meterAnimFrame = null;
let meterAnalyser  = null;
let meterCtx2d     = null;
let meterAudioCtx  = null;

// ═══════════════════════════════════════════
// Init
// ═══════════════════════════════════════════
async function init() {
  // Check OpenAI config from server
  try {
    const res = await fetch('/api/config');
    const data = await res.json();
    if (data.hasKey) {
      hasBackendOpenAIKey = true;
      els.openaiKey.placeholder = `已載入專案金鑰 (${data.apiKey})`;
      logEvent('openai', 'info', `已自動載入專案的 OpenAI API key`);
    }
  } catch (err) {
    logEvent('openai', 'info', '無法從伺服器讀取金鑰資訊');
  }

  // Check Google availability
  try {
    const res  = await fetch('/api/google-status');
    const data = await res.json();
    googleAvailable = data.available;
    if (data.hasKey) {
      hasBackendGoogleKey = true;
      els.googleKey.placeholder = `已載入專案金鑰 (${data.keyPrefix})`;
      logEvent('google', 'info', `已自動載入專案的 Google API key`);
    }
    els.googleStatus.textContent = data.available ? '✅ 已設定' : '❌ 未設定';
    els.googleStatus.className   = `status-tag ${data.available ? 'available' : 'unavailable'}`;
    if (!data.available && data.error) {
      logEvent('google', 'info', `未設定: ${data.error}`);
    }
  } catch {
    els.googleStatus.textContent = '❌ 無法連線';
    els.googleStatus.className   = 'status-tag unavailable';
  }

  // Restore saved key
  const saved = localStorage.getItem('stt-openai-key');
  if (saved) els.openaiKey.value = saved;

  const savedGoogle = localStorage.getItem('stt-google-key');
  if (savedGoogle) els.googleKey.value = savedGoogle;

  // Restore saved models
  const savedOpenAIModel = localStorage.getItem('stt-openai-model');
  if (savedOpenAIModel) els.openaiModel.value = savedOpenAIModel;

  const savedGoogleModel = localStorage.getItem('stt-google-model');
  if (savedGoogleModel) els.googleModel.value = savedGoogleModel;

  refreshButton();

  els.openaiKey.addEventListener('input', () => {
    localStorage.setItem('stt-openai-key', els.openaiKey.value);
    refreshButton();
  });

  els.googleKey.addEventListener('input', () => {
    localStorage.setItem('stt-google-key', els.googleKey.value);
    refreshButton();
  });

  els.openaiModel.addEventListener('change', () => {
    localStorage.setItem('stt-openai-model', els.openaiModel.value);
  });

  els.googleModel.addEventListener('change', () => {
    localStorage.setItem('stt-google-model', els.googleModel.value);
  });

  els.btnToggle.addEventListener('click', toggleRecording);

  // Clear-log buttons
  document.querySelectorAll('.btn-clear').forEach(btn => {
    btn.addEventListener('click', () => {
      const t = $(btn.dataset.target);
      if (t) t.innerHTML = '';
    });
  });

  // Microphone selection binding
  els.micSelect.addEventListener('change', () => {
    localStorage.setItem('stt-mic-device-id', els.micSelect.value);
    logEvent('openai', 'info', `已切換麥克風：${els.micSelect.options[els.micSelect.selectedIndex].text}`);
  });

  try {
    navigator.mediaDevices.addEventListener('devicechange', loadMics);
  } catch (e) {
    console.warn('不支援 devicechange 事件監聽', e);
  }

  await loadMics();

  // 預設網頁一開啟就自動啟動錄音
  if (!isRecording) {
    logEvent('openai', 'info', '網頁載入完成，預設啟動錄音...');
    try {
      await startRecording();
    } catch (e) {
      console.error('預設自動錄音失敗：', e);
    }
  }
}

function refreshButton() {
  const hasOpenAI = els.openaiKey.value.trim().length > 0 || hasBackendOpenAIKey;
  const hasGoogle = els.googleKey.value.trim().length > 0 || hasBackendGoogleKey || googleAvailable;
  els.btnToggle.disabled = !(hasOpenAI || hasGoogle);
}

// ═══════════════════════════════════════════
// Microphone Enumeration
// ═══════════════════════════════════════════
async function loadMics() {
  try {
    // 嘗試請求一次以取得標籤
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach(t => t.stop());
    } catch (e) {
      console.warn('麥克風權限請求失敗或被拒絕：', e);
    }

    const devices = await navigator.mediaDevices.enumerateDevices();
    const audioDevices = devices.filter(d => d.kind === 'audioinput');
    
    els.micSelect.innerHTML = '';
    
    if (audioDevices.length === 0) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = '未偵測到麥克風';
      els.micSelect.appendChild(opt);
      return;
    }

    const savedMicId = localStorage.getItem('stt-mic-device-id');
    let matchedIndex = -1;

    audioDevices.forEach((device, index) => {
      const opt = document.createElement('option');
      opt.value = device.deviceId;
      opt.textContent = device.label || `麥克風 ${index + 1} (${device.deviceId.slice(0, 5)})`;
      els.micSelect.appendChild(opt);

      if (savedMicId && device.deviceId === savedMicId) {
        matchedIndex = index;
      }
    });

    if (matchedIndex !== -1) {
      els.micSelect.selectedIndex = matchedIndex;
    } else {
      let defaultIdx = 0;
      let foundDJI = false;
      for (let i = 0; i < audioDevices.length; i++) {
        const lbl = audioDevices[i].label || '';
        if (lbl.includes('DJI')) {
          defaultIdx = i;
          foundDJI = true;
          break;
        }
      }
      if (!foundDJI) {
        for (let i = 0; i < audioDevices.length; i++) {
          const lbl = audioDevices[i].label || '';
          if (!lbl.includes('立體聲混音') && !lbl.includes('Stereo Mix') && !lbl.includes('Loopback')) {
            defaultIdx = i;
            break;
          }
        }
      }
      els.micSelect.selectedIndex = defaultIdx;
      if (audioDevices[defaultIdx]) {
        localStorage.setItem('stt-mic-device-id', audioDevices[defaultIdx].deviceId);
      }
    }
  } catch (err) {
    console.error('無法列出麥克風裝置：', err);
    els.micSelect.innerHTML = '<option value="">無法取得麥克風清單</option>';
  }
}

// ═══════════════════════════════════════════
// Recording toggle
// ═══════════════════════════════════════════
async function toggleRecording() {
  if (isRecording) { await stopRecording(); }
  else             { await startRecording(); }
}

async function startRecording() {
  try {
    const selectedDeviceId = els.micSelect.value;
    const constraints = {
      audio: {
        echoCancellation: true,
        noiseSuppression: true
      }
    };
    if (selectedDeviceId && selectedDeviceId !== 'default' && selectedDeviceId !== 'communications') {
      constraints.audio.deviceId = { exact: selectedDeviceId };
    }
    const micName = els.micSelect.options[els.micSelect.selectedIndex]?.text || '預設麥克風';
    logEvent('openai', 'info', `正在啟動麥克風: ${micName}`);
    micStream = await navigator.mediaDevices.getUserMedia(constraints);
  } catch (err) {
    alert('無法啟動麥克風: ' + err.message);
    return;
  }

  // Reset
  openaiTurnDeltas = ''; openaiAccText = ''; openaiSpeechStart = null;
  googleCurrentInter = ''; googleAccText = ''; googleSpeechStart = null;

  els.openaiStreaming.innerHTML  = '<span class="placeholder">等待語音輸入…</span>';
  els.openaiAccumulated.textContent = '';
  els.googleStreaming.innerHTML  = '<span class="placeholder">等待語音輸入…</span>';
  els.googleAccumulated.textContent = '';
  els.openaiLatency.textContent = '— ms';
  els.googleLatency.textContent = '— ms';



  // Launch both services in parallel
  const jobs = [];
  const apiKey = els.openaiKey.value.trim();
  if (apiKey || hasBackendOpenAIKey) {
    jobs.push(startOpenAI(apiKey).catch(err => {
      logEvent('openai', 'error', err.message);
      setStatus('openai', '錯誤', 'error');
    }));
  }
  const googleKey = els.googleKey.value.trim();
  if (googleKey || hasBackendGoogleKey || googleAvailable) {
    jobs.push(startGoogle(googleKey).catch(err => {
      logEvent('google', 'error', err.message);
      setStatus('google', '錯誤', 'error');
    }));
  }
  await Promise.allSettled(jobs);

  isRecording = true;
  els.btnToggle.classList.add('recording');
  els.btnToggle.querySelector('.btn-text').textContent = '停止錄音';
  els.btnToggle.querySelector('.btn-icon').textContent = '⏹️';

  // 註冊使用者互動時恢復 AudioContext 的處理器，防範瀏覽器掛起 Audio
  enableAudioContextResumeOnInteraction();
}

function enableAudioContextResumeOnInteraction() {
  const resume = async () => {
    let done = true;
    if (openaiAudioCtx && openaiAudioCtx.state === 'suspended') {
      try {
        await openaiAudioCtx.resume();
        logEvent('openai', 'info', '⚡ 偵測到使用者互動，音訊已恢復 (OpenAI)');
      } catch (e) {
        console.warn('無法恢復 OpenAI AudioContext:', e);
        done = false;
      }
    }
    if (googleAudioCtx && googleAudioCtx.state === 'suspended') {
      try {
        await googleAudioCtx.resume();
        logEvent('google', 'info', '⚡ 偵測到使用者互動，音訊已恢復 (Google)');
      } catch (e) {
        console.warn('無法恢復 Google AudioContext:', e);
        done = false;
      }
    }
    if (done) {
      window.removeEventListener('click', resume);
      window.removeEventListener('keydown', resume);
      window.removeEventListener('touchstart', resume);
    }
  };
  window.addEventListener('click', resume);
  window.addEventListener('keydown', resume);
  window.addEventListener('touchstart', resume);
}

async function stopRecording() {
  isRecording = false;

  // OpenAI cleanup
  if (openaiWS && openaiWS.readyState === WebSocket.OPEN) {
    try { openaiWS.send(JSON.stringify({ type: 'stop' })); } catch {}
    openaiWS.close();
  }
  openaiWS = null;
  if (openaiWorklet)  { openaiWorklet.disconnect(); openaiWorklet = null; }
  if (openaiAudioCtx) { openaiAudioCtx.close(); openaiAudioCtx = null; }
  setStatus('openai', '已斷線', '');

  // Google cleanup
  if (googleWS && googleWS.readyState === WebSocket.OPEN) {
    try { googleWS.send(JSON.stringify({ type: 'stop' })); } catch {}
    googleWS.close();
  }
  googleWS = null;
  if (googleWorklet)  { googleWorklet.disconnect(); googleWorklet = null; }
  if (googleAudioCtx) { googleAudioCtx.close(); googleAudioCtx = null; }
  setStatus('google', '已斷線', '');

  // Mic
  if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }

  stopAudioMeter();

  els.btnToggle.classList.remove('recording');
  els.btnToggle.querySelector('.btn-text').textContent = '開始錄音';
  els.btnToggle.querySelector('.btn-icon').textContent = '🎤';
}

// ═══════════════════════════════════════════
// OpenAI Realtime  (WebSocket proxy)
// ═══════════════════════════════════════════
function startOpenAI(apiKey) {
  logEvent('openai', 'info', '連線至 OpenAI Realtime…');

  const selectedModel = els.openaiModel.value || 'gpt-realtime-whisper';

  return new Promise((resolve, reject) => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    openaiWS = new WebSocket(`${proto}://${location.host}/ws/openai-stt`);

    openaiWS.onopen = () => {
      openaiWS.send(JSON.stringify({
        type: 'start',
        sampleRate: 24000,
        model: selectedModel,
        apiKey: apiKey
      }));
    };

    openaiWS.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'ready') {
          setStatus('openai', '已連線', 'connected');
          logEvent('openai', 'info', '串流已啟動 ✓');
          startOpenAIAudio().then(resolve).catch(reject);
          return;
        }
        handleOpenAI(msg);
      } catch {}
    };

    openaiWS.onerror = () => {
      setStatus('openai', '錯誤', 'error');
      reject(new Error('WebSocket error'));
    };

    openaiWS.onclose = () => setStatus('openai', '已斷線', '');
  });
}

async function startOpenAIAudio() {
  openaiAudioCtx = new AudioContext({ sampleRate: 24000 });
  if (openaiAudioCtx.state === 'suspended') {
    await openaiAudioCtx.resume();
    console.log('[OpenAI Audio] AudioContext resumed successfully');
  }
  const src = openaiAudioCtx.createMediaStreamSource(micStream);

  await openaiAudioCtx.audioWorklet.addModule('audio-processor.js');
  openaiWorklet = new AudioWorkletNode(openaiAudioCtx, 'pcm-audio-processor');

  openaiWorklet.port.onmessage = (e) => {
    if (e.data.type === 'audio' && openaiWS?.readyState === WebSocket.OPEN) {
      openaiWS.send(new Uint8Array(e.data.buffer));
    }
  };

  src.connect(openaiWorklet);
  const gain = openaiAudioCtx.createGain();
  gain.gain.value = 0;
  openaiWorklet.connect(gain);
  gain.connect(openaiAudioCtx.destination);

  startAudioMeterWithSrc(src, openaiAudioCtx);

  logEvent('openai', 'info', '音訊擷取啟動 (24 kHz PCM16)');
}

function handleOpenAI(msg) {
  switch (msg.type) {
    case 'info':
      logEvent('openai', 'info', msg.message);
      break;

    case 'speech_started':
      openaiSpeechStart = Date.now();
      openaiTurnDeltas  = '';
      logEvent('openai', 'speech_started', '🎤 語音開始');
      refreshOpenAI();
      break;

    case 'speech_stopped':
      logEvent('openai', 'speech_stopped', '🔇 語音結束');
      break;

    case 'delta':
      if (openaiSpeechStart && !openaiTurnDeltas) {
        els.openaiLatency.textContent = `${Date.now() - openaiSpeechStart} ms`;
      }
      openaiTurnDeltas += msg.delta || '';
      refreshOpenAI();
      logEvent('openai', 'delta', msg.delta || '');
      break;

    case 'completed': {
      const final = msg.transcript || '';
      const deltas = openaiTurnDeltas;
      if (deltas && deltas !== final) {
        logEvent('openai', 'correction', `"${deltas}" → "${final}"`);
      }
      openaiAccText += final;
      openaiTurnDeltas = '';
      refreshOpenAI();
      els.openaiAccumulated.textContent = openaiAccText;
      logEvent('openai', 'completed', final);
      break;
    }

    case 'error':
      logEvent('openai', 'error', msg.message);
      break;

    case 'stream_end':
      logEvent('openai', 'info', '串流結束');
      break;
  }
}

function refreshOpenAI() {
  const el = els.openaiStreaming;
  if (openaiTurnDeltas) {
    el.innerHTML = (openaiAccText ? `<span class="completed-text">${esc(openaiAccText)}</span>` : '') +
                   `<span class="delta-text cursor-blink">${esc(openaiTurnDeltas)}</span>`;
  } else if (openaiAccText) {
    el.innerHTML = `<span class="completed-text">${esc(openaiAccText)}</span>`;
  } else {
    el.innerHTML = '<span class="placeholder">等待語音輸入…</span>';
  }
}

// ═══════════════════════════════════════════
// Google Cloud STT  (WebSocket → backend)
// ═══════════════════════════════════════════
function startGoogle(apiKey) {
  logEvent('google', 'info', '連線至 Google Cloud STT…');

  return new Promise((resolve, reject) => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    googleWS = new WebSocket(`${proto}://${location.host}/ws/google-stt`);

    googleWS.onopen = () => {
      googleWS.send(JSON.stringify({
        type: 'start',
        sampleRate: 16000,
        language: 'zh-TW',
        model: els.googleModel.value || 'latest_long',
        apiKey: apiKey
      }));
    };

    googleWS.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'ready') {
          setStatus('google', '已連線', 'connected');
          logEvent('google', 'info', '串流已啟動 ✓');
          startGoogleAudio().then(resolve).catch(reject);
          return;
        }
        handleGoogle(msg);
      } catch {}
    };

    googleWS.onerror = () => {
      setStatus('google', '錯誤', 'error');
      reject(new Error('WebSocket error'));
    };

    googleWS.onclose = () => setStatus('google', '已斷線', '');
  });
}

async function startGoogleAudio() {
  googleAudioCtx = new AudioContext({ sampleRate: 16000 });
  if (googleAudioCtx.state === 'suspended') {
    await googleAudioCtx.resume();
    console.log('[Google Audio] AudioContext resumed successfully');
  }
  const src = googleAudioCtx.createMediaStreamSource(micStream);

  await googleAudioCtx.audioWorklet.addModule('audio-processor.js');
  googleWorklet = new AudioWorkletNode(googleAudioCtx, 'pcm-audio-processor');

  googleWorklet.port.onmessage = (e) => {
    if (e.data.type === 'audio' && googleWS?.readyState === WebSocket.OPEN) {
      googleWS.send(new Uint8Array(e.data.buffer));
    }
  };

  src.connect(googleWorklet);
  // Keep worklet alive with a silent output
  const gain = googleAudioCtx.createGain();
  gain.gain.value = 0;
  googleWorklet.connect(gain);
  gain.connect(googleAudioCtx.destination);

  startAudioMeterWithSrc(src, googleAudioCtx);

  logEvent('google', 'info', '音訊擷取啟動 (16 kHz PCM16)');
}

function handleGoogle(msg) {
  switch (msg.type) {
    case 'interim':
      if (!googleSpeechStart) googleSpeechStart = Date.now();
      if (googleSpeechStart && !googleCurrentInter) {
        els.googleLatency.textContent = `${Date.now() - googleSpeechStart} ms`;
      }
      googleCurrentInter = msg.transcript;
      refreshGoogle();
      logEvent('google', 'interim', msg.transcript);
      break;

    case 'final':
      googleAccText += msg.transcript;
      googleCurrentInter = '';
      googleSpeechStart  = null;
      refreshGoogle();
      els.googleAccumulated.textContent = googleAccText;
      logEvent('google', 'final',
        `${msg.transcript} (${(msg.confidence * 100).toFixed(1)}%)`);
      break;

    case 'error':
      logEvent('google', 'error', msg.message);
      break;

    case 'stream_end':
      logEvent('google', 'info', '串流結束');
      break;
  }
}

function refreshGoogle() {
  const el = els.googleStreaming;
  if (googleCurrentInter) {
    el.innerHTML =
      (googleAccText ? `<span class="final-text">${esc(googleAccText)}</span>` : '') +
      `<span class="interim-text cursor-blink">${esc(googleCurrentInter)}</span>`;
  } else if (googleAccText) {
    el.innerHTML = `<span class="final-text">${esc(googleAccText)}</span>`;
  } else {
    el.innerHTML = '<span class="placeholder">等待語音輸入…</span>';
  }
}

// ═══════════════════════════════════════════
// Audio level meter
// ═══════════════════════════════════════════
function startAudioMeterWithSrc(src, audioCtx) {
  stopAudioMeter();
  meterAnalyser = audioCtx.createAnalyser();
  meterAnalyser.fftSize = 256;
  src.connect(meterAnalyser);

  const canvas = els.audioMeter;
  meterCtx2d = canvas.getContext('2d');
  canvas.width  = canvas.offsetWidth * 2;
  canvas.height = 8;

  const buf = new Uint8Array(meterAnalyser.frequencyBinCount);

  function draw() {
    meterAnimFrame = requestAnimationFrame(draw);
    meterAnalyser.getByteFrequencyData(buf);
    const avg = buf.reduce((a, b) => a + b, 0) / buf.length / 255;

    const w = canvas.width, h = canvas.height;
    meterCtx2d.clearRect(0, 0, w, h);

    const grad = meterCtx2d.createLinearGradient(0, 0, w * avg, 0);
    grad.addColorStop(0,   '#34d399');
    grad.addColorStop(0.6, '#fbbf24');
    grad.addColorStop(1,   '#ef4444');
    meterCtx2d.fillStyle = grad;
    meterCtx2d.fillRect(0, 0, w * avg, h);
  }
  draw();
}

function stopAudioMeter() {
  if (meterAnimFrame) { cancelAnimationFrame(meterAnimFrame); meterAnimFrame = null; }
  if (meterCtx2d)     { meterCtx2d.clearRect(0, 0, els.audioMeter.width, els.audioMeter.height); }
}

// ═══════════════════════════════════════════
// Utilities
// ═══════════════════════════════════════════
function setStatus(service, text, cls) {
  const b = service === 'openai' ? els.openaiStatus : els.googleStatusBadge;
  b.textContent = text;
  b.className   = `status-badge ${cls}`;
}

function logEvent(service, type, content) {
  const log = service === 'openai' ? els.openaiLog : els.googleLog;
  const t   = new Date().toLocaleTimeString('zh-TW', { hour12: false });

  const div = document.createElement('div');
  div.className = 'ev';
  div.innerHTML =
    `<span class="ev-time">${t}</span>` +
    `<span class="ev-type ${type}">${type}</span>` +
    `<span class="ev-content">${esc(content)}</span>`;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;

  // cap at 300 entries
  while (log.children.length > 300) log.removeChild(log.firstChild);
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ───── Go ─────
init();
