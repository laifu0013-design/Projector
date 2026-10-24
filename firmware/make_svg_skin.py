"""用 characters.py 的 SVG 角色生成小智皮肤：21 个表情（192px PNG）+ index.json → assets.bin。

用法：python make_svg_skin.py <画风 anime|simple> <基础资源目录> <输出目录>
  基础资源目录：解包后的 generated_assets（里面的 srmodels.bin、字体、index.json 带唤醒词配置）。
需要 rsvg-convert（brew install librsvg）。
"""
import json
import os
import shutil
import subprocess
import sys

from PIL import Image

import characters
from assets_tool import pack
from make_skin import SIZE, SKIN, preview


def main():
    style, src, out = sys.argv[1:4]
    skin_dir = os.path.join(out, "skin")
    shutil.rmtree(skin_dir, ignore_errors=True)
    os.makedirs(skin_dir)

    index = json.load(open(os.path.join(src, "index.json"), encoding="utf-8"))
    for key in ("srmodels", "text_font"):
        if index.get(key):
            shutil.copy(os.path.join(src, index[key]), skin_dir)

    faces = {}
    for name in characters.EXPRESSIONS:
        svg_path = os.path.join(out, f"{name}.svg")
        png_path = os.path.join(skin_dir, f"{name}.png")
        open(svg_path, "w", encoding="utf-8").write(characters.render(style, name))
        subprocess.run(["rsvg-convert", "-w", str(SIZE), "-h", str(SIZE), svg_path, "-o", png_path], check=True)
        faces[name] = Image.open(png_path).convert("RGBA")
        faces[name].save(png_path, optimize=True)

    index["emoji_collection"] = [{"name": n, "file": f"{n}.png"} for n in faces]
    # 固件激活阶段会请求 robot_2，官方资源包里也没有；指到 neutral，免得显示空白
    index["emoji_collection"].append({"name": "robot_2", "file": "neutral.png"})
    index["skin"] = SKIN
    json.dump(index, open(os.path.join(skin_dir, "index.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=4)

    pack(skin_dir, os.path.join(out, "assets.bin"))
    preview(faces, os.path.join(out, "preview.png"))


if __name__ == "__main__":
    main()
