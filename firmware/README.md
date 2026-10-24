# 板子固件（微雪 ESP32-S3-Touch-AMOLED-2.16）

基于官方 [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) v2.5.2，改动都在 `xiaozhi-v2.5.2-board.patch` 里：

- 界面重新布局：状态栏、电量百分比、聊天卡片，文字不再被圆角屏幕切掉；默认深色
- 唤醒词改成“小橙子”（MultiNet7 自定义命令词）
- 按键：BOOT 单击开始/结束对话、双击亮屏/息屏；KEY3（GPIO18）单击调音量、长按按住说话；PWR 短按亮屏/息屏
- 版本号改成 9.2.5，防止官方 OTA 把自定义固件覆盖回去

## 编译和烧录

```bash
git clone https://github.com/78/xiaozhi-esp32 && cd xiaozhi-esp32 && git checkout v2.5.2
git apply ../xiaozhi-v2.5.2-board.patch
# 路径里不能有中文，ESP-IDF v6.1
. ~/esp/esp-idf/export.sh
python scripts/build.py waveshare/esp32-s3-touch-amoled-2.16 --name esp32-s3-touch-amoled-2.16
esptool --port <串口> --baud 921600 write-flash 0x20000 build/xiaozhi.bin
```

## 皮肤（表情、颜色）

资源分区在 `0x800000`。`characters.py` 是用 SVG 画的角色和 21 个表情，选定的是“简约萌系”橙发女孩：

```bash
# 基础目录：从官方资源包解包得到（字体、唤醒模型、index.json）
python assets_tool.py unpack assets.bin base/
python make_svg_skin.py simple base/ out/      # 需要 rsvg-convert（brew install librsvg）
esptool --port <串口> write-flash 0x800000 out/assets.bin
```

其他脚本：`make_skin.py`（早期的圆团子表情）、`style_samples.py`（三种画风对比图）、`mockup.py`（界面效果图）。
