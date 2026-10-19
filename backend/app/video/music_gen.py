"""Generated background music: a short, royalty-free bed made by code (chord pad, bass, arpeggio, soft beat).

Used when no music track is available, so every video has a musical bed under the voice. The style follows the
video theme (calm = slow pad, energetic = faster with a firmer beat). Same seed -> same track.
"""
import array
import io
import math
import random
import wave

SR = 22050  # enough for a background bed; FFmpeg resamples it for the final mix
STYLES = {  # bpm, kick, arpeggio, hats
    "calm": (72, 0.0, 0.05, 0.0),
    "modern": (96, 0.45, 0.07, 0.0),
    "energetic": (118, 0.8, 0.08, 0.025),
    "corporate": (88, 0.3, 0.06, 0.0),
}
PROGRESSIONS = [  # semitone offsets of triads from the key root
    [(0, 4, 7), (7, 11, 14), (9, 12, 16), (5, 9, 12)],   # I  V  vi IV
    [(9, 12, 16), (5, 9, 12), (0, 4, 7), (7, 11, 14)],   # vi IV I  V
    [(0, 4, 7), (9, 12, 16), (5, 9, 12), (7, 11, 14)],   # I  vi IV V
]
ROOTS = [220.0, 246.94, 261.63, 293.66]  # A3 B3 C4 D4


def _hz(root: float, semis: int) -> float:
    return root * 2 ** (semis / 12)


def generate(seconds: float, style: str = "modern", seed: int = 0) -> bytes:
    bpm, kick_amt, arp_amt, hat_amt = STYLES.get(style, STYLES["modern"])
    rnd = random.Random(seed)
    prog, root = rnd.choice(PROGRESSIONS), rnd.choice(ROOTS)
    beat = 60 / bpm
    bar = 4 * beat
    n = int(seconds * SR)
    out = array.array("f", bytes(4 * n))
    two_pi = 2 * math.pi
    # pad + bass: per bar, chord tones with slow attack/release
    for b in range(int(seconds / bar) + 1):
        chord = prog[b % len(prog)]
        start, end = int(b * bar * SR), min(n, int((b + 1) * bar * SR))
        freqs = [_hz(root, s) for s in chord]
        bass = _hz(root, chord[0]) / 2
        for i in range(start, end):
            t = (i - start) / SR
            env = min(1.0, t / 0.35) * min(1.0, (bar - t) / 0.4)
            ti = i / SR
            pad = sum(math.sin(two_pi * f * ti) + 0.25 * math.sin(two_pi * 2 * f * ti) for f in freqs)
            tb = t % beat  # bass pulses on each beat
            bs = math.sin(two_pi * bass * ti) * math.exp(-tb * 3.2)
            out[i] += 0.055 * env * pad + 0.16 * bs
    # arpeggio: chord tones an octave up, eighth notes
    step = beat / 2
    if arp_amt:
        for k in range(int(seconds / step)):
            chord = prog[int(k * step / bar) % len(prog)]
            f = _hz(root, chord[k % 3] + 12)
            s0 = int(k * step * SR)
            for i in range(s0, min(n, s0 + int(step * SR))):
                t = (i - s0) / SR
                out[i] += arp_amt * math.exp(-t * 7) * (math.sin(two_pi * f * t) + 0.2 * math.sin(two_pi * 3 * f * t))
    # soft kick on every beat, hats on the off-beats
    for k in range(int(seconds / beat)):
        s0 = int(k * beat * SR)
        if kick_amt:
            ph = 0.0
            for i in range(s0, min(n, s0 + int(0.22 * SR))):
                t = (i - s0) / SR
                ph += two_pi * (45 + 75 * math.exp(-t * 30)) / SR
                out[i] += kick_amt * 0.5 * math.sin(ph) * math.exp(-t * 14)
        if hat_amt:
            h0 = s0 + int(beat / 2 * SR)
            for i in range(h0, min(n, h0 + int(0.05 * SR))):
                out[i] += hat_amt * rnd.uniform(-1, 1) * math.exp(-(i - h0) / SR * 90)
    peak = max(1e-6, max(abs(x) for x in out))
    pcm = array.array("h", (int(max(-1.0, min(1.0, x / peak * 0.8)) * 32767) for x in out))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
