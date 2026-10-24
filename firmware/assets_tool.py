"""小智固件 assets 分区的解包 / 打包工具（格式与上游 scripts/spiffs_assets/spiffs_assets_gen.py 一致）。

解包：python assets_tool.py unpack assets.bin 输出目录
打包：python assets_tool.py pack   输入目录 assets.bin
     （输入目录里文件顺序按“扩展名、文件名”排序，和上游一致）
"""
import os
import sys

NAME_LEN = 32


def unpack(src: str, out_dir: str) -> None:
    data = open(src, "rb").read()
    count = int.from_bytes(data[0:4], "little")
    length = int.from_bytes(data[8:12], "little")
    body = data[12:12 + length]
    table, blobs = body[:count * (NAME_LEN + 12)], body[count * (NAME_LEN + 12):]
    os.makedirs(out_dir, exist_ok=True)
    for i in range(count):
        e = table[i * (NAME_LEN + 12):(i + 1) * (NAME_LEN + 12)]
        name = e[:NAME_LEN].rstrip(b"\0").decode()
        size = int.from_bytes(e[NAME_LEN:NAME_LEN + 4], "little")
        off = int.from_bytes(e[NAME_LEN + 4:NAME_LEN + 8], "little")
        w = int.from_bytes(e[NAME_LEN + 8:NAME_LEN + 10], "little")
        h = int.from_bytes(e[NAME_LEN + 10:NAME_LEN + 12], "little")
        assert blobs[off:off + 2] == b"\x5a\x5a", f"{name}: 缺少 0x5A5A 前缀"
        open(os.path.join(out_dir, name), "wb").write(blobs[off + 2:off + 2 + size])
        print(f"{name:<36} {size:>9} 字节  {w}x{h}")


def pack(src_dir: str, out: str) -> None:
    names = sorted((n for n in os.listdir(src_dir) if n != "config.json" and not n.startswith(".")),
                   key=lambda n: (os.path.splitext(n)[1], os.path.splitext(n)[0]))
    table, blobs = bytearray(), bytearray()
    for n in names:
        assert len(n.encode()) <= NAME_LEN, f"文件名太长：{n}"
        raw = open(os.path.join(src_dir, n), "rb").read()
        w = h = 0  # 官方资源包里图片宽高也是 0，固件运行时自己解析
        table += n.encode().ljust(NAME_LEN, b"\0") + len(raw).to_bytes(4, "little") \
            + len(blobs).to_bytes(4, "little") + w.to_bytes(2, "little") + h.to_bytes(2, "little")
        blobs += b"\x5a\x5a" + raw
    body = table + blobs
    header = len(names).to_bytes(4, "little") + (sum(body) & 0xFFFF).to_bytes(4, "little") + len(body).to_bytes(4, "little")
    open(out, "wb").write(header + body)
    print(f"打包 {len(names)} 个文件 → {out}（{len(header) + len(body)} 字节）")


if __name__ == "__main__":
    cmd, a, b = sys.argv[1:4]
    {"unpack": unpack, "pack": pack}[cmd](a, b)
