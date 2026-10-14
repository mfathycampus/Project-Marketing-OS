"""Arabic voiceover via Azure AI Speech (REST, no SDK). One clip per scene, cached on disk, so each scene
lasts exactly as long as its narration and the on-screen text doubles as the caption."""
import hashlib
import io
import wave
from html import escape

import httpx

from app.config import settings
from app.storage.local import LocalStorage

OUTPUT_FORMAT = "riff-24khz-16bit-mono-pcm"  # WAV: duration is exact and clips concatenate losslessly
RATE = 24000


class VoiceError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(settings.azure_speech_key and settings.azure_speech_region)


def _ssml(text: str, voice: str, rate: str) -> str:
    lang = "-".join(voice.split("-")[:2]) if voice.count("-") >= 2 else "ar-SA"
    return (f"<speak version='1.0' xml:lang='{lang}'><voice name='{escape(voice)}'>"
            f"<prosody rate='{escape(rate)}'>{escape(text)}</prosody></voice></speak>")


def synthesize(text: str, voice: str | None = None, http: httpx.Client | None = None,
               storage: LocalStorage | None = None) -> bytes:
    """WAV bytes for `text`. Cached by (voice, rate, text) so re-rendering a video costs nothing."""
    voice = voice or settings.tts_voice
    storage = storage or LocalStorage()
    key = "tts/" + hashlib.sha256(f"{voice}|{settings.tts_rate}|{text}".encode()).hexdigest() + ".wav"
    try:
        return storage.get(key)
    except OSError:
        pass
    url = f"https://{settings.azure_speech_region.strip()}.tts.speech.microsoft.com/cognitiveservices/v1"
    client = http or httpx.Client(timeout=60)
    try:
        r = client.post(url, content=_ssml(text, voice, settings.tts_rate).encode("utf-8"), headers={
            "Ocp-Apim-Subscription-Key": settings.azure_speech_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
            "User-Agent": "marketing-os",
        })
    except httpx.HTTPError as exc:
        raise VoiceError(f"تعذّر الاتصال بخدمة Azure Speech ({settings.azure_speech_region}): {exc}") from exc
    if r.status_code in (401, 403):
        raise VoiceError("مفتاح Azure Speech أو المنطقة غير صحيحة (401/403). شغّل set-azure.bat وأدخلهما من صفحة Keys and Endpoint")
    if r.status_code == 429:
        raise VoiceError("تجاوزت حد Azure Speech المؤقت (429). أعد المحاولة بعد دقيقة")
    if r.status_code >= 400 or not r.content.startswith(b"RIFF"):
        raise VoiceError(f"رفضت Azure Speech توليد الصوت (HTTP {r.status_code}): {r.text[:200]}")
    storage.put(key, r.content)
    return r.content


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / float(w.getframerate())


def build_track(clips: list[tuple[float, bytes | None]], total_s: float) -> bytes:
    """One mono WAV with each clip placed at its start time (seconds) and silence elsewhere."""
    frames = bytearray(b"\x00\x00" * int(total_s * RATE))
    for start, data in clips:
        if not data:
            continue
        with wave.open(io.BytesIO(data)) as w:
            if w.getframerate() != RATE or w.getsampwidth() != 2 or w.getnchannels() != 1:
                raise VoiceError("unexpected voice clip format")
            pcm = w.readframes(w.getnframes())
        at = int(start * RATE) * 2
        end = min(len(frames), at + len(pcm))
        frames[at:end] = pcm[: end - at]
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    return out.getvalue()
