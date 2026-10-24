"""生成“Claude 工作台”风格的小智皮肤：21 个表情 PNG + index.json，再打包成 assets.bin。

用法：python make_skin.py <官方资源包解包目录> <输出目录>
  官方解包目录里的 srmodels.bin（唤醒词模型）和字体会原样复用。
输出：<输出目录>/skin/（全部资源）、<输出目录>/assets.bin、<输出目录>/preview.png
"""
import json
import math
import os
import shutil
import sys

from PIL import Image, ImageDraw, ImageFilter

from assets_tool import pack

SIZE = 192          # 表情边长；待机时原尺寸显示，对话时固件缩小到 2/3，只缩不放，不会被裁切
SS = 4              # 超采样倍数，画完缩小得到平滑边缘
FACE = (222, 126, 92)       # 陶土橙，和网页端强调色一致
FACE_DARK = (190, 92, 62)   # 轮廓、头顶小卷毛
INK = (52, 36, 30)          # 五官颜色（偏暖的深棕，比纯黑柔和）
WHITE = (255, 255, 255)
BLUSH = (255, 160, 165)
TEAR = (130, 195, 245)
HEART = (240, 80, 100)
TONGUE = (245, 120, 130)

SKIN = {
    "light": {"text_color": "#1F1F1D", "background_color": "#F7F5EF"},
    "dark": {"text_color": "#ECEBE6", "background_color": "#1D1C1A"},
}

EYE_Y = 56          # 眼睛放低、间距拉开，看起来更“幼”
EYE_DX = 16
MOUTH_Y = 67


class Face:
    EY, EDX, MY = EYE_Y, EYE_DX, MOUTH_Y   # 眼睛高度、两眼半间距、嘴巴高度（子类可改）
    def __init__(self, blush=190):
        self.n = SIZE * SS
        self.u = self.n / 100  # 以 100 为单位的坐标系
        self.img = Image.new("RGBA", (self.n, self.n), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)
        self._body()
        if blush:
            self.blush(blush)

    # ---- 基础 ----
    def box(self, x0, y0, x1, y1):
        u = self.u
        return [x0 * u, y0 * u, x1 * u, y1 * u]

    def p(self, x, y):
        return (x * self.u, y * self.u)

    def w(self, v):
        return max(1, int(v * self.u))

    def stroke(self, pts, width=4.2, color=INK):
        """圆头圆角的线，比 PIL 默认的平头线可爱。"""
        pts = [self.p(x, y) for x, y in pts]
        self.d.line(pts, fill=color, width=self.w(width), joint="curve")
        r = width * self.u / 2
        for x, y in (pts[0], pts[-1]):
            self.d.ellipse([x - r, y - r, x + r, y + r], fill=color)

    def arc_pts(self, cx, cy, rx, ry, a0, a1, steps=24):
        return [(cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / steps)),
                 cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / steps))) for i in range(steps + 1)]

    def soft(self, draw_fn, blur):
        layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        draw_fn(ImageDraw.Draw(layer))
        self.img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur * self.u)))

    def _body(self):
        # 稍扁的麻薯形 + 底部阴影 + 左上高光 + 头顶一撮小卷毛
        body = self.box(7, 15, 93, 93)
        self.d.ellipse(body, fill=FACE_DARK)
        self.d.ellipse(self.box(8.2, 16.2, 91.8, 91.8), fill=FACE)
        mask = Image.new("L", self.img.size, 0)
        ImageDraw.Draw(mask).ellipse(self.box(8.2, 16.2, 91.8, 91.8), fill=255)
        shade = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        ImageDraw.Draw(shade).ellipse(self.box(4, 40, 96, 108), fill=FACE_DARK + (50,))
        shade = shade.filter(ImageFilter.GaussianBlur(9 * self.u))
        hl = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        ImageDraw.Draw(hl).ellipse(self.box(24, 24, 40, 31), fill=WHITE + (150,))
        hl = hl.filter(ImageFilter.GaussianBlur(2 * self.u))
        for layer in (shade, hl):
            clipped = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
            clipped.paste(layer, (0, 0), mask)
            self.img.alpha_composite(clipped)
        self.stroke(self.arc_pts(53, 13, 5, 5, 200, 470, 30), width=3.4, color=FACE_DARK)

    def blush(self, alpha=190):
        def draw(d):
            for cx in (50 - 28, 50 + 28):
                d.ellipse(self.box(cx - 8, self.MY - 5, cx + 8, self.MY + 3), fill=BLUSH + (alpha,))
        self.soft(draw, 1.6)

    # ---- 眼睛 ----
    def glossy(self, cx, cy=None, rx=7.6, ry=9.2, look=(0, 0), big_shine=False):
        cy = self.EY if cy is None else cy
        self.d.ellipse(self.box(cx - rx, cy - ry, cx + rx, cy + ry), fill=INK)
        lx, ly = look
        r1 = 3.4 if big_shine else 2.6
        self.d.ellipse(self.box(cx - 2.4 + lx - r1, cy - 3.2 + ly - r1, cx - 2.4 + lx + r1, cy - 3.2 + ly + r1), fill=WHITE)
        self.d.ellipse(self.box(cx + 2.6 + lx - 1.2, cy + 3 + ly - 1.2, cx + 2.6 + lx + 1.2, cy + 3 + ly + 1.2), fill=WHITE)

    def glossy_eyes(self, **kw):
        for cx in (50 - self.EDX, 50 + self.EDX):
            self.glossy(cx, **kw)

    def happy_eye(self, cx, cy=None):   # ^ 形
        cy = self.EY + 1 if cy is None else cy
        self.stroke(self.arc_pts(cx, cy + 3, 6.5, 6.5, 200, 340), width=4.6)

    def closed_eye(self, cx, cy=None):  # ‿ 形
        cy = self.EY - 1 if cy is None else cy
        self.stroke(self.arc_pts(cx, cy - 2, 6.5, 5.5, 20, 160), width=4.2)

    def chevron(self, cx, cy=None, left=True):  # > < 形
        cy = self.EY if cy is None else cy
        s = 1 if left else -1
        self.stroke([(cx - 5 * s, cy - 6), (cx + 5 * s, cy), (cx - 5 * s, cy + 6)], width=4.4)

    def heart(self, cx, cy, s, color=HEART, shine=True):
        pts = []
        for i in range(72):
            t = math.pi * 2 * i / 72
            x = 16 * math.sin(t) ** 3
            y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
            pts.append(self.p(cx + x * s / 16, cy - y * s / 16))
        self.d.polygon(pts, fill=color)
        if shine:
            self.d.ellipse(self.box(cx - s * 0.55, cy - s * 0.5, cx - s * 0.15, cy - s * 0.1), fill=WHITE)

    def star(self, cx, cy, r, color=WHITE):
        pts = []
        for i in range(10):
            a = math.pi / 2 + i * math.pi / 5
            rr = r if i % 2 == 0 else r * 0.45
            pts.append(self.p(cx + rr * math.cos(a), cy - rr * math.sin(a)))
        self.d.polygon(pts, fill=color)

    def swirl(self, cx, cy=None):
        cy = self.EY if cy is None else cy
        pts = [(cx + (0.4 + t * 0.9) * math.cos(t), cy + (0.4 + t * 0.9) * math.sin(t))
               for t in [i / 8 for i in range(0, 60)]]
        self.stroke(pts, width=2.6)

    def brows(self, kind):
        y = self.EY - 13
        if kind == "worried":
            self.stroke([(28, y + 1), (40, y - 3)], width=3.4)
            self.stroke([(60, y - 3), (72, y + 1)], width=3.4)
        elif kind == "angry":
            self.stroke([(27, y - 3), (40, y + 2)], width=3.8)
            self.stroke([(60, y + 2), (73, y - 3)], width=3.8)

    # ---- 嘴巴（都比较小，放在两眼之间偏下） ----
    def cat_mouth(self, y=None):   # ω
        y = self.MY if y is None else y
        self.stroke(self.arc_pts(46.5, y - 1, 3.5, 3.2, 10, 170) + self.arc_pts(53.5, y - 1, 3.5, 3.2, 10, 170), width=2.8)

    def small_smile(self, y=None, half=5.5):
        y = self.MY if y is None else y
        self.stroke(self.arc_pts(50, y - 3, half, 4.5, 20, 160), width=3.6)

    def small_frown(self, y=None, half=5):
        y = self.MY + 2 if y is None else y
        self.stroke(self.arc_pts(50, y + 3, half, 4, 200, 340), width=3.6)

    def open_mouth(self, y=None, half=8, depth=9, tongue=True):
        y = self.MY - 2 if y is None else y
        self.d.chord(self.box(50 - half, y - depth, 50 + half, y + depth), 0, 180, fill=INK)
        if tongue:
            self.d.chord(self.box(50 - half * 0.6, y + depth * 0.35, 50 + half * 0.6, y + depth * 1.05), 180, 360, fill=TONGUE)

    def o_mouth(self, y=None, rx=3.6, ry=4.4):
        y = self.MY + 1 if y is None else y
        self.d.ellipse(self.box(50 - rx, y - ry, 50 + rx, y + ry), fill=INK)

    def wavy(self, y=None, half=8):
        y = self.MY + 1 if y is None else y
        self.stroke([(50 - half + i, y + 1.6 * math.sin(i / (half * 2) * math.pi * 4)) for i in range(0, int(half * 2) + 1)], width=3.2)

    def tongue_out(self, y=None, side=0):
        y = self.MY if y is None else y
        self.d.chord(self.box(46 + side, y - 3, 56 + side, y + 9), 0, 180, fill=TONGUE)
        self.stroke(self.arc_pts(50, y - 4, 6, 4, 20, 160), width=3.4)

    def kiss(self, y=None):
        y = self.MY if y is None else y
        self.stroke(self.arc_pts(50, y - 2.5, 2.6, 2.5, -90, 90) + self.arc_pts(50, y + 2.5, 2.6, 2.5, -90, 90), width=3.2)

    def flat(self, y=None, half=4):
        y = self.MY + 1 if y is None else y
        self.stroke([(50 - half, y), (50 + half, y)], width=3.6)

    # ---- 装饰 ----
    def tear_drop(self, cx, top, length=12):
        u = self.u
        self.d.polygon([self.p(cx, top), self.p(cx - 3.6, top + length * 0.6), self.p(cx + 3.6, top + length * 0.6)], fill=TEAR)
        self.d.ellipse(self.box(cx - 3.7, top + length * 0.4, cx + 3.7, top + length * 0.4 + 7.4), fill=TEAR)
        self.d.ellipse(self.box(cx - 1.8, top + length * 0.55, cx - 0.2, top + length * 0.55 + 1.8), fill=WHITE)

    def tear_stream(self, cx):
        self.d.rounded_rectangle(self.box(cx - 3, self.EY + 5, cx + 3, 86), radius=3 * self.u, fill=TEAR)

    def sweat(self):
        self.tear_drop(82, 30, 14)

    def anger_mark(self, cx=78, cy=30):
        for a in (45, 135, 225, 315):
            dx, dy = math.cos(math.radians(a)), math.sin(math.radians(a))
            self.stroke(self.arc_pts(cx + dx * 6, cy + dy * 6, 3.5, 3.5, a + 90, a + 270, 10), width=2.6, color=HEART)

    def bubble(self, cx=60, cy=66, r=5.5):
        self.d.ellipse(self.box(cx - r, cy - r, cx + r, cy + r), fill=(200, 230, 255, 200), outline=WHITE, width=self.w(1.2))
        self.d.ellipse(self.box(cx - r * 0.5, cy - r * 0.6, cx - r * 0.1, cy - r * 0.2), fill=WHITE)

    def zz(self):
        for x, y, s in ((62, 31, 6), (72, 24, 4.5)):
            self.stroke([(x, y), (x + s, y), (x, y + s), (x + s, y + s)], width=2.4)

    def question(self, cx=70, cy=31):
        self.stroke(self.arc_pts(cx, cy, 4.5, 4.5, 180, 400, 16) + [(cx, cy + 7)], width=3)
        self.d.ellipse(self.box(cx - 1.8, cy + 10, cx + 1.8, cy + 13.6), fill=INK)

    def sunglasses(self):
        u = self.u
        for cx in (50 - self.EDX, 50 + self.EDX):
            self.d.rounded_rectangle(self.box(cx - 11, self.EY - 7, cx + 11, self.EY + 7), radius=6 * u, fill=INK)
            self.d.line(self.box(cx - 7, self.EY - 3, cx - 3, self.EY - 5), fill=WHITE, width=self.w(1.8))
        self.stroke([(45, self.EY - 3), (55, self.EY - 3)], width=3)

    def done(self):
        return self.img.resize((SIZE, SIZE), Image.LANCZOS)


def build_faces(face_cls=None) -> dict[str, Image.Image]:
    face_cls = face_cls or Face
    EY, MY = face_cls.EY, face_cls.MY
    L, R = 50 - face_cls.EDX, 50 + face_cls.EDX
    recipes = {
        "neutral": lambda f: (f.glossy_eyes(), f.flat()),
        "happy": lambda f: (f.glossy_eyes(), f.cat_mouth()),
        "laughing": lambda f: (f.chevron(L, left=True), f.chevron(R, left=False), f.open_mouth()),
        "funny": lambda f: (f.happy_eye(L), f.happy_eye(R), f.open_mouth(), f.tear_drop(80, 44, 9)),
        "sad": lambda f: (f.brows("worried"), f.glossy_eyes(big_shine=True, look=(0, 1)), f.small_frown()),
        "angry": lambda f: (f.brows("angry"), f.glossy_eyes(ry=7), f.small_frown(), f.anger_mark()),
        "crying": lambda f: (f.brows("worried"), f.closed_eye(L, EY + 2), f.closed_eye(R, EY + 2),
                             f.tear_stream(L), f.tear_stream(R), f.open_mouth(y=MY, half=6, depth=6, tongue=False)),
        "loving": lambda f: (f.heart(L, EY, 9), f.heart(R, EY, 9), f.cat_mouth()),
        "embarrassed": lambda f: (f.chevron(L, left=True), f.chevron(R, left=False), f.wavy(), f.blush(230), f.sweat()),
        "surprised": lambda f: (f.glossy_eyes(rx=7.4, ry=9), f.o_mouth()),
        "shocked": lambda f: (f.glossy_eyes(rx=4, ry=4.8), f.o_mouth(y=MY + 2, rx=5, ry=6.5), f.sweat()),
        "thinking": lambda f: (f.glossy_eyes(look=(1.5, -1.5)), f.flat(half=3), f.question()),
        "winking": lambda f: (f.glossy(L), f.happy_eye(R), f.tongue_out(side=1), f.star(82, 34, 5, (255, 210, 90))),
        "cool": lambda f: (f.sunglasses(), f.small_smile(half=4.5)),
        "relaxed": lambda f: (f.closed_eye(L), f.closed_eye(R), f.small_smile()),
        "delicious": lambda f: (f.happy_eye(L), f.happy_eye(R), f.tongue_out(side=3)),
        "kissy": lambda f: (f.closed_eye(L), f.closed_eye(R), f.kiss(), f.heart(76, 30, 6, shine=False)),
        "confident": lambda f: (f.glossy(L, look=(0, 0)), f.glossy(R), f.star(L - 2.4, EY - 3.2, 4.2),
                                f.star(R - 2.4, EY - 3.2, 4.2), f.small_smile(half=6)),
        "sleepy": lambda f: (f.closed_eye(L), f.closed_eye(R), f.o_mouth(rx=2.6, ry=2.6), f.bubble(), f.zz()),
        "silly": lambda f: (f.glossy(L, rx=7.6, ry=9), f.glossy(R, rx=4.6, ry=5.4), f.tongue_out(side=-2)),
        "confused": lambda f: (f.swirl(L), f.swirl(R), f.wavy(half=6)),
    }
    faces = {}
    for name, fn in recipes.items():
        f = face_cls()
        fn(f)
        faces[name] = f.done()
    return faces


def preview(faces: dict[str, Image.Image], path: str) -> None:
    cols, cell = 7, SIZE + 24
    rows = math.ceil(len(faces) / cols)
    sheet = Image.new("RGB", (cols * cell, rows * cell * 2), SKIN["dark"]["background_color"])
    sheet.paste(Image.new("RGB", (cols * cell, rows * cell), SKIN["light"]["background_color"]), (0, rows * cell))
    for half in range(2):
        for i, im in enumerate(faces.values()):
            x, y = (i % cols) * cell + 12, (i // cols) * cell + 12 + half * rows * cell
            sheet.paste(im, (x, y), im)
    sheet.save(path)


def main(face_cls=None):
    src, out = sys.argv[1], sys.argv[2]
    skin_dir = os.path.join(out, "skin")
    shutil.rmtree(skin_dir, ignore_errors=True)
    os.makedirs(skin_dir)

    index = json.load(open(os.path.join(src, "index.json"), encoding="utf-8"))
    for key in ("srmodels", "text_font"):
        if index.get(key):
            shutil.copy(os.path.join(src, index[key]), skin_dir)

    faces = build_faces(face_cls)
    for name, im in faces.items():
        im.save(os.path.join(skin_dir, f"{name}.png"), optimize=True)
    index["emoji_collection"] = [{"name": n, "file": f"{n}.png"} for n in faces]
    # 固件激活阶段会请求 robot_2，官方资源包里也没有；指到 neutral，免得显示空白
    index["emoji_collection"].append({"name": "robot_2", "file": "neutral.png"})
    index["skin"] = SKIN
    json.dump(index, open(os.path.join(skin_dir, "index.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=4)

    pack(skin_dir, os.path.join(out, "assets.bin"))
    preview(faces, os.path.join(out, "preview.png"))


if __name__ == "__main__":
    main()
