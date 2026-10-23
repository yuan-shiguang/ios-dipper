#!/usr/bin/env python3
"""Build a Qualcomm Image.gz-dtb blob with a 4-byte aligned gzip stream.

ABL decompresses the kernel and then looks for the device tree at the end of
the gzip stream. It is not documented whether the offset is the exact number
of bytes gzip consumed or that value rounded up to 4, and the stock dipper
image cannot tell us: its stream happens to be 14362216 bytes, which is already
4-aligned.

So make the question moot - pad the gzip header with an FCOMMENT field until
the stream length is a multiple of 4. Then both interpretations point at the
same byte and the DTB sits flush against the stream, exactly as in the stock
image.

Usage:
    mkimage_gz_dtb.py Image dtb out/Image.gz-dtb
"""
import struct
import sys
import zlib


def gzip_aligned(data, align=4):
    """gzip `data`, padding the header so the stream length is align-aligned."""
    co = zlib.compressobj(9, zlib.DEFLATED, -15)   # raw deflate, no gzip wrapper
    body = co.compress(data) + co.flush()

    # header 10 + deflate + crc32 4 + isize 4
    total = 10 + len(body) + 8
    pad = (-total) % align

    flg = 0
    extra = b""
    if pad:
        # FCOMMENT (FLG bit 4): comment bytes terminated by NUL, so it costs
        # exactly `pad` bytes.
        flg |= 0x10
        extra = b"A" * (pad - 1) + b"\x00"

    hdr = b"\x1f\x8b" + b"\x08" + bytes([flg]) + b"\x00\x00\x00\x00" + b"\x02\x03"
    out = hdr + extra + body + struct.pack("<II", zlib.crc32(data) & 0xffffffff,
                                           len(data) & 0xffffffff)
    assert len(out) % align == 0, "padding maths is wrong: %d" % len(out)
    return out


def main():
    if len(sys.argv) != 4:
        raise SystemExit(__doc__.strip().splitlines()[-1].strip())
    image = open(sys.argv[1], "rb").read()
    dtb = open(sys.argv[2], "rb").read()

    if dtb[:4] != b"\xd0\x0d\xfe\xed":
        raise SystemExit("%s is not a flattened device tree" % sys.argv[2])

    gz = gzip_aligned(image)
    out = gz + dtb
    open(sys.argv[3], "wb").write(out)

    # Round-trip check: this is exactly what ABL does.
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    back = d.decompress(out)
    rest = d.unused_data
    assert back == image, "gzip round-trip mismatch"
    assert rest[:4] == b"\xd0\x0d\xfe\xed", "DTB not flush against the stream"

    print("Image %d -> gzip %d (len mod 4 = %d) + dtb %d = %d"
          % (len(image), len(gz), len(gz) % 4, len(dtb), len(out)))


if __name__ == "__main__":
    main()
