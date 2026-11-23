"""Make the students' murmur for the projector: real speech, mixed until no word stands out.

A text-to-speech model reads short everyday lines in several voices. The clips are spread over a loop,
each speaker at its own distance (quieter and duller further away), with a little room echo, and the
end of the loop wraps onto its start so it repeats without a seam.

    python scripts/make_murmur.py          # needs OPENAI_API_KEY and ffmpeg

The clips are cached in runs/murmur/, so a second run only mixes. Output: shelflife/static/audio/murmur.mp3
"""
from __future__ import annotations

import io
import os
import random
import subprocess
import wave
from pathlib import Path

import numpy as np
from scipy.signal import butter, fftconvolve, sosfilt

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "runs" / "murmur"
OUT = ROOT / "shelflife" / "static" / "audio" / "murmur.mp3"
RATE = 24000
LOOP = 48.0  # seconds
SPEAKERS = 10

VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"]
STYLE = ("Speak casually and fairly quietly, like a student chatting with a friend in a lecture hall before the "
         "lecture starts. Natural, relaxed, mid-conversation, a little fast, not performing for anyone.")
LINES = [
    "Hast du die Folien von letzter Woche schon runtergeladen?",
    "Ich hab heute echt noch gar nichts gegessen, ich sterbe.",
    "Wo sitzen eigentlich die anderen, hast du die gesehen?",
    "Nee, das war doch erst nächsten Dienstag, oder?",
    "Ja, genau, und dann meinte sie, das reicht noch bis Freitag.",
    "Gehst du nachher noch mit in die Mensa?",
    "Keine Ahnung, ich glaub, das wird heute wieder voll lang.",
    "Mein Akku ist fast leer, hast du ein Ladekabel dabei?",
    "Ach, das ist doch dieses Spiel mit dem Laden, oder?",
    "Wir müssen das Protokoll noch fertig machen, bis Donnerstag.",
    "Echt? Das wusste ich gar nicht, wer hat das gesagt?",
    "Ich hab gestern bis zwei Uhr an dem Bericht gesessen.",
    "Okay, warte, ich schick dir das gleich rüber.",
    "Did you get the email about the group work?",
    "I think it's the same room as last time, right?",
    "No, I haven't started yet, honestly.",
    "Wait, are we supposed to bring our laptops today?",
    "Yeah, that's what I said, but nobody listened.",
    "Do you want to grab a coffee after this?",
    "Oh no, I totally forgot about that.",
    "Mhm, yeah, I know what you mean.",
    "It's so cold in here again today.",
    "Haha, nee, oder? Das ist ja witzig.",
    "Und, wie war dein Wochenende?",
    "Ich glaub, ich nehm nachher den Bus um halb.",
    "Hast du schon gesehen, was wir für die Klausur lernen müssen?",
]


def tts(text: str, voice: str) -> np.ndarray:
    from openai import OpenAI

    client = OpenAI()
    for model in ("gpt-4o-mini-tts", "tts-1"):
        try:
            kw = {"instructions": STYLE} if model != "tts-1" else {}
            r = client.audio.speech.create(model=model, voice=voice, input=text, response_format="wav", **kw)
            break
        except Exception as e:  # an older account may only have tts-1
            print(f"  {model}: {e}")
    else:
        raise SystemExit("no text-to-speech model answered")
    with wave.open(io.BytesIO(r.read())) as w:
        assert w.getframerate() == RATE and w.getsampwidth() == 2, (w.getframerate(), w.getsampwidth())
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        return a.reshape(-1, w.getnchannels()).mean(axis=1)


def clip(i: int, text: str, voice: str) -> np.ndarray:
    path = CACHE / f"{i:02d}-{voice}.npy"
    if not path.exists():
        print(f"speaking {i:02d} ({voice})")
        a = tts(text, voice)
        nz = np.flatnonzero(np.abs(a) > 0.01)  # trim the silence around the line
        a = a[max(0, nz[0] - 240): nz[-1] + 480] if len(nz) else a
        np.save(path, a)
    return np.load(path)


def main() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)
    voices = VOICES[:]
    rng.shuffle(voices)
    # every line once in two voices, so each speaker has a few things to say
    clips: dict[str, list[np.ndarray]] = {v: [] for v in voices}
    for i, line in enumerate(LINES):
        for v in (voices[i % len(voices)], voices[(i + 5) % len(voices)]):
            clips[v].append(clip(i * 2 + (v != voices[i % len(voices)]), line, v))

    n = int(LOOP * RATE)
    mix = np.zeros(n, dtype=np.float32)
    for s in range(SPEAKERS):
        v = voices[s % len(voices)]
        near = rng.uniform(0.0, 1.0)  # 1 is a neighbour, 0 the back of the room
        gain = 0.25 + 0.75 * near ** 1.5
        lp = butter(2, 1800 + 4200 * near, "low", fs=RATE, output="sos")
        track = np.zeros(n + RATE * 10, dtype=np.float32)
        t = int(rng.uniform(0, 4) * RATE)
        pool = clips[v][:]
        while t < n:
            rng.shuffle(pool)
            for a in pool:
                if t >= n:
                    break
                track[t:t + len(a)] += a
                t += len(a) + int(rng.uniform(0.25, 1.6) * RATE)  # the friend answers in the gaps
        track = sosfilt(lp, track) * gain
        mix += track[:n]
        mix[:len(track) - n] += track[n:]  # wrap the tail onto the start: the loop has no seam

    # a mid-sized room: decaying noise as the echo, folded the same way
    ir_len = int(0.9 * RATE)
    ir = np.random.default_rng(3).standard_normal(ir_len).astype(np.float32) * np.exp(-np.arange(ir_len) / (0.16 * RATE))
    ir = sosfilt(butter(2, 3500, "low", fs=RATE, output="sos"), ir)
    ir /= np.sqrt((ir ** 2).sum())
    wet = fftconvolve(np.concatenate([mix, mix[:ir_len]]), ir)[ir_len:ir_len + n]
    mix = 0.8 * mix + 0.45 * wet
    mix = sosfilt(butter(1, 120, "high", fs=RATE, output="sos"), mix)

    mix *= 0.12 / np.sqrt((mix ** 2).mean())  # about -18 dBFS
    mix = np.tanh(mix * 1.4) / 1.4  # round off the rare peaks
    pcm = (np.clip(mix, -1, 1) * 32767).astype(np.int16)
    raw = CACHE / "murmur.wav"
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(pcm.tobytes())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-ac", "1", "-b:a", "80k", str(OUT)], check=True)
    print(f"{OUT} ({OUT.stat().st_size // 1024} KB, {LOOP:.0f} s)")


if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set")
    main()
