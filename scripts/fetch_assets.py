#!/usr/bin/env python3
"""Download the source materials for the Theresa still-image MAD.

Images come from PRTS wiki (prts.wiki), which mirrors in-game assets of
Arknights (© Hypergryph / Yostar).  They are fetched on demand and kept out of
git (see .gitignore): this repository only ships the scripts, the storyboard
and the finished fan video.

Music: "Gymnopedie No. 1" — Erik Satie, performed by Kevin MacLeod
(incompetech.com), licensed under CC BY 4.0.

Usage:  python3 scripts/fetch_assets.py            # downloads into assets/
"""
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
UA = "TheresaMAD-fanproject/1.0 (https://github.com/fsynadxy/mad)"

# PRTS file name -> local name
IMAGES = {
    # SideStory「巴别塔」(2024-04-11) story CGs, in narrative order
    **{f"Avg_49_{k}.png": f"babel_{k}.png" for k in [
        "i01", "i02", "i03_1", "i04", "i05", "i06_1", "i06_2", "i21_1",
        "i07", "i15", "i22", "i08", "i09", "i10_1", "i10_2", "i11", "i17",
        "i19", "i21_2", "i18", "i13", "i12", "i16_1", "i16_2", "i14",
        "i20_1", "i20_2"]},
    # Main story EP14「慈悲灯塔」(2024-05-01) story CGs
    **{f"Avg_50_{k}.png": f"ep14_{k}.png" for k in [
        "i02_1", "i02_2", "i19", "i20", "i22", "i23", "i32", "i32_2",
        "i33_1", "i33_2", "i35"]},
    # Operator 魔王 (Civilight Eterna) artwork and story portrait
    "立绘_魔王_1.png": "op_e0.png",
    "立绘_魔王_2.png": "op_e2.png",
    "立绘_魔王_skin1.png": "op_skin_zhuidao.png",
    "Avg_avg_npc_056.png": "avg_theresa.png",
    "活动预告_巴别塔_01.jpg": "babel_banner.jpg",
}

# Fonts (SIL Open Font License 1.1)
FONTS = {
    "NotoSerifSC-Regular.otf":
        "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Serif/SubsetOTF/SC/NotoSerifSC-Regular.otf",
    "NotoSerifSC-SemiBold.otf":
        "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Serif/SubsetOTF/SC/NotoSerifSC-SemiBold.otf",
    "CormorantGaramond.ttf":
        "https://raw.githubusercontent.com/google/fonts/main/ofl/cormorantgaramond/CormorantGaramond%5Bwght%5D.ttf",
}

MUSIC = {
    "gymnopedie_no1_macleod.mp3":
        "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Gymnopedie%20No%201.mp3",
}


def get(url, dest, tries=5):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            dest.write_bytes(data)
            return len(data)
        except Exception as e:  # network hiccups through proxies are common
            wait = 2 ** (k + 1)
            print(f"  retry {k + 1}/{tries} in {wait}s: {e}", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"failed: {url}")


def resolve_prts(names):
    """Ask the PRTS MediaWiki API for the current upload URLs."""
    urls = {}
    names = list(names)
    for i in range(0, len(names), 40):
        chunk = names[i:i + 40]
        titles = "|".join("文件:" + n.replace("_", " ") for n in chunk)
        api = ("https://prts.wiki/api.php?action=query&prop=imageinfo"
               "&iiprop=url&format=json&titles=" + urllib.parse.quote(titles))
        tmp = ASSETS / ".api.json"
        get(api, tmp)
        d = json.loads(tmp.read_text(encoding="utf-8"))
        tmp.unlink()
        norm = {x["to"]: x["from"] for x in d["query"].get("normalized", [])}
        for p in d["query"]["pages"].values():
            if "imageinfo" not in p:
                print("  missing on PRTS:", p["title"], file=sys.stderr)
                continue
            title = norm.get(p["title"], p["title"])
            fname = title.split(":", 1)[1].replace(" ", "_")
            urls[fname] = p["imageinfo"][0]["url"]
    return urls


def main():
    (ASSETS / "img").mkdir(parents=True, exist_ok=True)
    (ASSETS / "music").mkdir(parents=True, exist_ok=True)
    (ASSETS / "fonts").mkdir(parents=True, exist_ok=True)
    todo = {k: v for k, v in IMAGES.items() if not (ASSETS / "img" / v).exists()}
    if todo:
        urls = resolve_prts(todo)
        for src, local in todo.items():
            if src not in urls:
                continue
            n = get(urls[src], ASSETS / "img" / local)
            print(f"img   {local:28s} {n / 1e6:5.2f} MB")
            time.sleep(0.5)
    for sub, table in (("music", MUSIC), ("fonts", FONTS)):
        for local, url in table.items():
            dest = ASSETS / sub / local
            if not dest.exists():
                n = get(url, dest)
                print(f"{sub:5s} {local:28s} {n / 1e6:5.2f} MB")
    print("done ->", ASSETS)


if __name__ == "__main__":
    main()
