#!/usr/bin/env python3
"""Extract the device tree blob(s) from a Qualcomm boot image.

Qualcomm packs the kernel as Image.gz-dtb: a gzip stream holding the ARM64
Image, immediately followed by the device tree. On dipper that tail is not one
tree but a *collection*: 65 flattened device trees back to back, one per
board-id / panel / hardware revision, and ABL picks the entry whose
qcom,board-id matches the phone it is running on. If none matches it gives up
before it ever jumps to the kernel - which on a device that cannot be started
with `fastboot boot` looks like "black screen, back in fastboot".

Appending only the first tree (what an earlier build did) therefore boots on
exactly one hardware revision - and that first entry is not even a dipper: it
is board-id 0x2b, "Xiaomi Polaris P3".

So by default this writes the whole tail verbatim, which is what stock does
and what ABL expects.

Usage:
    extract_dtb.py boot.img dtb/dipper/dtbs.bin        # whole tail (default)
    extract_dtb.py boot.img dtb/dipper/dtb --single    # first tree only
    extract_dtb.py boot.img --list                     # just describe them
"""
import struct
import sys
import zlib

FDT_MAGIC = b"\xd0\x0d\xfe\xed"
# Board ids that belong to dipper. Everything else in the tail is another
# device that shares the sdm845 kernel (polaris, ursa, equuleus) or a
# Qualcomm reference board.
DIPPER_BOARD_IDS = {0x1e, 0x1f, 0x24, 0x25, 0x26, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37}


def kernel_field(data):
    """Return the raw kernel blob (Image.gz-dtb or plain Image)."""
    if data[:8] != b"ANDROID!":
        raise SystemExit("not an Android boot image")
    kernel_size, = struct.unpack_from("<I", data, 8)
    page_size, = struct.unpack_from("<I", data, 36)
    return data[page_size:page_size + kernel_size]


def split_tail(blob):
    """Split a run of concatenated FDTs. Returns [(offset, size)], or []."""
    out, pos = [], 0
    while pos + 8 <= len(blob):
        if blob[pos:pos + 4] != FDT_MAGIC:
            break
        total, = struct.unpack_from(">I", blob, pos + 4)
        if total < 0x30 or pos + total > len(blob):
            break
        out.append((pos, total))
        pos += total
    return out


def fdt_props(blob, base):
    """Root-level properties of the FDT at `base`, as name -> bytes.

    All offsets inside an FDT are relative to the tree's own start, and the
    struct block is 4-byte aligned *relative to it*. Since the trees in the
    tail are packed back to back, `base` is usually not 4-aligned, so aligning
    an absolute offset rounds to the wrong byte and every tree after the first
    mis-parses. Keep `p` relative to `base`.
    """
    off_struct, off_strings = struct.unpack_from(">II", blob, base + 8)
    p = off_struct
    props = {}
    depth = 0
    while True:
        tok, = struct.unpack_from(">I", blob, base + p)
        p += 4
        if tok == 1:                       # BEGIN_NODE
            depth += 1
            e = blob.index(b"\0", base + p)
            p = ((e - base) + 1 + 3) & ~3
        elif tok == 2:                     # END_NODE
            depth -= 1
        elif tok == 3:                     # PROP
            ln, nameoff = struct.unpack_from(">II", blob, base + p)
            p += 8
            val = blob[base + p:base + p + ln]
            p = (p + ln + 3) & ~3
            if depth == 1:                 # only the root node
                e = blob.index(b"\0", base + off_strings + nameoff)
                props[blob[base + off_strings + nameoff:e].decode()] = val
        elif tok == 4:                     # NOP
            continue
        elif tok == 9:                     # END
            break
        else:
            raise ValueError("bad FDT token %d at +0x%x" % (tok, p))
    return props


def describe(blob, entries):
    rows = []
    for i, (off, size) in enumerate(entries):
        try:
            pr = fdt_props(blob, off)
        except Exception:
            rows.append((i, off, size, None, "?"))
            continue
        bid = struct.unpack(">%dI" % (len(pr.get("qcom,board-id", b"")) // 4),
                            pr.get("qcom,board-id", b"")) if "qcom,board-id" in pr else ()
        model = pr.get("model", b"").split(b"\0")[0].decode("ascii", "replace")
        rows.append((i, off, size, bid, model))
    return rows


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    if not args:
        raise SystemExit(__doc__.strip().splitlines()[-3].strip())
    data = open(args[0], "rb").read()
    kernel = kernel_field(data)
    if kernel[:2] != b"\x1f\x8b":
        raise SystemExit("kernel is not gzip; for header v2 read the dtb field instead")

    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    image = d.decompress(kernel)
    rest = d.unused_data
    note = "gzip %d -> %d bytes of Image, %d bytes after it" % (
        len(kernel) - len(rest), len(image), len(rest))
    if rest[:4] != FDT_MAGIC:
        raise SystemExit("%s: no DTB right after the stream" % note)

    entries = split_tail(rest)
    rows = describe(rest, entries)
    tail_end = entries[-1][0] + entries[-1][1] if entries else 0
    covered = bool(entries) and tail_end == len(rest)
    print(note)
    if covered:
        print("%d device tree(s), chained exactly to the end" % len(entries))
    else:
        print("%d device tree(s), WARNING: %d bytes unaccounted for"
              % (len(entries), len(rest) - tail_end))
    dipper = [r for r in rows if r[3] and r[3][0] in DIPPER_BOARD_IDS]
    print("dipper board-ids present: %s"
          % (", ".join("0x%x" % r[3][0] for r in dipper) or "NONE - ABL will not boot this"))

    if "--list" in flags:
        for i, off, size, bid, model in rows:
            ids = "/".join("0x%x" % v for v in bid) if bid else "-"
            print("  #%02d off=%-9d size=%-7d board-id=%-12s %s" % (i, off, size, ids, model))
        return

    if len(args) < 2:
        raise SystemExit("no output file")

    if "--single" in flags:
        off, size = entries[0]
        blob = rest[off:off + size]
        print("WARNING: writing one tree only (board-id %s); ABL picks by board-id, "
              "so this boots on one hardware revision at most"
              % ("/".join("0x%x" % v for v in rows[0][3]) if rows[0][3] else "?"))
    else:
        blob = rest
    open(args[1], "wb").write(blob)
    print("wrote %s (%d bytes)" % (args[1], len(blob)))


if __name__ == "__main__":
    main()
