# 平静入梦 — 特蕾西娅 · 明日方舟静止画MAD

《明日方舟》角色**特蕾西娅（Theresa）**的静止画 MAD，1080p / 30fps，时长约 3 分 16 秒。

- 成片：[`output/Theresa_MAD_平静入梦.mp4`](output/Theresa_MAD_平静入梦.mp4)
- 角色资料与故事整理：[`docs/特蕾西娅_角色资料.md`](docs/特蕾西娅_角色资料.md)
- 分镜表（每个镜头的小节、时间、画面、字幕）：[`docs/分镜表.md`](docs/分镜表.md)

## 构思

片名取自特蕾西娅临终前的愿望——

> 也许有天……我们会一起让这片大地上的每一个人，都能平静入梦。

配乐选用萨蒂《裸体歌舞（Gymnopédie）第 1 号》。这首曲子的表情记号是 *Lent et douloureux*（缓慢而痛苦地），像一首安静的摇篮曲，与她温柔、克制，又注定要告别的一生相称。全片按曲子的乐句分为五幕：

| 幕 | 小节 | 内容 |
| --- | --- | --- |
| 序 | 1–4 | 前奏，白花丛中的特蕾西娅，片名 |
| I 王冠 | 5–12 | 卡兹戴尔的废墟，王冠同时选择了双子 |
| II 巴别塔 | 13–23 | 与博士、阿米娅相遇，巴别塔成为很多人的家 |
| III 诀别 | 24–51 | 分裂、雨中会面、冠冕交给阿米娅、抹去博士的记忆、死亡；第二遍前奏（40–43 小节）落在她离世的那一刻 |
| IV 慈悲灯塔 | 52–70 | 第十四章：重逢、交出魔王之剑、与凯尔希和博士告别、众魂散去 |
| V 存续 | 71–80 | 干员「魔王」：“别怕，我在。”回到开头的白花，片尾字卡 |

所有转场都对齐在乐曲的小节线上（节拍由 `scripts/analyze_beats.py` 从音频中检测：77.13 BPM，3/4 拍，共 80 小节）。画面效果包括缓慢的推拉摇移、叠化、黑场与白闪转场、同构图 CG 之间的“动态”叠化（如 i10_1 → i10_2 中特雷西斯的离去、i33_1 → i33_2 中特蕾西娅的身影浮现），以及呼应她“永恒尘埃 / 过往尘埃”的漂浮微尘粒子。

## 自行生成

依赖：Python 3.10+、ffmpeg、`numpy pillow opencv-python-headless scipy`。

```bash
pip install numpy pillow opencv-python-headless scipy
python3 scripts/fetch_assets.py        # 下载 CG / 立绘 / 音乐 / 字体到 assets/（不入库）
python3 scripts/analyze_beats.py       # 节拍分析 -> mad/beats.json
python3 scripts/render.py              # 渲染成片 -> output/
python3 scripts/render.py --stills 30 95   # 只渲染若干预览帧 -> build/
python3 scripts/render.py --cuesheet   # 重新生成 docs/分镜表.md
```

分镜写在 [`mad/storyboard.json`](mad/storyboard.json)，以**小节**为单位定义每个镜头（起始小节、持续小节数、图片、缩放/中心点、转场、字幕）。想换配乐的话，对新音频重新运行 `analyze_beats.py`，再按新的小节数调整分镜即可。

## 版权与署名

- **素材**：剧情 CG、干员立绘 © Hypergryph（鹰角网络）/ Yostar，取自 SideStory「巴别塔」、主线第十四章「慈悲灯塔」与干员「魔王」，经 [PRTS 维基](https://prts.wiki/) 获取。原图不随本仓库分发，由脚本按需下载。
- **音乐**："Gymnopedie No. 1" — Erik Satie, performed by Kevin MacLeod ([incompetech.com](https://incompetech.com/))，Licensed under Creative Commons: By Attribution 4.0 License, <http://creativecommons.org/licenses/by/4.0/>。
- **字体**：Noto Serif SC、Cormorant Garamond（SIL Open Font License 1.1）。
- 本作为**非商业同人作品**，仅供交流欣赏。若转载或投稿到视频平台，请保留片尾署名并注明素材出处。
