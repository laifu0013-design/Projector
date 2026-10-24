"""三种画风 × 三套发色的少女形象示例（SVG 矢量绘制，rsvg-convert 渲染），供挑选。

用法：python style_samples.py <输出目录>
输出：<输出目录>/<画风>_<配色>.svg/.png 和 samples.png 对比图
"""
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

PALETTES = {
    "orange": dict(name="橙棕", hair="#D9773F", hair_dark="#A44E26", hair_light="#F4AE74",
                   eye_top="#5E2F1C", eye_bottom="#D98A4E", accent="#7BC46A"),
    "black": dict(name="黑色", hair="#33313D", hair_dark="#16151C", hair_light="#6E6C86",
                  eye_top="#26203A", eye_bottom="#8A78C0", accent="#E94F6E"),
    "pink": dict(name="粉色", hair="#F3A3BA", hair_dark="#CF6F8E", hair_light="#FFD6E1",
                 eye_top="#6E2C47", eye_bottom="#E683A6", accent="#FFFFFF"),
}
SKIN, SKIN_SHADE, LINE = "#FFEADF", "#F5CDBD", "#B9786A"
BLUSH = "#FF8FA3"


def svg(body: str, defs: str = "") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="400" height="400">'
            f'<defs><filter id="blur" x="-50%" y="-50%" width="200%" height="200%">'
            f'<feGaussianBlur stdDeviation="3"/></filter>{defs}</defs>{body}</svg>')


def zigzag(x_from, x_to, tips, valley_y, tip_ys, curl=4):
    """刘海下沿：从右到左交替的发梢尖和发缝，二次曲线让每一缕带弧度。"""
    n = tips
    seg = (x_from - x_to) / (2 * n)
    d = ""
    x = x_from
    for i in range(n):
        tip_x, tip_y = x - seg, tip_ys[i % len(tip_ys)]
        val_x = x - 2 * seg
        d += f" Q {x - seg * 0.2:.1f} {tip_y - 8:.1f} {tip_x:.1f} {tip_y:.1f}"
        d += f" Q {tip_x - seg * 0.3 - curl:.1f} {valley_y + 6:.1f} {val_x:.1f} {valley_y:.1f}"
        x = val_x
    return d


# ---------------- 画风一：日系 Q 版美少女 ----------------

def anime(p) -> str:
    defs = f'''
    <linearGradient id="hairG" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{p['hair_light']}"/><stop offset=".45" stop-color="{p['hair']}"/>
      <stop offset="1" stop-color="{p['hair_dark']}"/></linearGradient>
    <linearGradient id="irisG" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{p['eye_top']}"/><stop offset=".55" stop-color="{p['eye_top']}"/>
      <stop offset="1" stop-color="{p['eye_bottom']}"/></linearGradient>
    <linearGradient id="skinG" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{SKIN_SHADE}"/><stop offset=".28" stop-color="{SKIN}"/></linearGradient>'''

    def eye(cx, cy, side):
        s = side  # -1 左眼，1 右眼
        return f'''
        <ellipse cx="{cx}" cy="{cy}" rx="11.5" ry="14.5" fill="url(#irisG)"/>
        <ellipse cx="{cx}" cy="{cy + 2}" rx="5.2" ry="7.5" fill="#1C1216" opacity=".75"/>
        <path d="M {cx - 8} {cy + 8} Q {cx} {cy + 14} {cx + 8} {cy + 8}" stroke="{p['eye_bottom']}" stroke-width="2.4"
              fill="none" opacity=".9" stroke-linecap="round"/>
        <ellipse cx="{cx - 4 * s - 1}" cy="{cy - 5}" rx="4.2" ry="5.4" fill="#fff"/>
        <circle cx="{cx + 4 * s}" cy="{cy + 6}" r="2" fill="#fff"/>
        <path d="M {cx - 11 * s} {cy - 12} Q {cx + 1 * s} {cy - 19} {cx + 12 * s} {cy - 11} Q {cx + 15 * s} {cy - 9} {cx + 17 * s} {cy - 12}"
              stroke="#2A1A1C" stroke-width="2.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M {cx + 11 * s} {cy - 11} L {cx + 15 * s} {cy - 4}" stroke="#2A1A1C" stroke-width="1.6"
              stroke-linecap="round" opacity=".8"/>
        <path d="M {cx - 5} {cy + 16} Q {cx} {cy + 17.5} {cx + 5} {cy + 16}" stroke="#2A1A1C" stroke-width="1.3"
              fill="none" opacity=".55" stroke-linecap="round"/>'''

    bangs_d = "M 55 106 C 48 60 74 38 100 38 C 126 38 152 60 145 106" + \
        zigzag(145, 55, 5, 84, [100, 96, 93, 96, 100]) + " Z"
    body = f'''
    <path d="M 53 92 C 45 58 70 33 100 33 C 130 33 155 58 147 92 C 152 130 158 160 150 192 L 139 174 L 132 194
             C 125 172 127 152 129 142 L 71 142 C 73 152 75 172 68 194 L 61 174 L 50 192 C 42 160 48 130 53 92 Z"
          fill="url(#hairG)" stroke="{p['hair_dark']}" stroke-width="1.6"/>
    <path d="M 62 100 C 60 133 78 159 100 165 C 122 159 140 133 138 100 C 138 77 122 64 100 64 C 78 64 62 77 62 100 Z"
          fill="url(#skinG)" stroke="{LINE}" stroke-width="1.2"/>
    <ellipse cx="76" cy="136" rx="9.5" ry="5.5" fill="{BLUSH}" opacity=".55" filter="url(#blur)"/>
    <ellipse cx="124" cy="136" rx="9.5" ry="5.5" fill="{BLUSH}" opacity=".55" filter="url(#blur)"/>
    <g stroke="#fff" stroke-width="1.2" opacity=".8" stroke-linecap="round">
      <path d="M 71 137 l 3 -4 M 75 138 l 3 -4 M 79 138 l 3 -4"/><path d="M 119 137 l 3 -4 M 123 138 l 3 -4 M 127 138 l 3 -4"/></g>
    {eye(82, 117, -1)}{eye(118, 117, 1)}
    <path d="M 99 131 q 1 1.5 2 0" stroke="{LINE}" stroke-width="1.2" fill="none" opacity=".6"/>
    <path d="M 93 143 Q 96.5 147 100 143.5 Q 103.5 147 107 143" stroke="#7A3B3B" stroke-width="1.8" fill="none"
          stroke-linecap="round"/>
    <path d="{bangs_d}" fill="url(#hairG)" stroke="{p['hair_dark']}" stroke-width="1.6" stroke-linejoin="round"/>
    <path d="M 64 84 C 53 112 55 142 60 172 L 66 158 C 68 132 69 110 70 92 Z" fill="url(#hairG)"
          stroke="{p['hair_dark']}" stroke-width="1.4"/>
    <path d="M 136 84 C 147 112 145 142 140 172 L 134 158 C 132 132 131 110 130 92 Z" fill="url(#hairG)"
          stroke="{p['hair_dark']}" stroke-width="1.4"/>
    <path d="M 70 60 Q 100 47 130 60" stroke="#fff" stroke-width="4" fill="none" opacity=".45" stroke-linecap="round"/>
    <g transform="translate(136 58) rotate(20)">
      <path d="M 0 0 L -11 -7 L -11 7 Z M 0 0 L 11 -7 L 11 7 Z" fill="{p['accent']}" stroke="{p['hair_dark']}"
            stroke-width="1.2" stroke-linejoin="round"/>
      <circle r="3" fill="{p['accent']}" stroke="{p['hair_dark']}" stroke-width="1.2"/></g>'''
    return svg(body, defs)


# ---------------- 画风二：简约萌系 ----------------

def simple(p) -> str:
    o = "#4A3430"
    body = f'''
    <g stroke="{o}" stroke-width="4" stroke-linejoin="round" stroke-linecap="round">
      <path d="M 48 118 C 40 150 52 176 66 186 L 70 160 Z M 152 118 C 160 150 148 176 134 186 L 130 160 Z"
            fill="{p['hair']}"/>
      <circle cx="100" cy="116" r="50" fill="{SKIN}"/>
      <path d="M 48 120 C 44 74 70 52 100 52 C 130 52 156 74 152 120 C 144 104 132 96 122 98
               C 114 86 86 86 78 98 C 68 96 56 104 48 120 Z" fill="{p['hair']}"/>
      <g transform="translate(140 62)">
        <path d="M 0 0 C -10 -14 -24 -8 -20 4 C -18 12 -8 8 0 0 Z M 0 0 C 10 -14 24 -8 20 4 C 18 12 8 8 0 0 Z"
              fill="{p['accent'] if p['accent'] != '#FFFFFF' else '#FF7FA0'}"/>
        <circle r="4.5" fill="{p['accent'] if p['accent'] != '#FFFFFF' else '#FF7FA0'}"/></g>
    </g>
    <ellipse cx="82" cy="122" rx="5.5" ry="7.5" fill="{o}"/><ellipse cx="118" cy="122" rx="5.5" ry="7.5" fill="{o}"/>
    <circle cx="80" cy="119" r="2" fill="#fff"/><circle cx="116" cy="119" r="2" fill="#fff"/>
    <ellipse cx="70" cy="136" rx="8" ry="5" fill="{BLUSH}" opacity=".7"/>
    <ellipse cx="130" cy="136" rx="8" ry="5" fill="{BLUSH}" opacity=".7"/>
    <path d="M 94 134 Q 100 140 106 134" stroke="{o}" stroke-width="3.2" fill="none" stroke-linecap="round"/>
    <path d="M 66 74 Q 74 66 84 64" stroke="#fff" stroke-width="4" fill="none" opacity=".5" stroke-linecap="round"/>'''
    return svg(body)


# ---------------- 画风三：清新扁平插画 ----------------

def flat(p) -> str:
    body = f'''
    <path d="M 60 96 C 52 60 76 40 104 42 C 134 44 152 70 144 104 C 148 132 150 162 142 186 C 128 176 122 164 122 150
             L 80 150 C 78 166 72 178 58 186 C 50 160 52 128 60 96 Z" fill="{p['hair']}"/>
    <path d="M 120 110 C 132 130 140 160 142 186 C 132 178 124 166 124 150 Z" fill="{p['hair_dark']}" opacity=".35"/>
    <rect x="91" y="148" width="18" height="22" rx="6" fill="{SKIN_SHADE}"/>
    <path d="M 68 98 C 68 130 82 155 100 159 C 118 155 132 130 132 98 C 132 76 118 63 100 63 C 82 63 68 76 68 98 Z"
          fill="{SKIN}"/>
    <circle cx="69" cy="112" r="6" fill="{SKIN}"/><circle cx="69" cy="122" r="2" fill="{p['accent'] if p['accent'] != '#FFFFFF' else '#E8B04A'}"/>
    <ellipse cx="82" cy="128" rx="8" ry="4.5" fill="{BLUSH}" opacity=".35" filter="url(#blur)"/>
    <ellipse cx="118" cy="128" rx="8" ry="4.5" fill="{BLUSH}" opacity=".35" filter="url(#blur)"/>
    <path d="M 76 101 Q 84 97 92 100" stroke="{p['hair_dark']}" stroke-width="2.2" fill="none" stroke-linecap="round"/>
    <path d="M 108 100 Q 116 97 124 101" stroke="{p['hair_dark']}" stroke-width="2.2" fill="none" stroke-linecap="round"/>
    <g>
      <path d="M 76 115 Q 84 107 92 115 Q 84 120 76 115 Z" fill="#fff"/>
      <circle cx="84" cy="114.5" r="4.6" fill="{p['eye_top']}"/><circle cx="85.5" cy="113" r="1.4" fill="#fff"/>
      <path d="M 75 115 Q 84 106 93 114" stroke="#2B1D1D" stroke-width="2.2" fill="none" stroke-linecap="round"/>
      <path d="M 108 114 Q 116 107 124 115 Q 116 120 108 114 Z" fill="#fff"/>
      <circle cx="116" cy="114.5" r="4.6" fill="{p['eye_top']}"/><circle cx="117.5" cy="113" r="1.4" fill="#fff"/>
      <path d="M 107 114 Q 116 106 125 115" stroke="#2B1D1D" stroke-width="2.2" fill="none" stroke-linecap="round"/>
    </g>
    <path d="M 100 120 Q 98 128 101 130" stroke="{LINE}" stroke-width="1.4" fill="none" opacity=".55" stroke-linecap="round"/>
    <path d="M 93 139 Q 100 143.5 107 139 Q 100 141 93 139 Z" fill="#D9707A" stroke="#C25F69" stroke-width="1.4"
          stroke-linejoin="round"/>
    <path d="M 58 102 C 60 66 84 48 110 50 C 124 51 136 58 142 70 C 128 66 116 70 108 78 C 98 90 84 92 70 100
             C 66 108 64 118 62 128 C 58 120 57 110 58 102 Z" fill="{p['hair']}"/>
    <path d="M 110 50 C 124 51 136 58 142 70 C 132 68 122 70 114 74 C 118 64 116 56 110 50 Z" fill="{p['hair_light']}"
          opacity=".6"/>
    <path d="M 132 74 C 140 84 144 96 144 108 C 138 98 132 90 126 84 Z" fill="{p['hair']}"/>'''
    return svg(body)


STYLES = [("anime", "日系 Q 版美少女", anime), ("simple", "简约萌系", simple), ("flat", "清新扁平插画", flat)]


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    font = ImageFont.truetype("/System/Library/Fonts/Hiragino Sans GB.ttc", 26)
    cell, label_w, head = 260, 220, 50
    sheet = Image.new("RGB", (label_w + cell * 3, head + cell * 3), (29, 28, 26))
    d = ImageDraw.Draw(sheet)
    for j, (key, pal) in enumerate(PALETTES.items()):
        d.text((label_w + cell * j + cell / 2, head / 2), pal["name"], font=font, fill=(236, 235, 230), anchor="mm")
    for i, (skey, sname, fn) in enumerate(STYLES):
        d.text((label_w / 2, head + cell * i + cell / 2), f"{i + 1}. {sname}", font=font, fill=(236, 235, 230), anchor="mm")
        for j, (pkey, pal) in enumerate(PALETTES.items()):
            base = os.path.join(out, f"{skey}_{pkey}")
            open(base + ".svg", "w").write(fn(pal))
            subprocess.run(["rsvg-convert", base + ".svg", "-o", base + ".png"], check=True)
            im = Image.open(base + ".png").convert("RGBA").resize((cell - 20, cell - 20), Image.LANCZOS)
            sheet.paste(im, (label_w + cell * j + 10, head + cell * i + 10), im)
    sheet.save(os.path.join(out, "samples.png"))


if __name__ == "__main__":
    main()
