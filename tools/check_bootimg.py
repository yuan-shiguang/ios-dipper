#!/usr/bin/env python3
"""Verify that a shim boot image is one the target ABL will actually accept.

dipper can only be started with `fastboot flash boot`, and ABL does not
validate anything at flash time - a wrong image is written happily and then
refused at boot, which shows up as "black screen, back in fastboot". This
script is the cheap way to find that out before the flash: it re-parses the
image the same way ABL does and compares the fields that matter against the
stock layout.

Checks, in the order ABL trips over them:

  header     magic, page size, header version
  addresses  kernel_addr / ramdisk_addr / tags_addr (offset from base)
  kernel     gzip magic, ARM64 Image magic after decompression
  dtb        trees flush against the end of the gzip stream (Image.gz-dtb),
             and for dipper at least one with a board-id ABL can match
  ramdisk    non-empty, gzip or newc cpio

Usage:
    check_bootimg.py boot.img --board dipper [--dtbs dtb/dipper/dtbs.bin]
                              [--ramdisk out/ramdisk.bin]

Exit status is 0 only if every check passed.
"""
import argparse
import struct
import sys
import zlib

PAGE = 4096
ARM64_MAGIC_OFF = 0x38
DTB_MAGIC = b"\xd0\x0d\xfe\xed"
GZIP_MAGIC = b"\x1f\x8b"

# dipper board-ids: ABL picks the appended tree whose qcom,board-id matches the
# phone. If the tail has none of these, ABL refuses the image before jumping.
DIPPER_BOARD_IDS = {0x1e, 0x1f, 0x24, 0x25, 0x26, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37}

# Measured from the stock dipper boot.img (MIUI 12 / Android 10, 2020-12).
EXPECT = {
    "dipper": {"header": 1, "kernel_addr": 0x8000, "ramdisk_addr": 0x1000000,
               "tags_addr": 0x100, "page": PAGE, "kernel": "gz-dtb"},
}


def split_trees(blob):
    """Split a run of concatenated FDTs into [(offset, size)]."""
    out, pos = [], 0
    while pos + 8 <= len(blob):
        if blob[pos:pos + 4] != DTB_MAGIC:
            break
        total, = struct.unpack_from(">I", blob, pos + 4)
        if total < 0x30 or pos + total > len(blob):
            break
        out.append((pos, total))
        pos += total
    return out


def board_id(blob, base):
    """qcom,board-id of the FDT at `base` (root node), or None.

    FDT offsets are relative to the tree start and the struct block is aligned
    relative to it too, so everything here works on relative offsets - the
    trees in the tail are packed back to back and most bases are not 4-aligned.
    """
    off_struct, off_strings = struct.unpack_from(">II", blob, base + 8)
    p, depth, found = off_struct, 0, None
    while True:
        tok, = struct.unpack_from(">I", blob, base + p)
        p += 4
        if tok == 1:
            depth += 1
            e = blob.index(b"\0", base + p)
            p = ((e - base) + 1 + 3) & ~3
        elif tok == 2:
            depth -= 1
        elif tok == 3:
            ln, nameoff = struct.unpack_from(">II", blob, base + p)
            p += 8
            val = blob[base + p:base + p + ln]
            p = (p + ln + 3) & ~3
            if depth == 1:
                e = blob.index(b"\0", base + off_strings + nameoff)
                if blob[base + off_strings + nameoff:e] == b"qcom,board-id" and ln >= 4:
                    found = struct.unpack_from(">I", val, 0)[0]
        elif tok == 4:
            continue
        elif tok == 9:
            break
        else:
            return None
    return found


class Bad(Exception):
    pass


def parse(data):
    if data[:8] != b"ANDROID!":
        raise Bad("no ANDROID! magic: this is not a boot image at all")
    f = struct.unpack_from("<10I", data, 8)
    h = {
        "kernel_size": f[0], "kernel_addr": f[1],
        "ramdisk_size": f[2], "ramdisk_addr": f[3],
        "second_size": f[4], "second_addr": f[5],
        "tags_addr": f[6], "page_size": f[7],
        "header_version": f[8], "os_version": f[9],
    }
    h["name"] = data[48:64].split(b"\0")[0].decode("ascii", "replace")
    h["cmdline"] = data[64:576].split(b"\0")[0].decode("ascii", "replace")
    page = h["page_size"]
    if page not in (2048, 4096, 8192, 16384):
        raise Bad("page_size %d is not a power-of-two Android page size" % page)
    off = page
    h["kernel"] = data[off:off + h["kernel_size"]]
    off += (h["kernel_size"] + page - 1) // page * page
    h["ramdisk"] = data[off:off + h["ramdisk_size"]]
    off += (h["ramdisk_size"] + page - 1) // page * page
    h["second"] = data[off:off + h["second_size"]]
    if h["header_version"] >= 2:
        # v2 header lives at the start of the image, after the v1 block.
        base = 8 + 1632
        dtb_size, dtb_addr = struct.unpack_from("<IQ", data, base)
        h["dtb_size"], h["dtb_addr"] = dtb_size, dtb_addr
        # The v2 dtb sits after the second stage, page aligned.
        off += (h["second_size"] + page - 1) // page * page
        h["dtb"] = data[off:off + dtb_size]
    else:
        h["dtb_size"], h["dtb_addr"], h["dtb"] = 0, 0, b""
    return h


def check_kernel_image(img):
    if len(img) < 0x40:
        raise Bad("decompressed kernel is %d bytes, too short for an arm64 Image" % len(img))
    if img[ARM64_MAGIC_OFF:ARM64_MAGIC_OFF + 4] != b"ARMd":
        raise Bad("decompressed kernel has no ARM64 Image magic at 0x38 "
                  "(found %r) - ABL would jump into garbage" % img[ARM64_MAGIC_OFF:ARM64_MAGIC_OFF + 4])
    text_offset, image_size = struct.unpack_from("<QQ", img, 8)
    if text_offset == 0:
        raise Bad("text_offset is 0; the arm64 Image header requires a legal value "
                  "(dipper's stock kernel uses 0x80000)")
    if image_size > len(img) + 0x100000:
        raise Bad("image_size 0x%x does not fit the decompressed %d bytes" % (image_size, len(img)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--board", default="dipper", choices=sorted(EXPECT))
    ap.add_argument("--dtb")
    ap.add_argument("--dtbs", help="whole appended tree collection (dipper)")
    ap.add_argument("--ramdisk")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    want = EXPECT[a.board]
    data = open(a.image, "rb").read()
    ok = []

    try:
        h = parse(data)
        if h["header_version"] != want["header"]:
            raise Bad("header_version is %d, %s needs %d"
                      % (h["header_version"], a.board, want["header"]))
        ok.append("header v%d, page %d" % (h["header_version"], h["page_size"]))

        if h["page_size"] != want["page"]:
            raise Bad("page_size is %d, stock uses %d" % (h["page_size"], want["page"]))
        for k in ("kernel_addr", "ramdisk_addr", "tags_addr"):
            if h[k] != want[k]:
                raise Bad("%s is 0x%x, stock uses 0x%x" % (k, h[k], want[k]))
        ok.append("addresses kernel=0x%x ramdisk=0x%x tags=0x%x"
                  % (h["kernel_addr"], h["ramdisk_addr"], h["tags_addr"]))

        k = h["kernel"]
        if not k:
            raise Bad("kernel field is empty")
        if k[:2] == GZIP_MAGIC:
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            try:
                img = d.decompress(k)
            except zlib.error as e:
                raise Bad("gzip stream is corrupt: %s" % e)
            rest = d.unused_data
            check_kernel_image(img)
            ok.append("kernel gzip %d -> %d bytes, ARM64 Image ok" % (len(k), len(img)))
            if want["kernel"] == "gz-dtb":
                if rest[:4] != DTB_MAGIC:
                    raise Bad("no device tree flush against the gzip stream (found %r at +%d); "
                              "ABL looks for it exactly there"
                              % (rest[:4], len(k) - len(rest)))
                trees = split_trees(rest)
                if not trees:
                    raise Bad("the bytes after the stream do not parse as a device tree")
                end = trees[-1][0] + trees[-1][1]
                if end != len(rest):
                    raise Bad("%d device tree(s) cover %d of %d bytes after the stream"
                              % (len(trees), end, len(rest)))
                ids = [board_id(rest, o) for o, _ in trees]
                known = sorted({i for i in ids if i is not None})
                ok.append("dtb %d tree(s) flush after the stream, board-ids %s"
                          % (len(trees), " ".join("0x%x" % i for i in known) or "none"))
                if a.board == "dipper":
                    hit = sorted(set(known) & DIPPER_BOARD_IDS)
                    if not hit:
                        raise Bad("none of the %d appended trees has a dipper board-id "
                                  "(0x1e/0x1f/0x24/0x25/0x26/0x32..0x37); ABL would find "
                                  "no match and drop to fastboot" % len(trees))
                    ok.append("dipper board-ids covered: %s"
                              % " ".join("0x%x" % i for i in hit))
                if a.dtbs:
                    ref = open(a.dtbs, "rb").read()
                    if ref != rest:
                        raise Bad("appended trees differ from %s" % a.dtbs)
                elif a.dtb:
                    ref = open(a.dtb, "rb").read()
                    if ref != rest[:len(ref)]:
                        raise Bad("first appended tree differs from %s" % a.dtb)
            elif want["kernel"] == "image":
                if rest:
                    raise Bad("%d unexpected bytes after the gzip stream" % len(rest))
        else:
            if want["kernel"] == "gz-dtb":
                raise Bad("kernel is not gzip (starts %r); dipper's ABL wants "
                          "Image.gz-dtb" % k[:4])
            check_kernel_image(k)
            ok.append("kernel raw Image %d bytes" % len(k))

        if h["ramdisk_size"] == 0:
            raise Bad("ramdisk is empty; ABL rejects a zero-size ramdisk on some builds")
        rd = h["ramdisk"]
        if rd[:2] == GZIP_MAGIC:
            body = zlib.decompress(rd, 16 + zlib.MAX_WBITS)
            kind = "gzip->%d" % len(body)
        elif rd[:6] in (b"070701", b"070702"):
            kind = "newc"
        else:
            raise Bad("ramdisk is neither gzip nor newc cpio (starts %r)" % rd[:6])
        ok.append("ramdisk %d bytes (%s)" % (h["ramdisk_size"], kind))

        if a.ramdisk:
            ref = open(a.ramdisk, "rb").read()
            if ref != rd:
                raise Bad("ramdisk in the image differs from %s" % a.ramdisk)

        if h["header_version"] >= 2 and h["dtb_size"]:
            if h["dtb"][:4] != DTB_MAGIC:
                raise Bad("v2 dtb field does not start with a FDT magic")
            ok.append("v2 dtb %d bytes at 0x%x" % (h["dtb_size"], h["dtb_addr"]))
    except Bad as e:
        print("FAIL %s: %s" % (a.image, e), file=sys.stderr)
        return 1

    if not a.quiet:
        print("ok   %s (%d bytes, %s)" % (a.image, len(data), a.board))
        for line in ok:
            print("     " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
