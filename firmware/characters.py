"""“小橙子”两种画风的 SVG 角色：日系 Q 版（anime）和简约萌系（simple），每种都支持同一套表情。

render(style, expression) -> SVG 字符串，viewBox 200×200。
表情由三部分组成：眼睛、嘴巴、装饰（见 EXPRESSIONS）。
"""

HAIR, HAIR_DARK, HAIR_LIGHT = "#D9773F", "#A44E26", "#F4AE74"
EYE_TOP, EYE_BOTTOM = "#5E2F1C", "#D98A4E"
SKIN, SKIN_SHADE, LINE = "#FFEADF", "#F3C8B6", "#B9786A"
BLUSH, MOUTH, TONGUE, HEART, TEAR = "#FF8FA3", "#7A2E35", "#FF8E9E", "#F0506E", "#8CC8F5"
INK = "#2A1A1C"

# 表情名 -> (眼睛, 嘴巴, 装饰列表)
EXPRESSIONS = {
    "neutral": ("open", "smile", []),
    "happy": ("happy", "cat", []),
    "laughing": ("chevron", "open", []),
    "winking": ("wink", "tongue", ["sparkle"]),
    "thinking": ("look_up", "flat", ["question"]),
    "surprised": ("surprised", "o", []),
    "sad": ("teary", "frown", ["tear"]),
    "sleepy": ("closed", "o_small", ["zz", "bubble"]),
    "loving": ("heart", "cat", ["heart"]),
    "angry": ("angry", "frown", ["anger"]),
    # 以下用于凑满固件的 21 种情绪
    "funny": ("happy", "open", ["sweat"]),
    "crying": ("closed_tear", "open_sad", ["tears"]),
    "embarrassed": ("chevron", "wavy", ["sweat", "blush_more"]),
    "shocked": ("surprised", "o_big", ["sweat"]),
    "cool": ("half", "smirk", ["sparkle"]),
    "relaxed": ("closed", "smile", []),
    "delicious": ("happy", "tongue_side", []),
    "kissy": ("closed", "kiss", ["heart"]),
    "confident": ("star", "smirk", []),
    "silly": ("wink", "tongue", []),
    "confused": ("swirl", "wavy", ["question"]),
}


def heart_path(cx, cy, s):
    return (f"M {cx} {cy + s * 0.9} C {cx - s * 1.6} {cy - s * 0.1} {cx - s * 0.9} {cy - s * 1.25} {cx} {cy - s * 0.45} "
            f"C {cx + s * 0.9} {cy - s * 1.25} {cx + s * 1.6} {cy - s * 0.1} {cx} {cy + s * 0.9} Z")


def star_path(cx, cy, r):
    import math
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append(f"{cx + rr * math.cos(a):.1f} {cy + rr * math.sin(a):.1f}")
    return "M " + " L ".join(pts) + " Z"


def svg(body, defs=""):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="400" height="400">'
            f'<defs><filter id="blur" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3"/>'
            f'</filter>{defs}</defs>{body}</svg>')


def zigzag(x_from, x_to, tips, valley_y, tip_ys, curl=4):
    """刘海下沿：从右到左交替的发梢尖和发缝。"""
    seg = (x_from - x_to) / (2 * tips)
    d, x = "", x_from
    for i in range(tips):
        tip_x, tip_y = x - seg, tip_ys[i % len(tip_ys)]
        val_x = x - 2 * seg
        d += f" Q {x - seg * 0.2:.1f} {tip_y - 8:.1f} {tip_x:.1f} {tip_y:.1f}"
        d += f" Q {tip_x - seg * 0.3 - curl:.1f} {valley_y + 6:.1f} {val_x:.1f} {valley_y:.1f}"
        x = val_x
    return d


class Character:
    EYES = ((79, 118), (121, 118))   # 左右眼中心
    MOUTH_Y = 147
    BLUSH_Y = 138
    BLUSH_DX = 28

    def eyes(self, kind):
        (lx, ly), (rx, ry) = self.EYES
        if kind == "wink":
            return self.eye("open", lx, ly, -1) + self.eye("happy", rx, ry, 1)
        if kind == "chevron":
            return self.eye("chev_l", lx, ly, -1) + self.eye("chev_r", rx, ry, 1)
        return self.eye(kind, lx, ly, -1) + self.eye(kind, rx, ry, 1)

    def mouth(self, kind):
        y, c = self.MOUTH_Y, 100
        w = self.MOUTH_W
        s = f'stroke="{self.MOUTH_LINE}" stroke-width="{self.MOUTH_STROKE}" fill="none" stroke-linecap="round" stroke-linejoin="round"'
        return {
            "smile": f'<path d="M {c - w} {y - 1} Q {c} {y + w * 0.9} {c + w} {y - 1}" {s}/>',
            "cat": f'<path d="M {c - w} {y - 1} Q {c - w / 2} {y + w * 0.7} {c} {y} Q {c + w / 2} {y + w * 0.7} {c + w} {y - 1}" {s}/>',
            "open": (f'<path d="M {c - w * 1.3} {y - 2} Q {c} {y + w * 2.4} {c + w * 1.3} {y - 2} Z" fill="{MOUTH}"/>'
                     f'<path d="M {c - w * 0.75} {y + w * 1.15} Q {c} {y + w * 0.3} {c + w * 0.75} {y + w * 1.15} Q {c} {y + w * 1.6} {c - w * 0.75} {y + w * 1.15} Z" fill="{TONGUE}"/>'),
            "open_sad": f'<path d="M {c - w} {y + w * 1.2} Q {c} {y - w * 0.6} {c + w} {y + w * 1.2} Z" fill="{MOUTH}"/>',
            "tongue": (f'<path d="M {c - w} {y - 1} Q {c} {y + w * 0.8} {c + w} {y - 1}" {s}/>'
                       f'<path d="M {c - w * 0.15} {y + w * 0.35} Q {c + w * 0.35} {y + w * 1.6} {c + w * 0.85} {y + w * 0.25} Z" fill="{TONGUE}"/>'),
            "tongue_side": (f'<path d="M {c - w} {y} Q {c} {y + w * 0.7} {c + w} {y - 1}" {s}/>'
                            f'<path d="M {c + w * 0.2} {y + w * 0.3} Q {c + w * 1.2} {y + w * 1.4} {c + w * 1.2} {y - w * 0.1} Z" fill="{TONGUE}"/>'),
            "o": f'<ellipse cx="{c}" cy="{y + 2}" rx="{w * 0.55}" ry="{w * 0.7}" fill="{MOUTH}"/>',
            "o_big": f'<ellipse cx="{c}" cy="{y + 3}" rx="{w * 0.75}" ry="{w}" fill="{MOUTH}"/>',
            "o_small": f'<ellipse cx="{c}" cy="{y + 1}" rx="{w * 0.35}" ry="{w * 0.35}" fill="{MOUTH}"/>',
            "flat": f'<path d="M {c - w * 0.6} {y + 1} L {c + w * 0.6} {y + 1}" {s}/>',
            "frown": f'<path d="M {c - w * 0.8} {y + w * 0.6} Q {c} {y - w * 0.4} {c + w * 0.8} {y + w * 0.6}" {s}/>',
            "wavy": f'<path d="M {c - w} {y + 1} q {w / 4} -{w / 3} {w / 2} 0 t {w / 2} 0 t {w / 2} 0 t {w / 2} 0" {s}/>',
            "smirk": f'<path d="M {c - w * 0.7} {y} Q {c + w * 0.2} {y + w * 0.7} {c + w} {y - w * 0.4}" {s}/>',
            "kiss": f'<path d="M {c - 1} {y - w * 0.6} q {w * 0.6} {w * 0.3} 0 {w * 0.6} q {w * 0.6} {w * 0.3} 0 {w * 0.6}" {s}/>',
        }[kind]

    def blush(self, more=False):
        op = ".8" if more else self.BLUSH_OPACITY
        l, r = 100 - self.BLUSH_DX, 100 + self.BLUSH_DX
        return (f'<ellipse cx="{l}" cy="{self.BLUSH_Y}" rx="10" ry="5.5" fill="{BLUSH}" opacity="{op}" {self.BLUSH_FILTER}/>'
                f'<ellipse cx="{r}" cy="{self.BLUSH_Y}" rx="10" ry="5.5" fill="{BLUSH}" opacity="{op}" {self.BLUSH_FILTER}/>')

    SYMBOL_SHIFT = (0, 0)   # 问号、zz、爱心等头顶符号的平移量

    def extras(self, items):
        out = ""
        (lx, ly), (rx, ry) = self.EYES
        sx, sy = self.SYMBOL_SHIFT
        for it in items:
            if it == "question":
                q = "M 146 52 q 0 -9 8 -9 q 8 0 8 8 q 0 6 -8 8 v 5"
                out += (f'<g transform="translate({sx} {sy})" stroke-linecap="round" fill="none">'
                        f'<path d="{q}" stroke="#fff" stroke-width="7"/><circle cx="154" cy="72" r="4" fill="#fff" stroke="none"/>'
                        f'<path d="{q}" stroke="{INK}" stroke-width="3.4"/><circle cx="154" cy="72" r="2.2" fill="{INK}" stroke="none"/></g>')
            elif it == "zz":
                z = "M 140 52 h 10 l -10 10 h 10 M 154 38 h 7 l -7 7 h 7"
                out += (f'<g transform="translate({sx} {sy})" fill="none" stroke-linecap="round" stroke-linejoin="round">'
                        f'<path d="{z}" stroke="#fff" stroke-width="6.5"/><path d="{z}" stroke="{INK}" stroke-width="3"/></g>')
            elif it == "bubble":
                out += (f'<circle cx="{100 + self.MOUTH_W * 1.6}" cy="{self.MOUTH_Y - 4}" r="5" fill="#D6EEFF" opacity=".85" stroke="#fff" stroke-width="1"/>'
                        f'<circle cx="{100 + self.MOUTH_W * 1.6 - 1.6}" cy="{self.MOUTH_Y - 5.6}" r="1.4" fill="#fff"/>')
            elif it == "heart":
                out += f'<path d="{heart_path(152 + sx, 56 + sy, 7)}" fill="{HEART}" stroke="#fff" stroke-width="1.5"/>'
            elif it == "anger":
                out += (f'<g stroke="{HEART}" stroke-width="3" fill="none" stroke-linecap="round" transform="translate({150 + sx} {54 + sy})">'
                        f'<path d="M -9 -3 q 6 0 6 -6 M 3 -9 q 0 6 6 6 M 9 3 q -6 0 -6 6 M -3 9 q 0 -6 -6 -6"/></g>')
            elif it == "sparkle":
                out += f'<path d="{star_path(150 + sx, 56 + sy, 8)}" fill="#FFD25A"/>'
            elif it == "sweat":
                out += (f'<path d="M 148 70 q -6 9 0 12 q 6 -3 0 -12 Z" fill="{TEAR}"/>')
            elif it == "tear":
                out += f'<path d="M {lx - 6} {ly + 14} q -5 8 0 11 q 5 -3 0 -11 Z" fill="{TEAR}"/>'
            elif it == "tears":
                for x in (lx, rx):
                    out += f'<path d="M {x - 3} {ly + 4} q 1 16 -2 30 h 6 q -1 -14 0 -30 Z" fill="{TEAR}" opacity=".9"/>'
            elif it == "blush_more":
                out += self.blush(more=True)
        return out

    def render(self, expression):
        eyes, mouth, extras = EXPRESSIONS[expression]
        face = self.blush() + self.eyes(eyes) + self.nose() + self.mouth(mouth)
        return svg(self.back() + face + self.front() + self.extras(extras), self.defs())


class Anime(Character):
    """日系 Q 版：宽圆脸、渐变大眼、刘海阴影、呆毛、水手领、橙子切片发夹。"""
    MOUTH_W = 6.5
    MOUTH_LINE, MOUTH_STROKE = MOUTH, 1.9
    BLUSH_OPACITY, BLUSH_FILTER = ".55", 'filter="url(#blur)"'
    FACE = "M 56 100 C 55 136 76 160 100 166 C 124 160 145 136 144 100 C 144 74 124 58 100 58 C 76 58 56 74 56 100 Z"

    def defs(self):
        return f'''
        <linearGradient id="hairG" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{HAIR_LIGHT}"/>
          <stop offset=".45" stop-color="{HAIR}"/><stop offset="1" stop-color="{HAIR_DARK}"/></linearGradient>
        <linearGradient id="irisG" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{EYE_TOP}"/>
          <stop offset=".5" stop-color="{EYE_TOP}"/><stop offset="1" stop-color="{EYE_BOTTOM}"/></linearGradient>
        <clipPath id="faceClip"><path d="{self.FACE}"/></clipPath>'''

    def back(self):
        return f'''
        <path d="M 46 94 C 38 56 66 26 100 26 C 134 26 162 56 154 94 C 160 130 164 162 156 196 L 144 178 L 136 198
                 C 128 176 130 156 132 146 L 68 146 C 70 156 72 176 64 198 L 56 178 L 44 196 C 36 162 40 130 46 94 Z"
              fill="url(#hairG)" stroke="{HAIR_DARK}" stroke-width="1.6"/>
        <path d="M 92 156 L 92 182 L 108 182 L 108 156 Z" fill="{SKIN_SHADE}"/>
        <path d="M 46 200 C 52 186 74 178 92 179 L 100 193 L 108 179 C 126 178 148 186 154 200 Z" fill="#FFFFFF" stroke="#D8D2CC" stroke-width="1.2"/>
        <path d="M 62 200 C 66 192 80 186 92 186 L 100 199 L 108 186 C 120 186 134 192 138 200" fill="none" stroke="{HAIR}" stroke-width="2.4"/>
        <path d="M 100 193 L 89 187 L 90 199 Z M 100 193 L 111 187 L 110 199 Z" fill="{HAIR}" stroke="{HAIR_DARK}" stroke-width="1" stroke-linejoin="round"/>
        <path d="{self.FACE}" fill="{SKIN}" stroke="{LINE}" stroke-width="1.2"/>
        <g clip-path="url(#faceClip)"><path d="M 46 100 {zigzag(154, 46, 5, 90, [108, 103, 100, 103, 108])} L 46 40 L 154 40 L 154 100 Z"
              fill="{SKIN_SHADE}" opacity=".8" transform="translate(0 4)"/></g>'''

    def front(self):
        bangs = "M 47 106 C 40 54 70 26 100 26 C 130 26 160 54 153 106" + zigzag(153, 47, 5, 84, [100, 95, 92, 95, 100]) + " Z"
        return f'''
        <path d="{bangs}" fill="url(#hairG)" stroke="{HAIR_DARK}" stroke-width="1.6" stroke-linejoin="round"/>
        <path d="M 56 82 C 43 110 45 142 49 176 L 57 160 C 59 134 60 110 61 92 Z" fill="url(#hairG)" stroke="{HAIR_DARK}" stroke-width="1.4"/>
        <path d="M 144 82 C 157 110 155 142 151 176 L 143 160 C 141 134 140 110 139 92 Z" fill="url(#hairG)" stroke="{HAIR_DARK}" stroke-width="1.4"/>
        <path d="M 66 52 Q 100 38 134 52" stroke="#fff" stroke-width="4.5" fill="none" opacity=".45" stroke-linecap="round"/>
        <path d="M 101 27 C 96 12 112 6 117 15 C 110 13 105 19 105 27 Z" fill="{HAIR}" stroke="{HAIR_DARK}" stroke-width="1.3"/>
        <g transform="translate(140 50)">
          <circle r="9" fill="#FFB347" stroke="#E0791A" stroke-width="1.6"/><circle r="6" fill="#FFD27A"/>
          <path d="M 0 -6 V 6 M -5.2 -3 L 5.2 3 M -5.2 3 L 5.2 -3" stroke="#F29A2E" stroke-width="1.1"/>
          <path d="M 5 -8 q 6 -6 10 -2 q -5 4 -10 2 Z" fill="#7BC46A"/></g>'''

    def nose(self):
        return f'<path d="M 99 133 q 1 1.5 2 0" stroke="{LINE}" stroke-width="1.2" fill="none" opacity=".6"/>'

    def eye(self, kind, cx, cy, s):
        lash = f'stroke="{INK}" fill="none" stroke-linecap="round" stroke-linejoin="round"'
        upper = (f'<path d="M {cx - 12 * s} {cy - 12} Q {cx + 1 * s} {cy - 20} {cx + 13 * s} {cy - 11} Q {cx + 16 * s} {cy - 9} {cx + 18 * s} {cy - 12}" {lash} stroke-width="2.9"/>'
                 f'<path d="M {cx + 12 * s} {cy - 11} L {cx + 16 * s} {cy - 4}" {lash} stroke-width="1.6" opacity=".8"/>')
        lower = f'<path d="M {cx - 5} {cy + 16} Q {cx} {cy + 17.5} {cx + 5} {cy + 16}" {lash} stroke-width="1.3" opacity=".55"/>'

        def iris(dx=0, dy=0, shine=1.0, ry=15):
            return (f'<ellipse cx="{cx}" cy="{cy}" rx="12" ry="{ry}" fill="url(#irisG)"/>'
                    f'<ellipse cx="{cx + dx}" cy="{cy + 2 + dy}" rx="5.4" ry="7.6" fill="#1C1216" opacity=".75"/>'
                    f'<path d="M {cx - 8} {cy + 8} Q {cx} {cy + 14} {cx + 8} {cy + 8}" stroke="{EYE_BOTTOM}" stroke-width="2.4" fill="none" stroke-linecap="round"/>'
                    f'<ellipse cx="{cx - 4 * s - 1 + dx}" cy="{cy - 5 + dy}" rx="{4.4 * shine}" ry="{5.6 * shine}" fill="#fff"/>'
                    f'<circle cx="{cx + 4 * s + dx}" cy="{cy + 6 + dy}" r="2" fill="#fff"/>')

        if kind in ("open",):
            return iris() + upper + lower
        if kind == "look_up":
            return iris(dx=2 * -s * -1, dy=-3) + upper + lower
        if kind == "teary":
            return iris(shine=1.35) + upper + lower + f'<path d="M {cx - 9} {cy + 10} Q {cx} {cy + 15} {cx + 9} {cy + 10}" stroke="#fff" stroke-width="1.6" fill="none" opacity=".8"/>'
        if kind == "star":
            return iris() + f'<path d="{star_path(cx - 3 * s, cy - 4, 6)}" fill="#fff"/>' + upper + lower
        if kind == "angry":
            return (iris(ry=13) + f'<path d="M {cx - 13 * s} {cy - 8} L {cx + 13 * s} {cy - 15}" {lash} stroke-width="3.2"/>' + lower)
        if kind == "half":
            return (iris() + f'<path d="M {cx - 14} {cy - 16} H {cx + 14} V {cy - 2} Q {cx} {cy - 6} {cx - 14} {cy - 2} Z" fill="{SKIN}"/>'
                    f'<path d="M {cx - 13 * s} {cy - 2} Q {cx} {cy - 7} {cx + 14 * s} {cy - 3}" {lash} stroke-width="2.9"/>' + lower)
        if kind == "surprised":
            return (f'<ellipse cx="{cx}" cy="{cy}" rx="12" ry="14" fill="#fff" stroke="{INK}" stroke-width="1.4"/>'
                    f'<circle cx="{cx}" cy="{cy + 1}" r="5.5" fill="url(#irisG)"/><circle cx="{cx - 2}" cy="{cy - 1}" r="1.8" fill="#fff"/>'
                    f'<path d="M {cx - 12 * s} {cy - 15} Q {cx} {cy - 21} {cx + 13 * s} {cy - 14}" {lash} stroke-width="2.6"/>')
        if kind == "happy":
            return f'<path d="M {cx - 11} {cy + 3} Q {cx} {cy - 12} {cx + 11} {cy + 3}" {lash} stroke-width="3.4"/>'
        if kind == "closed":
            return (f'<path d="M {cx - 11} {cy - 1} Q {cx} {cy + 8} {cx + 11} {cy - 1}" {lash} stroke-width="3"/>'
                    f'<path d="M {cx + 9 * s} {cy + 2} l {3 * s} 3 M {cx + 4 * s} {cy + 5} l {2 * s} 3.5" {lash} stroke-width="1.6"/>')
        if kind == "closed_tear":
            return f'<path d="M {cx - 11} {cy} Q {cx} {cy + 7} {cx + 11} {cy}" {lash} stroke-width="3"/>'
        if kind in ("chev_l", "chev_r"):
            d = 1 if kind == "chev_l" else -1
            return f'<path d="M {cx - 8 * d} {cy - 9} L {cx + 7 * d} {cy} L {cx - 8 * d} {cy + 9}" {lash} stroke-width="3.6"/>'
        if kind == "heart":
            return (f'<path d="{heart_path(cx, cy, 11)}" fill="{HEART}"/>'
                    f'<ellipse cx="{cx - 5}" cy="{cy - 5}" rx="2.6" ry="3.4" fill="#fff" opacity=".9"/>')
        if kind == "swirl":
            return (f'<path d="M {cx} {cy} m -2 0 a 2 2 0 1 1 4 0 a 4 4 0 1 1 -8 0 a 6 6 0 1 1 12 0 a 8 8 0 1 1 -16 0 a 10 10 0 1 1 20 0" '
                    f'{lash} stroke-width="2.2"/>')
        raise KeyError(kind)


class Simple(Character):
    """简约萌系：粗描边、豆豆眼、平涂。"""
    EYES = ((82, 122), (118, 122))
    MOUTH_Y = 135
    BLUSH_Y = 136
    BLUSH_DX = 31
    MOUTH_W = 6
    MOUTH_LINE, MOUTH_STROKE = "#4A3430", 3.2
    BLUSH_OPACITY, BLUSH_FILTER = ".7", ""
    O = "#4A3430"
    SYMBOL_SHIFT = (-100, 0)   # 右上角是橙子发夹，符号放到左上角

    def defs(self):
        return ""

    def back(self):
        o = self.O
        return f'''<g stroke="{o}" stroke-width="4" stroke-linejoin="round" stroke-linecap="round">
          <path d="M 48 118 C 40 150 52 176 66 186 L 70 160 Z M 152 118 C 160 150 148 176 134 186 L 130 160 Z" fill="{HAIR}"/>
          <circle cx="100" cy="116" r="50" fill="{SKIN}"/></g>'''

    def front(self):
        o = self.O
        return f'''<g stroke="{o}" stroke-width="4" stroke-linejoin="round" stroke-linecap="round">
          <path d="M 48 120 C 44 74 70 52 100 52 C 130 52 156 74 152 120 C 144 104 132 96 122 98
                   C 114 86 86 86 78 98 C 68 96 56 104 48 120 Z" fill="{HAIR}"/>
          <g transform="translate(138 60)"><circle r="11" fill="#FFB347"/><circle r="6.5" fill="#FFD27A" stroke-width="0"/>
            <path d="M 0 -6.5 V 6.5 M -5.6 -3.2 L 5.6 3.2 M -5.6 3.2 L 5.6 -3.2" stroke="#F29A2E" stroke-width="1.4"/>
            <path d="M 6 -10 q 8 -7 13 -2 q -6 5 -13 2 Z" fill="#7BC46A"/></g></g>
        <path d="M 66 74 Q 74 66 84 64" stroke="#fff" stroke-width="4" fill="none" opacity=".5" stroke-linecap="round"/>'''

    def nose(self):
        return ""

    def eye(self, kind, cx, cy, s):
        o = self.O
        line = f'stroke="{o}" fill="none" stroke-linecap="round" stroke-linejoin="round"'
        dot = (f'<ellipse cx="{cx}" cy="{cy}" rx="5.5" ry="7.5" fill="{o}"/>'
               f'<circle cx="{cx - 2}" cy="{cy - 3}" r="2" fill="#fff"/>')
        if kind in ("open", "teary"):
            return dot + (f'<circle cx="{cx + 2}" cy="{cy + 3}" r="1.2" fill="#fff"/>' if kind == "teary" else "")
        if kind == "look_up":
            return (f'<ellipse cx="{cx + 1}" cy="{cy - 2}" rx="5.5" ry="7.5" fill="{o}"/>'
                    f'<circle cx="{cx - 1}" cy="{cy - 5}" r="2" fill="#fff"/>')
        if kind == "star":
            return f'<path d="{star_path(cx, cy, 8)}" fill="{o}"/>'
        if kind == "angry":
            return dot + f'<path d="M {cx - 9 * s} {cy - 10} L {cx + 7 * s} {cy - 14}" {line} stroke-width="3.4"/>'
        if kind == "half":
            return f'<path d="M {cx - 7} {cy} H {cx + 7}" {line} stroke-width="3.4"/><path d="M {cx - 5} {cy + 1} Q {cx} {cy + 6} {cx + 5} {cy + 1}" fill="{o}"/>'
        if kind == "surprised":
            return f'<circle cx="{cx}" cy="{cy}" r="8" fill="#fff" stroke="{o}" stroke-width="3.2"/><circle cx="{cx}" cy="{cy}" r="3.2" fill="{o}"/>'
        if kind == "happy":
            return f'<path d="M {cx - 7} {cy + 2} Q {cx} {cy - 8} {cx + 7} {cy + 2}" {line} stroke-width="3.4"/>'
        if kind in ("closed", "closed_tear"):
            return f'<path d="M {cx - 7} {cy - 1} Q {cx} {cy + 6} {cx + 7} {cy - 1}" {line} stroke-width="3.4"/>'
        if kind in ("chev_l", "chev_r"):
            d = 1 if kind == "chev_l" else -1
            return f'<path d="M {cx - 6 * d} {cy - 7} L {cx + 5 * d} {cy} L {cx - 6 * d} {cy + 7}" {line} stroke-width="3.6"/>'
        if kind == "heart":
            return f'<path d="{heart_path(cx, cy, 8.5)}" fill="{HEART}" stroke="{o}" stroke-width="2"/>'
        if kind == "swirl":
            return (f'<path d="M {cx} {cy} m -1.5 0 a 1.5 1.5 0 1 1 3 0 a 3 3 0 1 1 -6 0 a 4.5 4.5 0 1 1 9 0 a 6 6 0 1 1 -12 0" '
                    f'{line} stroke-width="2.4"/>')
        raise KeyError(kind)


STYLES = {"anime": Anime(), "simple": Simple()}


def render(style, expression):
    return STYLES[style].render(expression)
