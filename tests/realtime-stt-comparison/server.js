require('dotenv').config();
const express = require('express');
const { createServer } = require('http');
const { WebSocketServer, WebSocket: WsClient } = require('ws');
const path = require('path');
const fs = require('fs');

// ========== Auto-load OpenAI key from project config.json ==========
let projectApiKey = '';
try {
  const cfgPath = path.resolve(__dirname, '../../config.json');
  const raw = fs.readFileSync(cfgPath, 'utf-8');
  const cfg = JSON.parse(raw);
  if (cfg.apiKey) {
    projectApiKey = cfg.apiKey;
    console.log('✅ OpenAI API key: loaded from config.json');
  }
} catch (err) {
  console.log('⚠️  config.json not found or unreadable, enter key in browser');
}

const app = express();
const server = createServer(app);

app.use(express.static(path.join(__dirname, 'public')));
app.use(express.json());

// ========== Google Cloud STT availability ==========
let googleAvailable = false;
let googleError = '';
const projectGoogleApiKey = 'AIzaSyAGVXHX3g1vetMYQL4XpZ_rlluVrqd2Fl8';

(async () => {
  try {
    const { SpeechClient } = require('@google-cloud/speech');
    // If we have an API Key, construct client using it.
    const client = new SpeechClient(projectGoogleApiKey ? { apiKey: projectGoogleApiKey } : {});
    if (projectGoogleApiKey) {
      googleAvailable = true;
      console.log('✅ Google Cloud Speech-to-Text: configured with API Key');
    } else {
      await client.getProjectId();
      googleAvailable = true;
      console.log('✅ Google Cloud Speech-to-Text: available with default credentials');
    }
  } catch (err) {
    googleError = err.message;
    console.log('⚠️  Google Cloud Speech-to-Text: not available');
    console.log('   Set GOOGLE_APPLICATION_CREDENTIALS or run: gcloud auth application-default login');
  }
})();

// ========== API Routes ==========

app.get('/api/google-status', (_req, res) => {
  res.json({
    available: googleAvailable || !!projectGoogleApiKey,
    error: googleError,
    hasKey: !!projectGoogleApiKey,
    keyPrefix: projectGoogleApiKey ? projectGoogleApiKey.substring(0, 8) + '...' : ''
  });
});

// Serve project API key to frontend (local-only test tool)
app.get('/api/config', (_req, res) => {
  res.json({ apiKey: projectApiKey ? projectApiKey.substring(0, 8) + '...' : '', hasKey: !!projectApiKey });
});

// Diagnostic endpoint to check all models available for the project key
app.get('/api/diag-models', async (_req, res) => {
  if (!projectApiKey) return res.status(400).json({ error: 'No project api key loaded' });
  try {
    const response = await fetch('https://api.openai.com/v1/models', {
      headers: { 'Authorization': `Bearer ${projectApiKey}` }
    });
    const data = await response.json();
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Allow frontend to use project key without exposing it
app.post('/api/openai-token', async (req, res) => {
  let { apiKey, model } = req.body;
  // If no key provided, use project config key
  if (!apiKey && projectApiKey) apiKey = projectApiKey;
  if (!apiKey) return res.status(400).json({ error: 'API key is required' });

  try {
    const response = await fetch('https://api.openai.com/v1/realtime/client_secrets', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        session: {
          type: 'realtime',
          model: model || 'gpt-4o-mini-realtime-preview',
        }
      }),
    });

    if (!response.ok) {
      const errText = await response.text();
      return res.status(response.status).json({ error: errText });
    }

    const data = await response.json();
    console.log('[OpenAI client_secrets response]:', JSON.stringify(data, null, 2));
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// (openai-token endpoint already defined above)

// ========== WebSocket proxy for OpenAI Realtime STT ==========
const openaiWss = new WebSocketServer({ noServer: true });

openaiWss.on('connection', (clientWs) => {
  console.log('[OpenAI STT] Client connected');
  let openaiWs = null;
  let audioChunkCount = 0;
  let commitInterval = null;

  clientWs.on('message', (data, isBinary) => {
    if (isBinary) {
      // Forward raw audio as base64 to OpenAI
      if (openaiWs && openaiWs.readyState === WsClient.OPEN) {
        audioChunkCount++;
        const b64 = data.toString('base64');
        if (audioChunkCount === 1) {
          console.log(`[OpenAI STT] First chunk: raw=${data.length} bytes, base64=${b64.length} chars, first20b64="${b64.substring(0,20)}"`);
          // Save raw PCM to disk for verification
          require('fs').writeFileSync('debug_chunk1.pcm', data);
          console.log('[OpenAI STT] Saved debug_chunk1.pcm');
        }
        if (audioChunkCount % 100 === 1) {
          // Calculate max amplitude for debugging
          const int16 = new Int16Array(data.buffer, data.byteOffset, data.length / 2);
          let maxVal = 0;
          for (let i = 0; i < int16.length; i++) {
            const abs = Math.abs(int16[i]);
            if (abs > maxVal) maxVal = abs;
          }
          console.log(`[OpenAI STT] Audio chunk #${audioChunkCount}, size=${data.length} bytes, max_amplitude=${maxVal}`);
        }
        const payload = JSON.stringify({
          type: 'input_audio_buffer.append',
          audio: b64,
        });
        if (audioChunkCount === 1) {
          console.log(`[OpenAI STT] First payload length: ${payload.length}, type field: ${JSON.parse(payload).type}`);
        }
        openaiWs.send(payload);
      }
      return;
    }

    try {
      const msg = JSON.parse(data.toString());

      if (msg.type === 'start') {
        const apiKey = msg.apiKey || projectApiKey;
        if (!apiKey) {
          clientWs.send(JSON.stringify({ type: 'error', message: 'No API key' }));
          return;
        }

        const selectedModel = msg.model || 'gpt-realtime-whisper';
        const isWhisper = selectedModel === 'gpt-realtime-whisper';

        const wsUrl = `wss://api.openai.com/v1/realtime?model=gpt-realtime-2`;
        console.log(`[OpenAI STT] Connecting to: ${wsUrl}`);

        openaiWs = new WsClient(wsUrl, {
          headers: {
            'Authorization': `Bearer ${apiKey}`,
          },
        });

        openaiWs.on('open', () => {
          console.log('[OpenAI STT] Connected to OpenAI Realtime API');

          const sessionUpdatePayload = {
            type: 'session.update',
            session: {
              type: 'realtime',
              audio: {
                input: {
                  format: {
                    type: 'audio/pcm',
                    rate: 24000,
                  },
                  transcription: {
                    model: selectedModel,
                    language: 'zh',
                  }
                },
              },
            },
          };

          if (!isWhisper) {
            sessionUpdatePayload.session.audio.input.turn_detection = {
              type: 'server_vad',
              threshold: 0.5,
              silence_duration_ms: 1000,
            };
          }

          openaiWs.send(JSON.stringify(sessionUpdatePayload));
          console.log(`[OpenAI STT] Configured session: type=${sessionUpdatePayload.session.type}, model=${selectedModel}`);

          clientWs.send(JSON.stringify({ type: 'ready' }));
        });

        openaiWs.on('message', (raw) => {
          if (clientWs.readyState !== 1) return;
          try {
            const ev = JSON.parse(raw.toString());

            switch (ev.type) {
              case 'session.created':
              case 'session.updated':
              case 'transcription_session.created':
              case 'transcription_session.updated':
                console.log(`[OpenAI STT] ${ev.type}`);
                clientWs.send(JSON.stringify({ type: 'info', message: ev.type }));
                break;

              case 'input_audio_buffer.speech_started':
                clientWs.send(JSON.stringify({ type: 'speech_started', timestamp: Date.now() }));
                break;

              case 'input_audio_buffer.speech_stopped':
                clientWs.send(JSON.stringify({ type: 'speech_stopped' }));
                break;

              case 'conversation.item.input_audio_transcription.delta':
                clientWs.send(JSON.stringify({
                  type: 'delta',
                  delta: ev.delta || '',
                  timestamp: Date.now(),
                }));
                break;

              case 'conversation.item.input_audio_transcription.completed':
                clientWs.send(JSON.stringify({
                  type: 'completed',
                  transcript: ev.transcript || '',
                  timestamp: Date.now(),
                }));
                break;

              case 'error':
                console.error('[OpenAI STT] Error:', JSON.stringify(ev.error));
                clientWs.send(JSON.stringify({
                  type: 'error',
                  message: ev.error?.message || JSON.stringify(ev.error),
                }));
                break;

              default:
                // Log ALL events for debugging
                console.log(`[OpenAI STT] Event: ${ev.type}`);
                break;
            }
          } catch {}
        });

        openaiWs.on('error', (err) => {
          console.error('[OpenAI STT] WS error:', err.message);
          if (clientWs.readyState === 1) {
            clientWs.send(JSON.stringify({ type: 'error', message: err.message }));
          }
        });

        openaiWs.on('close', (code, reason) => {
          console.log(`[OpenAI STT] WS closed: ${code} ${reason}`);
          if (clientWs.readyState === 1) {
            clientWs.send(JSON.stringify({ type: 'stream_end' }));
          }
        });
      }

      if (msg.type === 'stop') {
        if (openaiWs && openaiWs.readyState === WsClient.OPEN) {
          console.log('[OpenAI STT] Stop requested, committing final audio buffer...');
          try {
            openaiWs.send(JSON.stringify({ type: 'input_audio_buffer.commit' }));
          } catch (err) {
            console.error('[OpenAI STT] Final commit error:', err.message);
          }
          const wsToClose = openaiWs;
          openaiWs = null;
          setTimeout(() => {
            if (wsToClose.readyState === WsClient.OPEN) {
              console.log('[OpenAI STT] Closing OpenAI connection after final commit delay');
              wsToClose.close();
            }
          }, 1000);
        } else {
          openaiWs = null;
        }
        console.log('[OpenAI STT] Streaming stopped');
      }
    } catch (err) {
      console.error('[OpenAI STT] Message error:', err.message);
    }
  });

  clientWs.on('close', () => {
    if (commitInterval) clearInterval(commitInterval);
    if (openaiWs && openaiWs.readyState === WsClient.OPEN) {
      openaiWs.close();
    }
    console.log('[OpenAI STT] Client disconnected');
  });
});

// ========== WebSocket proxy for Google Cloud STT ==========

const googleWss = new WebSocketServer({ noServer: true });

googleWss.on('connection', (ws) => {
  console.log('[Google STT] Client connected');
  let recognizeStream = null;
  let startMsgPayload = null;
  let client = null;
  let reconnectorTimer = null;

  function startGoogleStream() {
    if (recognizeStream) {
      try {
        recognizeStream.end();
      } catch (err) {
        console.error('[Google STT] Error ending old stream during reconnect:', err.message);
      }
      recognizeStream = null;
    }

    console.log('[Google STT] Creating new recognizeStream (silent reconnect)...');

    const { SpeechClient } = require('@google-cloud/speech');
    if (!client) {
      const apiKey = startMsgPayload.apiKey || projectGoogleApiKey;
      client = new SpeechClient(apiKey ? { apiKey } : {});
    }

    recognizeStream = client.streamingRecognize({
      config: {
        encoding: 'LINEAR16',
        sampleRateHertz: startMsgPayload.sampleRate || 16000,
        languageCode: startMsgPayload.language || 'zh-TW',
        enableAutomaticPunctuation: true,
        model: startMsgPayload.model || 'latest_long',
      },
      interimResults: true,
    });

    recognizeStream.on('data', (response) => {
      if (ws.readyState !== 1) return; // OPEN
      if (!response.results || response.results.length === 0) return;

      const result = response.results[0];
      ws.send(JSON.stringify({
        type: result.isFinal ? 'final' : 'interim',
        transcript: result.alternatives[0]?.transcript || '',
        confidence: result.alternatives[0]?.confidence || 0,
        stability: result.stability || 0,
        timestamp: Date.now(),
      }));
    });

    recognizeStream.on('error', (err) => {
      console.error('[Google STT] Stream error:', err.message);
      
      // 如果是超過 305 秒的時長限制錯誤，主動在後端重連，不向前端發送 error
      if (err.message.includes('Exceeded maximum allowed stream duration') || err.message.includes('305 seconds')) {
        console.log('[Google STT] Duration limit hit. Attempting silent reconnect...');
        startGoogleStream();
      } else {
        if (ws.readyState === 1) {
          ws.send(JSON.stringify({ type: 'error', message: err.message }));
        }
      }
    });

    recognizeStream.on('end', () => {
      // 只有在非重連、且真正結束時才發送 end 給前端
      // 這裡不主動發送，避免干擾前端狀態
    });

    // 防範機制：在 290 秒（約 4.8 分鐘）時主動、定時重連，避免觸發 Google 的 305 秒硬上限
    if (reconnectorTimer) clearTimeout(reconnectorTimer);
    reconnectorTimer = setTimeout(() => {
      console.log('[Google STT] 290s limit approaching. Initiating proactive silent reconnect...');
      startGoogleStream();
    }, 290000);
  }

  ws.on('message', (data, isBinary) => {
    if (isBinary) {
      if (recognizeStream && !recognizeStream.destroyed) {
        recognizeStream.write(data);
      }
      return;
    }

    try {
      const msg = JSON.parse(data.toString());

      if (msg.type === 'start') {
        startMsgPayload = msg;
        startGoogleStream();
        ws.send(JSON.stringify({ type: 'ready' }));
        console.log('[Google STT] Streaming started');
      }

      if (msg.type === 'stop') {
        if (reconnectorTimer) {
          clearTimeout(reconnectorTimer);
          reconnectorTimer = null;
        }
        if (recognizeStream && !recognizeStream.destroyed) {
          recognizeStream.end();
          recognizeStream = null;
        }
        console.log('[Google STT] Streaming stopped');
      }
    } catch (err) {
      console.error('[Google STT] Message error:', err.message);
    }
  });

  ws.on('close', () => {
    if (reconnectorTimer) {
      clearTimeout(reconnectorTimer);
      reconnectorTimer = null;
    }
    if (recognizeStream && !recognizeStream.destroyed) {
      recognizeStream.end();
    }
    console.log('[Google STT] Client disconnected');
  });
});

// ========== Manual WebSocket upgrade routing ==========
server.on('upgrade', (req, socket, head) => {
  const pathname = new URL(req.url, `http://${req.headers.host}`).pathname;
  if (pathname === '/ws/openai-stt') {
    openaiWss.handleUpgrade(req, socket, head, (ws) => openaiWss.emit('connection', ws, req));
  } else if (pathname === '/ws/google-stt') {
    googleWss.handleUpgrade(req, socket, head, (ws) => googleWss.emit('connection', ws, req));
  } else {
    socket.destroy();
  }
});

// ========== Start ==========

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
  console.log(`\n🎙️  Realtime STT Comparison Server`);
  console.log(`   http://localhost:${PORT}`);
  console.log(`   OpenAI Realtime: ready (WebSocket proxy)`);
  console.log(`   Google Cloud STT: ${googleAvailable ? '✅ ready' : '❌ not configured'}\n`);
});

