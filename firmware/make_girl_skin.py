"""“小橙子”Q 版少女皮肤：橙色双丸子头 + 小绿叶发夹，21 个表情复用 make_skin 的表情配方。

用法：python make_girl_skin.py <官方资源包解包目录> <输出目录>（输出同 make_skin.py）
"""
import math

from PIL import Image, ImageDraw

import make_skin
from make_skin import INK, WHITE, Face

HAIR = (238, 128, 72)
HAIR_DARK = (196, 88, 48)
SKIN = (255, 231, 214)
SKIN_LINE = (226, 172, 150)
IRIS = (176, 98, 62)
LEAF = (126, 196, 96)
LEAF_DARK = (76, 146, 62)


class GirlFace(Face):
    EY, EDX, MY = 64, 12, 77   # 脸比圆团子小，五官整体下移、收拢

    def _filled(self, shapes, color, outline=None, grow=1.3):
        """画一组椭圆；给了 outline 就先画放大一圈的描边色，得到整体外轮廓。"""
        if outline:
            for x0, y0, x1, y1 in shapes:
                self.d.ellipse(self.box(x0 - grow, y0 - grow, x1 + grow, y1 + grow), fill=outline)
        for x0, y0, x1, y1 in shapes:
            self.d.ellipse(self.box(x0, y0, x1, y1), fill=color)

    def _leaf(self, cx, cy, length, angle):
        a = math.radians(angle)
        pts = []
        for i in range(40):
            t = math.pi * 2 * i / 40
            x, y = length / 2 * math.cos(t), length / 4.2 * math.sin(t) * (1 - 0.35 * math.cos(t))
            pts.append(self.p(cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a)))
        self.d.polygon(pts, fill=LEAF, outline=LEAF_DARK)
        half = length / 2 * 0.8
        self.stroke([(cx - half * math.cos(a), cy - half * math.sin(a)), (cx + half * math.cos(a), cy + half * math.sin(a))],
                    width=1.2, color=LEAF_DARK)

    def _body(self):
        # 丸子单独描边、高过头顶，和头之间用发圈隔开，免得看着像熊耳朵
        buns = [(5, 3, 27, 25), (73, 3, 95, 25)]
        self._filled(buns, HAIR, outline=HAIR_DARK)
        for cx in (16, 84):
            self.stroke(self.arc_pts(cx - 2, 11, 6, 6, 200, 260, 8), width=2, color=(255, 190, 140))
        back = [(13, 20, 87, 90)]
        locks = [(10, 44, 27, 93), (73, 44, 90, 93)]
        self._filled(back + locks, HAIR, outline=HAIR_DARK)
        for x0, y0, x1, y1 in ((17, 20, 29, 28), (71, 20, 83, 28)):  # 发圈
            self.d.rounded_rectangle(self.box(x0, y0, x1, y1), radius=3 * self.u, fill=(226, 74, 88))

        self._filled([(23, 32, 77, 89)], SKIN, outline=SKIN_LINE, grow=0.9)

        # 刘海：几块椭圆叠出波浪形下沿，两侧盖住脸颊边缘
        bangs = [(20, 16, 80, 40), (23, 22, 45, 47), (38, 21, 62, 45), (55, 22, 77, 47), (17, 28, 33, 58), (67, 28, 83, 58)]
        self._filled(bangs, HAIR, outline=HAIR_DARK, grow=1.0)
        self._filled(bangs, HAIR)
        self.stroke(self.arc_pts(42, 34, 14, 10, 205, 250, 10), width=2.4, color=(255, 190, 140))

        self._leaf(88, 4, 13, -35)

    def blush(self, alpha=190):
        def draw(d):
            for cx in (50 - 19, 50 + 19):
                d.ellipse(self.box(cx - 6, self.MY - 7, cx + 6, self.MY - 2), fill=make_skin.BLUSH + (alpha,))
        self.soft(draw, 1.3)

    def glossy(self, cx, cy=None, rx=7.6, ry=9.2, look=(0, 0), big_shine=False):
        # 动漫风大眼：深色外圈 + 偏暖的下半虹膜 + 瞳孔 + 两个高光 + 上睫毛
        cy = self.EY if cy is None else cy
        rx, ry = rx * 0.74, ry * 0.86
        lx, ly = look
        self.d.ellipse(self.box(cx - rx, cy - ry, cx + rx, cy + ry), fill=INK)
        self.d.chord(self.box(cx - rx * 0.78, cy - ry * 0.55, cx + rx * 0.78, cy + ry * 0.9), 0, 180, fill=IRIS)
        self.d.ellipse(self.box(cx - rx * 0.35 + lx, cy - ry * 0.25 + ly, cx + rx * 0.35 + lx, cy + ry * 0.45 + ly), fill=INK)
        r1 = rx * (0.5 if big_shine else 0.4)
        self.d.ellipse(self.box(cx - rx * 0.35 + lx - r1, cy - ry * 0.4 + ly - r1, cx - rx * 0.35 + lx + r1, cy - ry * 0.4 + ly + r1), fill=WHITE)
        self.d.ellipse(self.box(cx + rx * 0.4 + lx - 1, cy + ry * 0.45 + ly - 1, cx + rx * 0.4 + lx + 1, cy + ry * 0.45 + ly + 1), fill=WHITE)
        side = -1 if cx < 50 else 1
        self.stroke(self.arc_pts(cx, cy + 1, rx + 1.6, ry + 1.2, 200, 340, 16) + [(cx + side * (rx + 3.2), cy - ry + 1.5)], width=2.6)

    def brows(self, kind):
        # 眉毛被刘海挡住大半，只露出刘海下沿一小截
        y = self.EY - 11
        for cx in (50 - self.EDX, 50 + self.EDX):
            s = -1 if cx < 50 else 1
            if kind == "worried":
                self.stroke([(cx - 5 * s, y), (cx + 4 * s, y - 2.5)], width=2.6)
            elif kind == "angry":
                self.stroke([(cx - 5 * s, y - 2.5), (cx + 4 * s, y + 1)], width=2.8)

    def sunglasses(self):
        for cx in (50 - self.EDX, 50 + self.EDX):
            self.d.rounded_rectangle(self.box(cx - 9, self.EY - 6, cx + 9, self.EY + 6), radius=5 * self.u, fill=INK)
            self.d.line(self.box(cx - 6, self.EY - 3, cx - 2, self.EY - 4.5), fill=WHITE, width=self.w(1.6))
        self.stroke([(47, self.EY - 3), (53, self.EY - 3)], width=2.4)

    def sweat(self):
        self.tear_drop(73, 46, 11)

    def bubble(self, cx=58, cy=78, r=4.5):
        super().bubble(cx, cy, r)

    def tear_stream(self, cx):
        self.d.rounded_rectangle(self.box(cx - 2.5, self.EY + 5, cx + 2.5, 88), radius=2.5 * self.u, fill=make_skin.TEAR)


if __name__ == "__main__":
    make_skin.main(GirlFace)
