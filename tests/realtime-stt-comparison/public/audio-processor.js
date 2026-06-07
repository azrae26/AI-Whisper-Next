/**
 * AudioWorklet processor: captures raw PCM from mic → Int16 at AudioContext sample rate.
 * AudioContext should be created at 16000 Hz so browser handles downsampling.
 */
class PCMAudioProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = [];
    this._bufferSize = 2048; // ~128ms at 16kHz
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    const samples = input[0]; // mono
    for (let i = 0; i < samples.length; i++) {
      this._buffer.push(samples[i]);
    }

    while (this._buffer.length >= this._bufferSize) {
      const chunk = this._buffer.splice(0, this._bufferSize);
      const int16 = new Int16Array(chunk.length);

      for (let i = 0; i < chunk.length; i++) {
        const s = Math.max(-1, Math.min(1, chunk[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }

      this.port.postMessage(
        { type: 'audio', buffer: int16.buffer },
        [int16.buffer]
      );
    }

    return true;
  }
}

registerProcessor('pcm-audio-processor', PCMAudioProcessor);
