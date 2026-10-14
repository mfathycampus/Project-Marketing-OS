"""Arabic voiceover. Providers, picked automatically (VOICE_PROVIDER=auto):

1. ElevenLabs  (ELEVENLABS_API_KEY + ELEVENLABS_VOICE_ID)  best quality, commercial use on paid plans
2. Azure Speech (AZURE_SPEECH_KEY + AZURE_SPEECH_REGION)    official Microsoft neural voices
3. edge-tts     (no key)                                    same Microsoft voices through Edge's read-aloud
                                                             service; unofficial, may stop working

Every provider returns a 24 kHz mono 16-bit WAV, cached by (provider, voice, rate, text), one clip per scene, so a
scene lasts exactly as long as its narration and the on-screen text doubles as the caption.
"""
import hashlib
import io
import json
import subprocess
import tempfile
import wave
from html import escape
from pathlib import Path

import httpx

from app.config import settings
from app.storage.local import LocalStorage

OUTPUT_FORMAT = "riff-24khz-16bit-mono-pcm"  # Azure: WAV, exact duration, lossless concatenation
RATE = 24000
ELEVEN_API = "https://api.elevenlabs.io/v1"


class VoiceError(RuntimeError):
    pass


# ---------- provider selection ----------
def _edge_available() -> bool:
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        return False
    return True


def provider() -> str | None:
    wanted = (settings.voice_provider or "auto").lower()
    if wanted == "off":
        return None
    have = {
        "elevenlabs": bool(settings.elevenlabs_api_key and settings.elevenlabs_voice_id),
        "azure": bool(settings.azure_speech_key and settings.azure_speech_region),
        "edge": _edge_available(),
    }
    if wanted in have:
        return wanted if have[wanted] else None
    return next((p for p in ("elevenlabs", "azure", "edge") if have[p]), None)


def enabled() -> bool:
    return provider() is not None


def current_voice() -> str:
    """'provider:voice' for the video request (part of the video digest), or '' for no narration."""
    p = provider()
    if p is None:
        return ""
    return f"{p}:{settings.elevenlabs_voice_id if p == 'elevenlabs' else settings.tts_voice}"


# ---------- synthesis ----------
def synthesize(text: str, voice: str | None = None, http: httpx.Client | None = None,
               storage: LocalStorage | None = None) -> bytes:
    """WAV bytes for `text`. `voice` is 'provider:name' (a bare name means Azure, for older callers)."""
    voice = voice or current_voice() or f"azure:{settings.tts_voice}"
    prov, _, name = voice.partition(":") if ":" in voice else ("azure", "", voice)
    storage = storage or LocalStorage()
    key = _key(prov, name, text)
    try:
        return storage.get(key)
    except OSError:
        pass
    if prov == "elevenlabs":
        data = _elevenlabs(text, name, http)
    elif prov == "edge":
        data, words = _edge(text, name)
        storage.put(key + ".words.json", json.dumps(words, ensure_ascii=False).encode("utf-8"))
    else:
        data = _azure(text, name, http)
    storage.put(key, data)
    return data


def _ssml(text: str, voice: str, rate: str) -> str:
    lang = "-".join(voice.split("-")[:2]) if voice.count("-") >= 2 else "ar-SA"
    return (f"<speak version='1.0' xml:lang='{lang}'><voice name='{escape(voice)}'>"
            f"<prosody rate='{escape(rate)}'>{escape(text)}</prosody></voice></speak>")


def _azure(text: str, voice: str, http: httpx.Client | None) -> bytes:
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
    return r.content


def _elevenlabs(text: str, voice_id: str, http: httpx.Client | None) -> bytes:
    client = http or httpx.Client(timeout=120)
    try:
        r = client.post(f"{ELEVEN_API}/text-to-speech/{voice_id}", params={"output_format": "pcm_24000"},
                        headers={"xi-api-key": settings.elevenlabs_api_key, "Accept": "audio/pcm"},
                        json={"text": text, "model_id": settings.elevenlabs_model})
    except httpx.HTTPError as exc:
        raise VoiceError(f"تعذّر الاتصال بـ ElevenLabs: {exc}") from exc
    if r.status_code == 401:
        raise VoiceError("مفتاح ElevenLabs غير صحيح أو بلا صلاحية text_to_speech (401). شغّل set-elevenlabs.bat")
    if r.status_code == 404:
        raise VoiceError("ELEVENLABS_VOICE_ID غير موجود في حسابك (404). أضف الصوت إلى My Voices ثم شغّل set-elevenlabs.bat")
    if r.status_code >= 400:
        detail = r.text[:240]
        if "quota" in detail.lower() or r.status_code == 402:
            raise VoiceError(f"انتهى رصيد أحرف ElevenLabs لهذا الشهر: {detail}")
        raise VoiceError(f"رفضت ElevenLabs توليد الصوت (HTTP {r.status_code}): {detail}")
    if not r.content:
        raise VoiceError("ElevenLabs أعاد صوتًا فارغًا")
    return _pcm_to_wav(r.content)


def _edge(text: str, voice: str) -> tuple[bytes, list[list]]:
    """(WAV, [[start_s, end_s, word], ...]): Edge also reports when each word is spoken (synced captions)."""
    try:
        import edge_tts
    except ImportError as exc:
        raise VoiceError("حزمة edge-tts غير مثبتة: شغّل start.bat (أو python -m pip install edge-tts)") from exc
    audio, words = bytearray(), []
    try:
        comm = edge_tts.Communicate(text, voice, rate=settings.tts_rate, boundary="WordBoundary")
        for chunk in comm.stream_sync():
            if chunk["type"] == "audio":
                audio += chunk["data"]
            elif chunk["type"] == "WordBoundary":  # offsets are in 100 ns ticks
                start = chunk["offset"] / 1e7
                words.append([round(start, 3), round(start + chunk["duration"] / 1e7, 3), chunk["text"]])
    except Exception as exc:  # noqa: BLE001 - unofficial service: report, never crash the video
        raise VoiceError(f"خدمة edge-tts المجانية لم تستجب ({type(exc).__name__}): {str(exc)[:160]}") from exc
    if not audio:
        raise VoiceError(f"خدمة edge-tts لم ترجع صوتًا للصوت {voice}")
    with tempfile.TemporaryDirectory() as tmp:
        mp3 = Path(tmp) / "v.mp3"
        mp3.write_bytes(bytes(audio))
        return _to_wav(mp3), words


def _key(prov: str, name: str, text: str) -> str:
    return "tts/" + hashlib.sha256(f"{prov}|{name}|{settings.tts_rate}|{text}".encode()).hexdigest() + ".wav"


def word_times(text: str, voice: str, seconds: float, storage: LocalStorage | None = None) -> list[tuple[float, float, str]]:
    """When each on-screen word of `text` is spoken within its clip. Real timings from Edge when cached; otherwise
    the clip's duration is spread over the words by length (Azure REST and ElevenLabs return none here)."""
    tokens = text.split()
    if not tokens:
        return []
    prov, _, name = voice.partition(":") if ":" in voice else ("azure", "", voice)
    spoken = []
    try:
        spoken = json.loads((storage or LocalStorage()).get(_key(prov, name, text) + ".words.json").decode("utf-8"))
    except (OSError, ValueError):
        pass
    if spoken:  # the voice may split/merge tokens (numbers, punctuation): map each on-screen word proportionally
        n, m = len(tokens), len(spoken)
        return [(float(spoken[min(m - 1, i * m // n)][0]), float(spoken[min(m - 1, max(i * m // n, (i + 1) * m // n - 1))][1]), tok)
                for i, tok in enumerate(tokens)]
    weights = [len(t) + 2 for t in tokens]
    total, at, out = sum(weights), 0.0, []
    for tok, wgt in zip(tokens, weights):
        out.append((at, at + seconds * wgt / total, tok))
        at += seconds * wgt / total
    return out


# Arabic voices of the Microsoft catalogue (Edge and Azure share it)
VOICES = {
    "ar-EG-SalmaNeural": "سلمى (مصري)", "ar-EG-ShakirNeural": "شاكر (مصري)",
    "ar-SA-HamedNeural": "حامد (سعودي)", "ar-SA-ZariyahNeural": "زارية (سعودية)",
    "ar-AE-FatimaNeural": "فاطمة (إماراتي)", "ar-AE-HamdanNeural": "حمدان (إماراتي)",
    "ar-KW-FahedNeural": "فهد (كويتي)", "ar-KW-NouraNeural": "نورة (كويتية)",
    "ar-JO-SanaNeural": "سناء (أردني)", "ar-JO-TaimNeural": "تيم (أردني)",
}


# ---------- audio helpers ----------
def _pcm_to_wav(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm[: len(pcm) // 2 * 2])
    return out.getvalue()


def _to_wav(path: Path) -> bytes:
    from app.video.renderer import ffmpeg_exe

    r = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE),
                        "-f", "s16le", "-"], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise VoiceError(f"تعذّر تحويل الصوت: {r.stderr.decode(errors='replace')[-200:]}")
    return _pcm_to_wav(r.stdout)


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
    return _pcm_to_wav(bytes(frames))


# ---------- `python -m app.video.voice` : list ElevenLabs voices of your account ----------
def list_elevenlabs_voices(http: httpx.Client | None = None) -> list[dict]:
    client = http or httpx.Client(timeout=30)
    r = client.get(f"{ELEVEN_API}/voices", headers={"xi-api-key": settings.elevenlabs_api_key})
    if r.status_code == 401:
        raise VoiceError("مفتاح ElevenLabs غير صحيح أو لا يملك صلاحية voices_read")
    r.raise_for_status()
    return [{"voice_id": v.get("voice_id"), "name": v.get("name"),
             "labels": v.get("labels") or {}} for v in r.json().get("voices", [])]


if __name__ == "__main__":
    if not settings.elevenlabs_api_key:
        print("ELEVENLABS_API_KEY is empty in .env")
    else:
        try:
            voices = list_elevenlabs_voices()
        except Exception as exc:  # noqa: BLE001
            print("Could not list voices:", exc)
        else:
            print("Voices in your ElevenLabs account (copy the voice_id you want):")
            for v in voices:
                labels = ", ".join(f"{k}={val}" for k, val in v["labels"].items())
                print(f"  {v['voice_id']}  {v['name']}  {labels}")
