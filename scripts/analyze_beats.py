#!/usr/bin/env python3
"""Beat / bar analysis for the soundtrack.

Writes mad/beats.json with the beat grid, bar downbeats (3/4) and a loudness
curve so the storyboard can cut on bar lines.  Pure numpy/scipy:
spectral-flux onset envelope -> tempo by autocorrelation -> dynamic
programming beat tracker (Ellis 2007) -> downbeat phase from bass onsets.
"""
import json
import pathlib
import subprocess
import sys

import numpy as np
from scipy.signal import find_peaks

ROOT = pathlib.Path(__file__).resolve().parent.parent
SR, HOP, NFFT = 22050, 256, 2048


def load(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1",
                          "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def stft_mag(x):
    win = np.hanning(NFFT).astype(np.float32)
    n = 1 + (len(x) - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    return np.abs(np.fft.rfft(x[idx] * win, axis=1))


def main(audio):
    x = load(audio)
    dur = len(x) / SR
    S = np.log1p(100 * stft_mag(x))
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    flux = np.maximum(np.diff(S, axis=0), 0)
    env = np.r_[0, flux.sum(1)]
    bass = np.r_[0, flux[:, freqs < 220].sum(1)]
    fps = SR / HOP
    env = (env - env.mean()) / (env.std() + 1e-9)

    # tempo: autocorrelation of the onset envelope, prior around 77 bpm
    ac = np.correlate(env, env, "full")[len(env) - 1:]
    lags = np.arange(len(ac)) / fps
    bpm = 60 / np.maximum(lags, 1e-9)
    prior = np.exp(-0.5 * (np.log2(bpm / 77.0) / 0.4) ** 2)
    ok = (bpm > 40) & (bpm < 200)
    lag = int(np.argmax(np.where(ok, ac * prior, -np.inf)))
    period = lag  # frames per beat

    # DP beat tracking
    tight = 100.0
    score = env.copy()
    back = np.full(len(env), -1)
    for t in range(len(env)):
        lo, hi = t - 2 * period, t - period // 2
        if hi <= 0:
            continue
        prev = np.arange(max(lo, 0), hi)
        pen = -tight * np.log((t - prev) / period) ** 2
        k = np.argmax(score[prev] + pen)
        score[t] = env[t] + score[prev][k] + pen[k]
        back[t] = prev[k]
    t = int(np.argmax(score[-2 * period:])) + len(env) - 2 * period
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    beats = np.array(beats[::-1])
    bt = beats / fps

    # downbeat: phase (mod 3) with strongest bass onsets
    bstr = np.array([bass[max(b - 2, 0):b + 3].max() for b in beats])
    phase = int(np.argmax([bstr[p::3].mean() for p in range(3)]))
    bars = bt[phase::3]

    # loudness (RMS, 0.25 s) for visual dynamics
    w = int(0.25 * SR)
    rms = np.sqrt(np.convolve(x ** 2, np.ones(w) / w, "same"))[::w // 2]
    rms = (rms / rms.max()).round(4).tolist()

    out = {
        "audio": str(pathlib.Path(audio).name),
        "duration": round(dur, 3),
        "tempo_bpm": round(60 * fps / period, 2),
        "beats": [round(b, 3) for b in bt],
        "downbeat_phase": phase,
        "bars": [round(b, 3) for b in bars],
        "rms_hop": round(w / 2 / SR, 4),
        "rms": rms,
    }
    dest = ROOT / "mad" / "beats.json"
    dest.write_text(json.dumps(out, indent=1))
    print(f"duration {dur:.2f}s tempo {out['tempo_bpm']} bpm beats {len(bt)} "
          f"bars {len(bars)} first bar {bars[0]:.2f}s phase {phase}")
    first_sound = np.argmax(np.abs(x) > 0.01) / SR
    print("first sound at", round(first_sound, 3))
    ib = np.diff(bars)
    print("bar len mean %.3f sd %.3f min %.3f max %.3f" % (ib.mean(), ib.std(), ib.min(), ib.max()))
    print("bars:", " ".join(f"{b:.2f}" for b in bars))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else
         ROOT / "assets" / "music" / "gymnopedie_no1_macleod.mp3")
