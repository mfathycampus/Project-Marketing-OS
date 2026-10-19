"""Speech-to-text with word timings for "your face" videos: faster-whisper, running locally (free, private).

The model (WHISPER_MODEL, default "small", ~460 MB) downloads once on first use and is cached by the library.
"""
import logging
import threading

from app.config import settings

log = logging.getLogger(__name__)
_models: dict = {}
_lock = threading.Lock()


class TranscribeError(RuntimeError):
    pass


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _model():
    name = settings.whisper_model or "small"
    with _lock:
        if name not in _models:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscribeError("أداة تفريغ الكلام غير مثبتة: شغّل start.bat (يثبّت faster-whisper) ثم أعد المحاولة") from exc
            try:
                _models[name] = WhisperModel(name, device="cpu", compute_type="int8")
            except Exception as exc:  # noqa: BLE001 - first use downloads the model
                raise TranscribeError(f"تعذّر تحميل نموذج Whisper «{name}» (يحتاج إنترنت في أول مرة): {exc}") from exc
        return _models[name]


def transcribe(path: str, language: str | None = "ar", on_progress=None) -> list[tuple[float, float, str]]:
    """[(start_s, end_s, word), ...] for the speech in an audio/video file."""
    model = _model()
    try:
        segments, info = model.transcribe(path, language=language or None, word_timestamps=True, vad_filter=True,
                                          beam_size=5, condition_on_previous_text=False)
        words = []
        for seg in segments:
            for w in seg.words or []:
                text = w.word.strip()
                if text:
                    words.append((round(float(w.start), 3), round(float(w.end), 3), text))
            if on_progress and info.duration:
                on_progress(min(1.0, seg.end / info.duration))
    except Exception as exc:  # noqa: BLE001
        raise TranscribeError(f"فشل تفريغ الكلام: {exc}") from exc
    return words
