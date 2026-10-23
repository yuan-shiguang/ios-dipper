#!/usr/bin/env python3
"""Build a Qualcomm Image.gz-dtb blob with a 4-byte aligned gzip stream.

ABL decompresses the kernel and then looks for the device tree at the end of
the gzip stream. It is not documented whether the offset is the exact number
of bytes gzip consumed or that value rounded up to 4, and the stock dipper
image cannot tell us: its stream happens to be 14362216 bytes, which is already
4-aligned.

So make the question moot - keep the stream length a multiple of 4. Then both
interpretations point at the same byte and the DTB sits flush against the
stream, exactly as in the stock image.

The padding goes into the IMAGE, never into the gzip header.

An earlier version of this tool set FLG.FCOMMENT and put the pad bytes in the
header. That is legal gzip - zlib round-trips it happily, which is why the
round-trip check below never caught it - but ABL's gunzip does not skip
optional header fields, so it read the comment bytes as deflate data and
refused the kernel. Every image whose deflate body was not already 4-aligned
went straight to fastboot with nothing on screen, and the ones that happened
to need no padding booted. That is not a theory: four images with FLG=0x00
boot, three with FLG=0x10 do not, and the only difference between the two
groups is that byte.

Appending a few incompressible bytes to the image and recompressing costs at
most a handful of tries, never touches the header, and lands the extra bytes
past the shim's _end where nothing reads them. mkpad.py has been padding
images exactly this way all along, and those images boot.

Usage:
    mkimage_gz_dtb.py Image dtb out/Image.gz-dtb
"""
import struct
import sys
import zlib

# Effectively incompressible: deflate emits these as literals, so each one
# costs about one byte of stream. Distinct values, not a run - a run would
# compress and might not move the length at all.
PAD = bytes((0x9e, 0x37, 0x51, 0xa2, 0x6c, 0x0f, 0xd8, 0x43,
             0xb1, 0x7a, 0x25, 0xef, 0x58, 0x93, 0xca, 0x16))


def gzip_plain(data):
    """gzip `data` with a bare 10-byte header: no FLG bits set at all."""
    co = zlib.compressobj(9, zlib.DEFLATED, -15)   # raw deflate, no gzip wrapper
    body = co.compress(data) + co.flush()
    hdr = b"\x1f\x8b" + b"\x08" + b"\x00" + b"\x00\x00\x00\x00" + b"\x02\x03"
    return hdr + body + struct.pack("<II", zlib.crc32(data) & 0xffffffff,
                                    len(data) & 0xffffffff)


def gzip_aligned(data, align=4):
    """Return (stream, padded_image) with len(stream) a multiple of `align`."""
    for extra in range(len(PAD) + 1):
        blob = data + PAD[:extra] if extra else data
        gz = gzip_plain(blob)
        if len(gz) % align == 0:
            return gz, blob
    raise SystemExit("could not reach %d-byte gzip alignment in %d tries"
                     % (align, len(PAD) + 1))


def main():
    if len(sys.argv) != 4:
        raise SystemExit(__doc__.strip().splitlines()[-1].strip())
    image = open(sys.argv[1], "rb").read()
    dtb = open(sys.argv[2], "rb").read()

    if dtb[:4] != b"\xd0\x0d\xfe\xed":
        raise SystemExit("%s is not a flattened device tree" % sys.argv[2])

    gz, blob = gzip_aligned(image)
    out = gz + dtb
    open(sys.argv[3], "wb").write(out)

    # Round-trip check: this is exactly what ABL does.
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    back = d.decompress(out)
    rest = d.unused_data
    assert back == blob, "gzip round-trip mismatch"
    assert rest[:4] == b"\xd0\x0d\xfe\xed", "DTB not flush against the stream"

    # The whole point: no optional header field, ever.
    assert gz[3] == 0, "gzip FLG must be 0, got 0x%02x" % gz[3]
    assert len(gz) % 4 == 0, "gzip stream is not 4-aligned: %d" % len(gz)

    print("Image %d (+%d pad) -> gzip %d (len mod 4 = %d, FLG=0x%02x)"
          " + dtb %d = %d"
          % (len(image), len(blob) - len(image), len(gz), len(gz) % 4, gz[3],
             len(dtb), len(out)))


if __name__ == "__main__":
    main()
