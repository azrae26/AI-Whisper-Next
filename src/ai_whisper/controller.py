from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QTimer, Signal

from .logging_setup import now_str, safe_print
from .models import AppConfig
from .services.audio_service import AudioService
from .services.hotkey_service import HotkeyService
from .services.paste_service import PasteService
from .services.settings_store import SettingsStore, is_startup_enabled, set_startup
from .services.transcription_service import TranscriptionService
from .services.vad_service import preload_silero_vad
from .ui.main_window import MainWindow

STATUS_CLEAR_DELAY_MS = 3500
ERROR_STATUS_CLEAR_DELAY_MS = 5500
OVERLAY_STATUS_CLEAR_DELAY_MS = 2000
ERROR_OVERLAY_STATUS_CLEAR_DELAY_MS = 4000


class AppController(QObject):
    segment_done = Signal(str)
    segments_complete = Signal()
    final_done = Signal(str)
    transcribe_error = Signal(str)
    no_audio = Signal()
    processing_started = Signal()

    def __init__(self, window: MainWindow, settings: SettingsStore):
        super().__init__()
        self.window = window
        self.settings_store = settings
        self.cfg = settings.get()
        self.audio = AudioService()
        self.paste = PasteService()
        self.hotkeys = HotkeyService()
        self.executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="AIWhisper")
        self.state = "idle"
        self.paste_prefix = "。"
        self._rec_start_time = 0.0
        self._prev_seg_event = threading.Event()
        self._prev_seg_event.set()
        self._segs_dispatched = 0
        self._segs_with_text = 0
        self._warmup_timer = QTimer(self)
        self._warmup_timer.setSingleShot(True)
        self._warmup_timer.timeout.connect(self._do_warmup_shutdown)
        self._segment_timer = QTimer(self)
        self._segment_timer.timeout.connect(self._check_segment)
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick_anim)
        self._waveform_timer = QTimer(self)
        self._waveform_timer.timeout.connect(self._tick_waveform)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._flush_settings)
        self._pending_config: AppConfig | None = None
        self._capture_field: str | None = None
        self._cleaned_up = False

        self._connect()
        self._apply_initial_state()
        preload_silero_vad()

    def _connect(self) -> None:
        self.window.toggle_clicked.connect(self.toggle_recording)
        self.window.settings_changed.connect(self.queue_settings_save)
        self.window.capture_requested.connect(self.start_hotkey_capture)
        self.window.tray_quit_requested.connect(self.quit_app)
        self.hotkeys.toggle_requested.connect(self.toggle_recording)
        self.hotkeys.history_requested.connect(self.paste_history)
        self.hotkeys.capture_finished.connect(self.finish_hotkey_capture)
        self.hotkeys.capture_cancelled.connect(self.cancel_hotkey_capture)
        self.segment_done.connect(self._on_segment_done)
        self.segments_complete.connect(self._on_segments_complete)
        self.final_done.connect(self._on_transcribe_done)
        self.transcribe_error.connect(self._on_transcribe_error)
        self.no_audio.connect(self._on_no_audio)
        self.processing_started.connect(self.window.set_processing_state)

    def _apply_initial_state(self) -> None:
        self.cfg.startup = is_startup_enabled()
        self.window.settings_page.set_config(self.cfg)
        self.window.set_hotkey_display(self.cfg.hotkey, self.cfg.hotkey_comma)
        self.hotkeys.register(self.cfg.hotkey, self.cfg.hotkey_comma, self.cfg.history_hotkeys)
        if not self.cfg.apiKey:
            QTimer.singleShot(300, self.window.show_settings)

    def queue_settings_save(self, cfg: AppConfig) -> None:
        cfg.geometry = self._geometry_string()
        self._pending_config = cfg
        self._save_timer.start(300)

    def _flush_settings(self) -> None:
        if self._pending_config is None:
            return
        old = self.cfg
        new = self._pending_config
        self._pending_config = None
        self.cfg = self.settings_store.save(new)
        if old.startup != new.startup:
            self.executor.submit(set_startup, new.startup)
        if (
            old.hotkey != new.hotkey
            or old.hotkey_comma != new.hotkey_comma
            or old.history_hotkeys != new.history_hotkeys
        ):
            self.hotkeys.register(new.hotkey, new.hotkey_comma, new.history_hotkeys)
        self.window.set_hotkey_display(new.hotkey, new.hotkey_comma)

    def start_hotkey_capture(self, field: str) -> None:
        self._capture_field = field
        self.window.settings_page.set_capture_prompt(field)
        self.hotkeys.start_capture()

    def finish_hotkey_capture(self, hotkey: str) -> None:
        if not self._capture_field:
            return
        field = self._capture_field
        self._capture_field = None
        self.hotkeys.finish_capture_cleanup()
        self.window.settings_page.set_captured_hotkey(field, hotkey)
        cfg = self.window.settings_page.current_config()
        self.hotkeys.register(cfg.hotkey, cfg.hotkey_comma, cfg.history_hotkeys)
        self.queue_settings_save(cfg)

    def cancel_hotkey_capture(self) -> None:
        if not self._capture_field:
            return
        field = self._capture_field
        self._capture_field = None
        self.hotkeys.finish_capture_cleanup()
        self.window.settings_page.reset_capture_button(field)
        cfg = self.window.settings_page.current_config()
        self.hotkeys.register(cfg.hotkey, cfg.hotkey_comma, cfg.history_hotkeys)
        self.queue_settings_save(cfg)

    def toggle_recording(self, paste_prefix: str = "。") -> None:
        if self.state == "idle":
            self.paste_prefix = paste_prefix
            self._start_recording()
        elif self.state == "recording":
            self._stop_recording()

    def _start_recording(self) -> None:
        if self._warmup_timer.isActive():
            self._warmup_timer.stop()
        t0 = time.perf_counter()
        ok = self.audio.start()
        safe_print(f"[main][{now_str()}] ⏱️ recorder.start() 耗時 {(time.perf_counter() - t0) * 1000:.1f}ms，ok={ok}")
        self._prev_seg_event = threading.Event()
        self._prev_seg_event.set()
        self._segs_dispatched = 0
        self._segs_with_text = 0
        if not ok:
            self.window.set_status("❌ 無法存取麥克風", "#EF4444")
            return
        self.state = "recording"
        self._rec_start_time = time.time()
        self.window.set_recording_state()
        self._anim_timer.start(33)
        self._waveform_timer.start(33)
        self._segment_timer.start(200)
        safe_print(f"[main][{now_str()}] 🎙️ 開始錄音")

    def _stop_recording(self) -> None:
        self._segment_timer.stop()
        self._anim_timer.stop()
        self._waveform_timer.stop()
        self.state = "processing"
        self.processing_started.emit()
        frames = self.audio.stop_capture()
        self._schedule_warmup_shutdown()
        self.executor.submit(self._process_final_audio, frames, self._prev_seg_event)

    def _process_final_audio(self, frames, prev_event: threading.Event) -> None:
        try:
            segment = self.audio.process_frames(frames, "stop", self.cfg.vad_confidence, self.cfg.vad_min_speech_sec)
        except Exception as e:
            safe_print(f"[main][{now_str()}] ❌ 音訊處理失敗: {e}")
            self.no_audio.emit()
            return
        if not segment.wav_bytes:
            if self._segs_dispatched > 0:
                prev_event.wait(timeout=30)
                if self._segs_with_text > 0:
                    self.segments_complete.emit()
                else:
                    self.final_done.emit("")
            else:
                self.no_audio.emit()
            return
        safe_print(f"[main][{now_str()}] ✅ 錄音完成，送出辨識")
        self.paste.prefetch_cursor_position(len(segment.wav_bytes))
        self._run_transcribe(segment.wav_bytes, prev_event, is_segment=False)

    def _schedule_warmup_shutdown(self) -> None:
        if self._warmup_timer.isActive():
            self._warmup_timer.stop()
        idle_min = self.cfg.warmup_idle_minutes
        self._warmup_timer.start(int(idle_min * 60 * 1000))
        safe_print(f"[main][{now_str()}] ⏳ 預熱 idle 計時器啟動，{idle_min:.0f} 分鐘後關閉麥克風")

    def _do_warmup_shutdown(self) -> None:
        self.executor.submit(self.audio.shutdown)
        safe_print(f"[main][{now_str()}] 💤 預熱 stream 已關閉（idle 超時）")

    def _check_segment(self) -> None:
        if self.state != "recording":
            return
        accumulated = self.audio.get_accumulated_seconds()
        silence = self.audio.get_silence_seconds()
        if (
            accumulated >= self.cfg.segment_max_accum and silence >= self.cfg.segment_short_silence
        ) or silence >= self.cfg.segment_silence:
            frames = self.audio.flush_capture()
            if not frames:
                return
            reason = "累積夠長+短靜音" if (
                accumulated >= self.cfg.segment_max_accum and silence >= self.cfg.segment_short_silence
            ) else f"靜音達{self.cfg.segment_silence:.0f}s"
            prev_event = self._prev_seg_event
            my_event = threading.Event()
            self._prev_seg_event = my_event
            self._segs_dispatched += 1
            self.executor.submit(self._process_segment_audio, frames, prev_event, my_event, reason, accumulated, silence)

    def _process_segment_audio(
        self,
        frames,
        prev_event: threading.Event,
        my_event: threading.Event,
        reason: str,
        accumulated: float,
        silence: float,
    ) -> None:
        try:
            segment = self.audio.process_frames(frames, "flush", self.cfg.vad_confidence, self.cfg.vad_min_speech_sec)
            self.audio.reset_silence()
            if not segment.wav_bytes:
                my_event.set()
                return
            safe_print(f"[main][{now_str()}] ✂️ 自動分段送出（{reason}，累積 {accumulated:.1f}s，靜音 {silence:.1f}s）")
            self.paste.prefetch_cursor_position(len(segment.wav_bytes))
            self._run_transcribe(segment.wav_bytes, prev_event, is_segment=True, my_event=my_event)
        except Exception as e:
            my_event.set()
            safe_print(f"[main][{now_str()}] ❌ 分段處理失敗: {e}")

    def _run_transcribe(
        self,
        wav_bytes: bytes,
        prev_event: threading.Event | None,
        is_segment: bool,
        my_event: threading.Event | None = None,
    ) -> None:
        cfg = self.cfg
        if not cfg.apiKey:
            if my_event:
                my_event.set()
            self.transcribe_error.emit("請先設定 API Key")
            return
        try:
            raw, clean, received_at = TranscriptionService.transcribe_clean(
                wav_bytes,
                cfg.apiKey,
                cfg.model,
                cfg.text_corrections,
            )
            if is_segment:
                safe_print(f"[main][{now_str()}] ✅ 分段辨識完成: \"{clean}\"")
                if clean:
                    assert prev_event is not None
                    prev_event.wait(timeout=30)
                    self.paste.paste_text(clean, delay_ms=30, t_received=received_at, end_prefix=self.paste_prefix)
                    self._segs_with_text += 1
                if my_event:
                    my_event.set()
                self.segment_done.emit(clean)
            else:
                safe_print(f"[main][{now_str()}] ✅ 辨識完成: \"{clean}\"")
                if clean:
                    if prev_event is not None:
                        prev_event.wait(timeout=30)
                    self.paste.paste_text(clean, t_received=received_at, end_prefix=self.paste_prefix)
                self.final_done.emit(clean)
        except Exception as e:
            if is_segment:
                if my_event:
                    my_event.set()
                safe_print(f"[main][{now_str()}] ❌ 分段辨識失敗: {e}")
            else:
                safe_print(f"[main][{now_str()}] ❌ 辨識失敗: {e}")
                self.transcribe_error.emit(str(e))

    def _on_segment_done(self, text: str) -> None:
        if text:
            self.window.add_history(text)

    def _on_segments_complete(self) -> None:
        self.state = "idle"
        self.window.set_idle_state()
        self.window.set_status("辨識完成 ✓", "#6EE7B7")
        self.window.show_overlay_status("辨識完成 ✓", "#6EE7B7", OVERLAY_STATUS_CLEAR_DELAY_MS)
        QTimer.singleShot(STATUS_CLEAR_DELAY_MS, lambda: self.window.set_status("等待中", "#A1A1AA"))

    def _on_transcribe_done(self, text: str) -> None:
        self.state = "idle"
        self.window.set_idle_state()
        if text:
            self.window.add_history(text)
            self.window.set_status("辨識完成 ✓", "#6EE7B7")
            self.window.show_overlay_status("辨識完成 ✓", "#6EE7B7", OVERLAY_STATUS_CLEAR_DELAY_MS)
        else:
            self.window.set_status("⚠ 未辨識到內容", "#FCD34D")
            self.window.show_overlay_status("未辨識到內容", "#FCD34D", OVERLAY_STATUS_CLEAR_DELAY_MS)
        QTimer.singleShot(STATUS_CLEAR_DELAY_MS, lambda: self.window.set_status("等待中", "#A1A1AA"))

    def _on_transcribe_error(self, err_msg: str) -> None:
        self.state = "idle"
        self.window.set_idle_state()
        if "API Key" in err_msg:
            self.window.set_status("❌ 請先設定 API Key", "#F87171")
            self.window.show_settings()
        else:
            short = err_msg[:60] + "…" if len(err_msg) > 60 else err_msg
            self.window.set_status(f"❌ {short}", "#F87171")
        self.window.show_overlay_status("辨識失敗", "#F87171", ERROR_OVERLAY_STATUS_CLEAR_DELAY_MS)
        QTimer.singleShot(ERROR_STATUS_CLEAR_DELAY_MS, lambda: self.window.set_status("等待中", "#A1A1AA"))

    def _on_no_audio(self) -> None:
        self.state = "idle"
        self.window.set_idle_state()
        self.window.set_status("⚠ 未錄到音訊", "#FCD34D")
        self.window.show_overlay_status("未錄到音訊", "#FCD34D", OVERLAY_STATUS_CLEAR_DELAY_MS)
        QTimer.singleShot(STATUS_CLEAR_DELAY_MS, lambda: self.window.set_status("等待中", "#A1A1AA"))

    def _tick_anim(self) -> None:
        if self.state != "recording":
            return
        elapsed = time.time() - self._rec_start_time
        minutes = int(elapsed) // 60
        seconds = int(elapsed) % 60
        t = (math.sin(elapsed * math.pi) + 1) / 2
        r = int(200 + (255 - 200) * t)
        g = int(60 + (180 - 60) * t)
        b = int(60 + (180 - 60) * t)
        self.window.set_timer(f"{minutes:02d}:{seconds:02d}", f"#{r:02x}{g:02x}{b:02x}")

    def _tick_waveform(self) -> None:
        if self.state != "recording":
            return
        self.window.set_waveform(self.audio.get_waveform())

    def paste_history(self, idx: int) -> None:
        text = self.window.history_text(idx)
        if text:
            safe_print(f"[main][{now_str()}] 📋 貼上記憶 {idx + 1}: \"{text[:20]}\"")
            self.paste.paste_text(text, delay_ms=0)
        else:
            safe_print(f"[main][{now_str()}] ⚠️ 記憶 {idx + 1} 不存在")

    def _geometry_string(self) -> str:
        geo = self.window.geometry()
        return f"{geo.width()}x{geo.height()}+{geo.x()}+{geo.y()}"

    def save_geometry_now(self) -> None:
        if self._pending_config is not None:
            self.cfg = self._pending_config
            self._pending_config = None
        cfg = self.cfg
        cfg.geometry = self._geometry_string()
        self.settings_store.save(cfg)

    def quit_app(self) -> None:
        self.cleanup()
        from PySide6.QtWidgets import QApplication
        QApplication.quit()

    def cleanup(self) -> None:
        if self._cleaned_up:
            return
        self._cleaned_up = True
        self.save_geometry_now()
        self.hotkeys.shutdown()
        self.audio.shutdown()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.window.tray.hide()
        self.window.waveform_overlay.hide_overlay()
