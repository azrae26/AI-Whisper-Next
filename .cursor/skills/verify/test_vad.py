import sys
import os
import wave
import numpy as np

# 尋找專案根目錄（包含 src/ 的目錄）
current_path = os.path.abspath(os.path.dirname(__file__))
project_root = None
for _ in range(6):
    if os.path.exists(os.path.join(current_path, "src")):
        project_root = current_path
        break
    current_path = os.path.dirname(current_path)

if not project_root:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

sys.path.insert(0, os.path.join(project_root, "src"))

from ai_whisper.services.vad_service import analyze_speech

def main():
    wav_path = os.path.join(project_root, "tests", "golden_speech.wav")
    if not os.path.exists(wav_path):
        print(f"Error: wav file not found at {wav_path}")
        return

    with wave.open(wav_path, "rb") as w:
        params = w.getparams()
        frames = w.readframes(w.getnframes())
        audio_data = np.frombuffer(frames, dtype=np.int16)

    print(f"Wav params: {params}")
    print(f"Audio samples: {len(audio_data)}")
    
    result = analyze_speech(audio_data)
    print("VAD Analysis Result:")
    print(f"  has_speech: {result.has_speech}")
    print(f"  engine: {result.engine}")
    print(f"  speech_frames: {result.speech_frames}")
    print(f"  total_frames: {result.total_frames}")
    print(f"  speech_ratio: {result.speech_ratio:.4f}")
    print(f"  speech_seconds: {result.speech_seconds:.4f}s")
    print(f"  reason: {result.reason}")

if __name__ == "__main__":
    main()
