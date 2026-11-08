#!/usr/bin/env python3
"""Render the Theresa still-image MAD from mad/storyboard.json.

    python3 scripts/render.py                      # full video -> output/
    python3 scripts/render.py --stills 12.5 80     # preview frames -> build/
    python3 scripts/render.py --start 70 --end 95  # render a time range

Every shot is timed in bars of the soundtrack (mad/beats.json, produced by
scripts/analyze_beats.py), so cuts and dissolves land on the music's
downbeats.  The look: slow Ken Burns moves, dissolves / dips to black or
white, serif captions, an act marker per chapter and drifting luminous
"dust" (the 微尘 of Theresa's Arts) over a soft vignette.
"""
import argparse
import json
import math
import os
import pathlib
import subprocess
import sys
from functools import lru_cache
from multiprocessing import Pool

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
IMG = ROOT / "assets" / "img"
FONTS = ROOT / "assets" / "fonts"

SB = json.loads((ROOT / "mad" / "storyboard.json").read_text(encoding="utf-8"))
BEATS = json.loads((ROOT / SB["beats"]).read_text())
W, H = SB["size"]
FPS = SB["fps"]
BARS = BEATS["bars"]
BAR_LEN = float(np.median(np.diff(BARS)))

TEXT_RGB = (246, 240, 234)
ACCENT_RGB = (226, 190, 200)  # Theresa's rose
DUST_RGB = np.array([1.0, 0.93, 0.90], np.float32)


# ----------------------------------------------------------------- timing
def bar_time(b):
    """Start time of (1-indexed, possibly fractional) bar b."""
    i = b - 1
    k = int(math.floor(i))
    f = i - k
    if k < 0:
        return BARS[0] + i * BAR_LEN
    if k + 1 < len(BARS):
        return BARS[k] + f * (BARS[k + 1] - BARS[k])
    return BARS[-1] + (i - (len(BARS) - 1)) * BAR_LEN


def smooth(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def build_timeline():
    shots = []
    for s in SB["shots"]:
        s = dict(s)
        s["t0"] = bar_time(s["bar"])
        s["t1"] = bar_time(s["bar"] + s["bars"])
        tin = s.get("in", {})
        s["in_type"] = tin.get("type", "dissolve")
        s["in_dur"] = tin.get("dur", 0.9)
        shots.append(s)
    for a, b in zip(shots, shots[1:]):
        a["out_type"], a["out_dur"] = b["in_type"], b["in_dur"]
    shots[-1]["out_type"], shots[-1]["out_dur"] = "black", 0.0
    music_end = shots[-1]["t1"]
    credits = {"t0": music_end, "t1": music_end + SB["credits_seconds"]}
    return shots, credits


SHOTS, CREDITS = build_timeline()
TOTAL = CREDITS["t1"]


# ----------------------------------------------------------------- images
def load_rgb(name):
    im = Image.open(IMG / name)
    if im.mode == "RGBA":
        bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
        bg.alpha_composite(im)
        im = bg
    return np.asarray(im.convert("RGB"), np.uint8)


def grade(arr, kind):
    if not kind:
        return arr
    f = arr.astype(np.float32)
    if kind == "cold":
        g = f.mean(2, keepdims=True)
        f = 0.65 * f + 0.35 * g
        f *= np.array([0.94, 0.98, 1.06], np.float32)
    return np.clip(f, 0, 255).astype(np.uint8)


@lru_cache(maxsize=48)
def source(key):
    """key = image name, or 'sprite|<sprite>|<bg>|<h>|<x>'."""
    if not key.startswith("sprite|"):
        return load_rgb(key)
    _, spr, bgname, sh, sx = key.split("|")
    sh, sx = float(sh), float(sx)
    CW, CH = int(W * 1.25), int(H * 1.25)
    bg = Image.fromarray(load_rgb(bgname))
    s = max(CW / bg.width, CH / bg.height) * 1.1
    bg = bg.resize((int(bg.width * s), int(bg.height * s)), Image.LANCZOS)
    bg = bg.crop(((bg.width - CW) // 2, (bg.height - CH) // 2,
                  (bg.width - CW) // 2 + CW, (bg.height - CH) // 2 + CH))
    bg = bg.filter(ImageFilter.GaussianBlur(28))
    bga = np.asarray(bg, np.float32) * 0.42
    # soft dark pool behind the figure so the cut-out reads clearly
    yy, xx = np.mgrid[0:CH, 0:CW].astype(np.float32)
    d = ((xx - sx * CW) / (0.32 * CW)) ** 2 + ((yy - 0.55 * CH) / (0.6 * CH)) ** 2
    bga *= (0.55 + 0.45 * np.clip(d, 0, 1))[..., None]
    canvas = Image.fromarray(np.clip(bga, 0, 255).astype(np.uint8)).convert("RGBA")
    sp = Image.open(IMG / spr).convert("RGBA")
    bbox = sp.getchannel("A").getbbox()
    sp = sp.crop(bbox)
    th = int(CH * sh * 0.92)
    tw = int(sp.width * th / sp.height)
    if tw > CW * 0.95:
        tw = int(CW * 0.95)
        th = int(sp.height * tw / sp.width)
    sp = sp.resize((tw, th), Image.LANCZOS)
    # faint rose glow around the silhouette
    glow = sp.getchannel("A").filter(ImageFilter.GaussianBlur(40))
    glow_layer = Image.new("RGBA", sp.size, ACCENT_RGB + (0,))
    glow_layer.putalpha(glow.point(lambda v: int(v * 0.35)))
    px = int(sx * CW - tw / 2)
    py = int(CH - th - CH * 0.03)
    canvas.alpha_composite(glow_layer, (px, py))
    canvas.alpha_composite(sp, (px, py))
    return np.asarray(canvas.convert("RGB"), np.uint8)


def shot_key(s, img=None):
    if "sprite" in s:
        return f"sprite|{s['sprite']}|{s['bg']}|{s.get('sprite_h', 1.0)}|{s.get('sprite_x', 0.5)}"
    return img or s["img"]


def camera(src, z, cx, cy):
    sh_, sw_ = src.shape[:2]
    s = max(W / sw_, H / sh_) * z
    hw, hh = W / (2 * s), H / (2 * s)
    cx = min(max(cx * sw_, hw), sw_ - hw)
    cy = min(max(cy * sh_, hh), sh_ - hh)
    M = np.float32([[s, 0, W / 2 - s * cx], [0, s, H / 2 - s * cy]])
    return cv2.warpAffine(src, M, (W, H), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REFLECT)


def render_shot(s, t):
    p = (t - s["t0"]) / (s["t1"] - s["t0"])  # may run past [0,1] in transitions
    z0, z1 = s.get("z", [1.0, 1.08])
    (x0, y0), (x1, y1) = s.get("c", [[0.5, 0.5], [0.5, 0.5]])
    z = z0 + (z1 - z0) * p
    cx = x0 + (x1 - x0) * p
    cy = y0 + (y1 - y0) * p
    if s.get("card"):
        return np.zeros((H, W, 3), np.float32)
    frame = camera(source(shot_key(s)), z, cx, cy).astype(np.float32)
    th = s.get("then")
    if th:
        tc = s["t0"] + th["at"] * (s["t1"] - s["t0"])
        w = smooth((t - (tc - th["dur"] / 2)) / th["dur"])
        if w > 0:
            f2 = camera(source(th["img"]), z, cx, cy).astype(np.float32)
            frame = frame * (1 - w) + f2 * w
    if s.get("grade"):
        frame = grade(frame.astype(np.uint8), s["grade"]).astype(np.float32)
    if s.get("dim"):
        frame *= s["dim"]
    return frame


def compose_shots(t):
    """Blend the shots active at time t according to their transitions."""
    idx = next((i for i, s in enumerate(SHOTS) if s["t0"] <= t < s["t1"]), None)
    if idx is None:
        return np.zeros((H, W, 3), np.float32)
    cur = SHOTS[idx]
    # incoming transition (near t0) or outgoing (near t1)?
    if idx > 0 and t - cur["t0"] < cur["in_dur"] / 2:
        a, b, tb = SHOTS[idx - 1], cur, cur["t0"]
    elif idx + 1 < len(SHOTS) and cur["t1"] - t < cur["out_dur"] / 2:
        a, b, tb = cur, SHOTS[idx + 1], cur["t1"]
    else:
        out = render_shot(cur, t)
        if idx == 0 and cur["in_type"] == "black":
            out *= smooth(t / cur["in_dur"])
        return out
    kind, d = b["in_type"], b["in_dur"]
    u = (t - (tb - d / 2)) / d  # 0..1 across the transition
    if kind == "dissolve":
        w = smooth(u)
        return render_shot(a, t) * (1 - w) + render_shot(b, t) * w
    if kind == "black":
        if u < 0.5:
            return render_shot(a, t) * smooth(1 - 2 * u)
        return render_shot(b, t) * smooth(2 * u - 1)
    if kind == "white":
        if u < 0.3:
            f, w = render_shot(a, t), smooth(u / 0.3)
        else:
            f, w = render_shot(b, t), smooth(1 - (u - 0.3) / 0.7)
        return f * (1 - w) + 255.0 * w
    return render_shot(b if u >= 0.5 else a, t)


# ----------------------------------------------------------------- text
@lru_cache(maxsize=16)
def font(name, size, weight=None):
    f = ImageFont.truetype(str(FONTS / name), size)
    if weight:
        f.set_variation_by_axes([weight])
    return f


def track(txt, i, tr):
    """Letter-spacing after txt[i]; dashes and ellipses stay joined."""
    if i == len(txt) - 1 or (txt[i] in "—…" and txt[i + 1] == txt[i]):
        return 0
    return tr


def text_layer(lines, specs, align="center", pad=40):
    """lines: list of (text, font, rgb, tracking, gap_after).  -> RGBA ndarray."""
    widths, heights = [], []
    for txt, f, _, tr, _ in lines:
        w = sum(f.getlength(ch) + track(txt, i, tr) for i, ch in enumerate(txt))
        asc, desc = f.getmetrics()
        widths.append(w)
        heights.append(asc + desc)
    tw = int(max(widths) + 2 * pad)
    th = int(sum(heights) + sum(g for *_, g in lines) + 2 * pad)
    img = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    mask = Image.new("L", (tw, th), 0)
    d, dm = ImageDraw.Draw(img), ImageDraw.Draw(mask)
    y = pad
    for (txt, f, rgb, tr, gap), w, h in zip(lines, widths, heights):
        x = pad + ((max(widths) - w) / 2 if align == "center" else 0)
        for i, ch in enumerate(txt):
            d.text((x, y), ch, font=f, fill=rgb + (255,))
            dm.text((x, y), ch, font=f, fill=255)
            x += f.getlength(ch) + track(txt, i, tr)
        y += h + gap
    if specs.get("grow"):
        mask_g = mask.filter(ImageFilter.MaxFilter(specs["grow"]))
    else:
        mask_g = mask
    shadow = mask_g.filter(ImageFilter.GaussianBlur(specs.get("blur", 9)))
    sh = np.asarray(shadow, np.float32) / 255 * specs.get("shadow", 0.85)
    fg = np.asarray(img, np.float32)
    a = fg[..., 3:] / 255
    rgb = fg[..., :3] * a  # premultiplied text over a dark soft halo
    alpha = np.maximum(a[..., 0], np.clip(sh * 1.6, 0, 1))
    return np.dstack([rgb, alpha]).astype(np.float32)


@lru_cache(maxsize=96)
def caption_layer(text, who):
    f = font("NotoSerifSC-Regular.otf", 40)
    lines = [(ln, f, TEXT_RGB, 3, 14) for ln in text.split("\n")]
    if who:
        lines[-1] = lines[-1][:4] + (20,)
        lines.append(("—— " + who, font("NotoSerifSC-Regular.otf", 24), ACCENT_RGB, 4, 0))
    return text_layer(lines, {"blur": 12, "shadow": 0.75, "grow": 9})


@lru_cache(maxsize=1)
def caption_band():
    y = np.arange(H, dtype=np.float32)
    g = np.clip((y - H * 0.58) / (H * 0.42), 0, 1)
    return (g * g * (3 - 2 * g))[:, None, None]


@lru_cache(maxsize=16)
def act_layer(no, name, en):
    lines = [(no, font("CormorantGaramond.ttf", 64, 500), ACCENT_RGB, 6, 2),
             (name, font("NotoSerifSC-SemiBold.otf", 38), TEXT_RGB, 10, 10),
             (en, font("CormorantGaramond.ttf", 22, 500), (205, 198, 194), 7, 0)]
    return text_layer(lines, {"blur": 8, "shadow": 0.8}, align="left")


@lru_cache(maxsize=4)
def title_layer():
    lines = [(SB["title_en"], font("CormorantGaramond.ttf", 40, 500), ACCENT_RGB, 18, 18),
             (SB["title"], font("NotoSerifSC-SemiBold.otf", 116), TEXT_RGB, 26, 26),
             (SB["subtitle"], font("NotoSerifSC-Regular.otf", 28), (220, 212, 206), 6, 0)]
    return text_layer(lines, {"blur": 14, "shadow": 0.9})


@lru_cache(maxsize=4)
def ending_layers():
    quote = text_layer([("博士，累了吗？那就休息吧。", font("NotoSerifSC-Regular.otf", 40), TEXT_RGB, 4, 16),
                        ("每个人都有平静入梦的权利，你也一样。", font("NotoSerifSC-Regular.otf", 40), TEXT_RGB, 4, 22),
                        ("—— 魔王", font("NotoSerifSC-Regular.otf", 24), ACCENT_RGB, 4, 0)],
                       {"blur": 10})
    title = text_layer([(SB["title"], font("NotoSerifSC-SemiBold.otf", 72), TEXT_RGB, 22, 14),
                        (SB["title_en"], font("CormorantGaramond.ttf", 30, 500), ACCENT_RGB, 16, 0)],
                       {"blur": 12})
    return quote, title


@lru_cache(maxsize=2)
def credits_layer():
    f = font("NotoSerifSC-Regular.otf", 27)
    fs = font("NotoSerifSC-Regular.otf", 22)
    g = (214, 206, 200)
    lines = [
        ("制作说明", font("NotoSerifSC-SemiBold.otf", 34), ACCENT_RGB, 10, 30),
        ("素材　《明日方舟》剧情CG / 干员立绘 © Hypergryph（鹰角网络）", f, TEXT_RGB, 2, 6),
        ("来源：SideStory「巴别塔」、主线第十四章「慈悲灯塔」、干员「魔王」  经 PRTS 维基整理", fs, g, 1, 26),
        ("音乐　\"Gymnopedie No. 1\" — Erik Satie, performed by Kevin MacLeod (incompetech.com)", f, TEXT_RGB, 1, 6),
        ("Licensed under Creative Commons: By Attribution 4.0 License  creativecommons.org/licenses/by/4.0/", fs, g, 1, 26),
        ("字体　Noto Serif SC · Cormorant Garamond（SIL Open Font License）", f, TEXT_RGB, 2, 30),
        ("非商业同人作品，仅供交流欣赏。", fs, ACCENT_RGB, 4, 0),
    ]
    return text_layer(lines, {"blur": 8})


def paste(frame, layer, x, y, opacity):
    if opacity <= 0.001:
        return
    lh, lw = layer.shape[:2]
    x, y = int(round(x)), int(round(y))
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + lw, W), min(y + lh, H)
    if x1 <= x0 or y1 <= y0:
        return
    L = layer[y0 - y:y1 - y, x0 - x:x1 - x]
    a = L[..., 3:4] * opacity
    region = frame[y0:y1, x0:x1]
    region *= (1 - a)
    region += L[..., :3] * opacity


def window(t, a, b, fin=0.7, fout=0.6):
    if t < a or t > b:
        return 0.0
    return smooth((t - a) / fin) * smooth((b - t) / fout)


def caption_opacity(s, c, t):
    a_, b_ = c.get("at", [0, s["bars"]])
    ta = bar_time(s["bar"] + a_) + 0.35
    tb = bar_time(s["bar"] + b_) - 0.3
    return window(t, ta, tb), ta


def overlays(frame, t):
    # darken the lower part of the frame while a caption is on screen
    band = max((caption_opacity(s, c, t)[0] for s in SHOTS
                if s["t0"] - 1 <= t <= s["t1"] + 1 for c in s.get("caps", [])), default=0)
    if band > 0:
        frame *= 1 - 0.58 * band * caption_band()
    for s in SHOTS:
        if not (s["t0"] - 1 <= t <= s["t1"] + 1):
            continue
        for c in s.get("caps", []):
            op, ta = caption_opacity(s, c, t)
            if op > 0:
                L = caption_layer(c["text"], c.get("who", ""))
                rise = 10 * (1 - smooth((t - ta) / 0.9))
                paste(frame, L, (W - L.shape[1]) / 2, H - 70 - L.shape[0] + rise, op)
        if "act" in s:
            ac = s["act"]
            op = window(t, s["t0"] + 0.5, s["t0"] + 4.6, 1.0, 1.0)
            if op > 0:
                L = act_layer(ac["no"], ac["name"], ac["en"])
                slide = 14 * (1 - smooth((t - s["t0"] - 0.5) / 1.2))
                paste(frame, L, 70 - slide, 50, op)
        if s.get("title"):
            op = window(t, 2.2, s["t1"] - 0.5, 1.6, 1.0)
            if op > 0:
                L = title_layer()
                paste(frame, L, (W - L.shape[1]) / 2, (H - L.shape[0]) / 2 - 20, op)
        if s.get("card") == "ending":
            q, ti = ending_layers()
            op_q = window(t, s["t0"] + 0.4, s["t0"] + 4.2, 1.2, 0.9)
            paste(frame, q, (W - q.shape[1]) / 2, (H - q.shape[0]) / 2, op_q)
            op_t = window(t, s["t0"] + 4.0, s["t1"] - 0.2, 1.2, 1.4)
            paste(frame, ti, (W - ti.shape[1]) / 2, (H - ti.shape[0]) / 2, op_t)
    if t >= CREDITS["t0"]:
        L = credits_layer()
        op = window(t, CREDITS["t0"] + 0.6, CREDITS["t1"] - 0.3, 1.2, 1.3)
        paste(frame, L, (W - L.shape[1]) / 2, (H - L.shape[0]) / 2, op)


# ----------------------------------------------------------------- effects
@lru_cache(maxsize=1)
def vignette():
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / math.sqrt(2)
    v = 1 - 0.42 * np.clip(r, 0, 1) ** 2.2
    # slight darkening at the bottom so captions stay legible
    v *= 1 - 0.18 * np.clip((yy - H * 0.72) / (H * 0.28), 0, 1) ** 1.5
    return v[..., None]


@lru_cache(maxsize=8)
def dot(radius):
    r = int(math.ceil(radius * 3))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1].astype(np.float32)
    g = np.exp(-(xx ** 2 + yy ** 2) / (2 * radius ** 2))
    core = np.exp(-(xx ** 2 + yy ** 2) / (2 * (radius * 0.35) ** 2))
    return (0.6 * g + 0.8 * core)[..., None] * DUST_RGB


rng = np.random.default_rng(11)
N_DUST = 70
DUST = {
    "x": rng.uniform(0, 1, N_DUST), "y": rng.uniform(0, 1, N_DUST),
    "vx": rng.normal(0.004, 0.006, N_DUST), "vy": rng.uniform(-0.022, -0.006, N_DUST),
    "amp": rng.uniform(0.004, 0.02, N_DUST), "freq": rng.uniform(0.1, 0.35, N_DUST),
    "ph": rng.uniform(0, 2 * np.pi, N_DUST),
    "r": rng.choice([1.2, 1.8, 2.6, 3.6, 5.0], N_DUST, p=[0.3, 0.3, 0.2, 0.13, 0.07]),
    "b": rng.uniform(40, 150, N_DUST),
}


def loudness(t):
    i = int(t / BEATS["rms_hop"])
    rms = BEATS["rms"]
    return rms[i] if 0 <= i < len(rms) else 0.0


def dust(frame, t, strength):
    D = DUST
    x = (D["x"] + D["vx"] * t + D["amp"] * np.sin(D["freq"] * t + D["ph"])) % 1.0
    y = (D["y"] + D["vy"] * t) % 1.0
    tw = 0.55 + 0.45 * np.sin(2.1 * D["freq"] * t * 3 + D["ph"] * 1.7)
    for k in range(N_DUST):
        spr = dot(float(D["r"][k]))
        r = spr.shape[0] // 2
        cx, cy = int(x[k] * (W + 2 * r)) - r, int(y[k] * (H + 2 * r)) - r
        ox, oy = cx - r, cy - r  # sprite's top-left corner in frame coords
        x0, y0 = max(ox, 0), max(oy, 0)
        x1, y1 = min(ox + spr.shape[1], W), min(oy + spr.shape[0], H)
        if x1 <= x0 or y1 <= y0:
            continue
        frame[y0:y1, x0:x1] += spr[y0 - oy:y1 - oy, x0 - ox:x1 - ox] * (D["b"][k] * tw[k] * strength)


def render_frame(n):
    t = n / FPS
    if t >= CREDITS["t0"]:
        frame = np.zeros((H, W, 3), np.float32)
    else:
        frame = compose_shots(t)
    frame *= vignette()
    fade_end = smooth((TOTAL - t) / 1.0)
    strength = (0.55 + 1.1 * loudness(t)) * smooth(t / 2.5) * fade_end
    dust(frame, t, strength)
    overlays(frame, t)
    return np.clip(frame, 0, 255).astype(np.uint8)


# ----------------------------------------------------------------- main
META = ["-metadata", "title=平静入梦 — 特蕾西娅 静止画MAD",
        "-metadata", "comment=Fan work. Arknights assets (c) Hypergryph. Music: Gymnopedie No. 1 "
                     "by Kevin MacLeod (incompetech.com), CC BY 4.0."]


def run(cmd):
    if subprocess.run(cmd).returncode != 0:
        raise SystemExit("ffmpeg failed: " + " ".join(cmd[:6]))


def render_lossless(n0, n1, path, workers):
    """Render frames once into a lossless intermediate (video only)."""
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv444p", str(path)]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    batch = FPS * 4  # bounded batches: frames never pile up in memory faster than x264 eats them
    with Pool(workers) as pool:
        for b0 in range(n0, n1, batch):
            for fr in pool.map(render_frame, range(b0, min(b0 + batch, n1)), chunksize=4):
                ff.stdin.write(fr.tobytes())
            if (b0 - n0) % (FPS * 20) == 0:
                print(f"  frame {b0}/{n1}  t={b0 / FPS:6.1f}s", file=sys.stderr, flush=True)
    ff.stdin.close()
    if ff.wait() != 0:
        raise SystemExit("ffmpeg failed (intermediate)")


def encode(n0, n1, out, workers, target_mb):
    """Render, then encode H.264/AAC.  With target_mb > 0 a two-pass encode
    keeps the whole file under that size (many upload / preview paths cut
    files above ~50 MB); target_mb = 0 uses constant quality instead."""
    music = ROOT / SB["music"]
    t0, dur = n0 / FPS, (n1 - n0) / FPS
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    inter = build / "intermediate.mkv"
    render_lossless(n0, n1, inter, workers)
    audio_in = ["-ss", f"{t0:.3f}", "-i", str(music), "-filter_complex",
                f"[1:a]apad,atrim=0:{dur:.3f},afade=t=out:st={max(dur - 0.5, 0):.3f}:d=0.5[a]",
                "-map", "0:v", "-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
    common = ["-c:v", "libx264", "-preset", "slow", "-tune", "film", "-pix_fmt", "yuv420p",
              "-g", str(FPS * 4)]
    if target_mb <= 0:
        run(["ffmpeg", "-y", "-v", "error", "-stats", "-i", str(inter), *audio_in, *common,
             "-crf", "22", "-movflags", "+faststart", *META, str(out)])
        return
    # bits for video = budget - audio (160k) - ~1.5 % container overhead
    vbr = int(target_mb * 8e6 / dur * 0.985 - 160e3)
    print(f"  two-pass target {target_mb} MB -> video {vbr / 1e3:.0f} kb/s", file=sys.stderr)
    rate = ["-b:v", str(vbr), "-maxrate", str(int(vbr * 2.5)), "-bufsize", str(int(vbr * 5)),
            "-passlogfile", str(build / "x264pass")]
    run(["ffmpeg", "-y", "-v", "error", "-i", str(inter), *common, *rate, "-pass", "1",
         "-an", "-f", "null", "/dev/null"])
    run(["ffmpeg", "-y", "-v", "error", "-stats", "-i", str(inter), *audio_in, *common, *rate,
         "-pass", "2", "-movflags", "+faststart", *META, str(out)])


def cuesheet(path):
    def mmss(t):
        return f"{int(t // 60)}:{t % 60:05.2f}"
    rows = ["# 分镜表（由 `python3 scripts/render.py --cuesheet` 生成）", "",
            f"音乐：{pathlib.Path(SB['music']).name}，{BEATS['tempo_bpm']} BPM，3/4 拍，"
            f"每小节约 {BAR_LEN:.2f} 秒；视频总长 {mmss(TOTAL)}。", "",
            "| # | 小节 | 时间 | 画面 | 转场 | 字幕 |", "| --- | --- | --- | --- | --- | --- |"]
    for i, s in enumerate(SHOTS, 1):
        pic = s.get("img") or (f"{s['sprite']}（合成，背景 {s['bg']}）" if "sprite" in s else "片尾字卡")
        if s.get("then"):
            pic += f" → {s['then']['img']}"
        if s.get("act"):
            pic = f"**{s['act']['no']} {s['act']['name']}** · " + pic
        caps = " / ".join(c["text"].replace("\n", "") + (f"（{c['who']}）" if c.get("who") else "")
                          for c in s.get("caps", []))
        if s.get("title"):
            caps = f"片名「{SB['title']}」"
        if s.get("card"):
            caps = "博士，累了吗？那就休息吧。每个人都有平静入梦的权利，你也一样。（魔王）"
        rows.append(f"| {i} | {s['bar']}–{s['bar'] + s['bars'] - 1} | {mmss(s['t0'])}–{mmss(s['t1'])} "
                    f"| {pic} | {s['in_type']} | {caps} |")
    rows.append(f"| — | — | {mmss(CREDITS['t0'])}–{mmss(CREDITS['t1'])} | 制作说明 | black | 素材 / 音乐 / 字体署名 |")
    pathlib.Path(path).write_text("\n".join(rows) + "\n", encoding="utf-8")
    print("cuesheet ->", path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuesheet", action="store_true", help="write docs/分镜表.md and exit")
    ap.add_argument("--stills", nargs="*", type=float, help="render preview stills at these times")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--out", default=str(ROOT / "output" / "Theresa_MAD_平静入梦.mp4"))
    ap.add_argument("--target-mb", type=float, default=40.0,
                    help="two-pass encode to this file size (0 = constant quality, larger file)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args()
    print(f"timeline: {len(SHOTS)} shots, music ends {CREDITS['t0']:.2f}s, total {TOTAL:.2f}s")
    if a.cuesheet:
        cuesheet(ROOT / "docs" / "分镜表.md")
        return
    if a.stills is not None:
        d = ROOT / "build"
        d.mkdir(exist_ok=True)
        for t in a.stills:
            Image.fromarray(render_frame(int(round(t * FPS)))).save(d / f"still_{t:07.2f}.jpg", quality=90)
        print("stills ->", d)
        return
    n0 = int(round(a.start * FPS))
    n1 = int(round((a.end if a.end is not None else TOTAL) * FPS))
    pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    encode(n0, n1, a.out, a.workers, a.target_mb)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
