"""渲染新界面效果图（480×480，按实物圆角裁切），用于和用户确认布局。

用法：python mockup.py <表情 png 目录> <输出 png>
"""
import sys

from PIL import Image, ImageDraw, ImageFont

W = 480
RADIUS = 44          # 面板圆角，按照片估计，装好环境后用测试图实测
INSET = 52           # 状态栏左右内缩，避开圆角
BG = (22, 21, 19)
CARD = (38, 36, 33)
TEXT = (236, 235, 230)
MUTED = (150, 146, 138)
ACCENT = (222, 126, 92)
OK = (120, 200, 130)
WARN = (240, 190, 90)

FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def f(size):
    return ImageFont.truetype(FONT, size)


def wifi(d, x, y):
    for i, r in enumerate((16, 11, 6)):
        d.arc([x - r, y - r, x + r, y + r], 225, 315, fill=TEXT, width=3)
    d.ellipse([x - 2.5, y - 2.5, x + 2.5, y + 2.5], fill=TEXT)


def battery(d, x, y, pct):
    d.rounded_rectangle([x, y - 9, x + 30, y + 9], 4, outline=TEXT, width=2)
    d.rectangle([x + 31, y - 4, x + 34, y + 4], fill=TEXT)
    d.rectangle([x + 4, y - 5, x + 4 + 22 * pct / 100, y + 5], fill=OK if pct > 20 else WARN)


def status_bar(d, label="16:01", pct=85):
    y = 40
    wifi(d, INSET + 16, y + 6)
    d.text((W / 2, y), label, font=f(24), fill=TEXT, anchor="mm")
    d.text((W - INSET - 40, y), f"{pct}%", font=f(18), fill=MUTED, anchor="rm")
    battery(d, W - INSET - 34, y, pct)


def emoji(img, path, cy, size):
    e = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
    img.alpha_composite(e, (W // 2 - size // 2, cy - size // 2))


def bubble(d, y, who, text, color):
    d.text((INSET, y), who, font=f(18), fill=MUTED)
    d.multiline_text((INSET, y + 26), text, font=f(26), fill=color, spacing=8)


def pill(d, y, dot, text):
    w = d.textlength(text, font=f(19)) + 44
    x0 = W / 2 - w / 2
    d.rounded_rectangle([x0, y - 18, x0 + w, y + 18], 18, fill=CARD)
    d.ellipse([x0 + 14, y - 5, x0 + 24, y + 5], fill=dot)
    d.text((x0 + 32, y), text, font=f(19), fill=TEXT, anchor="lm")


def frame(state, emo_dir):
    img = Image.new("RGBA", (W, W), BG)
    d = ImageDraw.Draw(img)
    status_bar(d)
    if state == "idle":
        emoji(img, f"{emo_dir}/relaxed.png", 205, 200)
        d.text((W / 2, 345), "说「你好小智」唤醒我", font=f(26), fill=MUTED, anchor="mm")
        pill(d, 420, OK, "电脑在线 · 空闲")
    elif state == "chat":
        emoji(img, f"{emo_dir}/happy.png", 150, 130)
        d.rounded_rectangle([INSET - 18, 226, W - INSET + 18, 404], 18, fill=CARD)
        bubble(d, 238, "你", "帮我找一下 D 盘里上个月的合同", MUTED)
        bubble(d, 300, "小智", "找到 3 份合同，最新的是\n《设备采购合同 0915》", TEXT)
        pill(d, 432, ACCENT, "Claude 正在处理 · 第 4 步")
    else:
        emoji(img, f"{emo_dir}/thinking.png", 128, 110)
        d.rounded_rectangle([INSET - 18, 200, W - INSET + 18, 400], 18, fill=CARD, outline=WARN, width=2)
        d.ellipse([INSET, 216, INSET + 22, 238], fill=WARN)
        d.text((INSET + 11, 227), "!", font=f(18), fill=BG, anchor="mm")
        d.text((INSET + 30, 227), "需要确认", font=f(20), fill=WARN, anchor="lm")
        d.multiline_text((INSET, 248), "删除文件：\nD:\\旧文件\\报价单_v1.xlsx", font=f(26), fill=TEXT, spacing=8)
        for i, (label, col) in enumerate((("拒绝", CARD), ("同意", ACCENT))):
            x0 = INSET + i * 196
            d.rounded_rectangle([x0, 336, x0 + 180, 386], 25, fill=col, outline=MUTED if i == 0 else col, width=2)
            d.text((x0 + 90, 361), label, font=f(24), fill=TEXT, anchor="mm")
        pill(d, 430, WARN, "点按钮，或直接说“同意”")
    # 按实物圆角裁切，角外画成边框黑色
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, W - 1], RADIUS, fill=255)
    out = Image.new("RGBA", (W, W), (0, 0, 0, 255))
    out.paste(img, (0, 0), mask)
    return out


def main():
    emo_dir, out = sys.argv[1], sys.argv[2]
    states = ["idle", "chat", "confirm"]
    gap = 30
    sheet = Image.new("RGB", (W * 3 + gap * 4, W + gap * 2 + 40), (235, 232, 225))
    d = ImageDraw.Draw(sheet)
    for i, (s, title) in enumerate(zip(states, ("待机", "对话 / Claude 干活中", "等待确认（需自建服务端）"))):
        x = gap + i * (W + gap)
        sheet.paste(frame(s, emo_dir).convert("RGB"), (x, gap))
        d.text((x + W / 2, W + gap + 22), title, font=f(22), fill=(60, 55, 50), anchor="mm")
    sheet.save(out)


if __name__ == "__main__":
    main()
