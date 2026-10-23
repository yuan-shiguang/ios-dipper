import struct
import sys

FIELDS = [
    (0, 8, "magic"),
    (8, 4, "kernel_size"),
    (12, 4, "kernel_addr"),
    (16, 4, "ramdisk_size"),
    (20, 4, "ramdisk_addr"),
    (24, 4, "second_size"),
    (28, 4, "second_addr"),
    (32, 4, "tags_addr"),
    (36, 4, "page_size"),
    (40, 4, "header_version"),
    (44, 4, "os_version"),
    (48, 16, "name"),
    (64, 512, "cmdline"),
    (576, 4, "id"),
]


def dump(p):
    d = open(p, "rb").read(4096)
    out = {}
    for off, ln, nm in FIELDS:
        v = d[off:off + ln]
        if nm in ("magic", "name", "cmdline"):
            out[nm] = v.rstrip(b"\x00")
        else:
            out[nm] = struct.unpack("<I", v)[0]
    return out, d


paths = sys.argv[1:]
rows = []
for p in paths:
    f, hdr = dump(p)
    rows.append((p.split("/")[-1], f, hdr))

names = [r[0] for r in rows]
w = max(len(n) for n in names) + 2
print(" ".join(["field".ljust(16)] + [n.ljust(w) for n in names]))
for off, ln, nm in FIELDS:
    vals = []
    for _, f, _ in rows:
        v = f[nm]
        vals.append(("%d (0x%x)" % (v, v)) if isinstance(v, int) else repr(v))
    same = len(set(vals)) == 1
    mark = " " if same else "*"
    print(" ".join([(nm + mark).ljust(16)] + [v.ljust(w) for v in vals]))

# raw header byte diff against the first file
print()
base = rows[0][2]
for name, _, hdr in rows[1:]:
    d = [i for i in range(4096) if hdr[i] != base[i]]
    if d:
        print("%s vs %s: header differs at %d bytes: %s" %
              (name, rows[0][0], len(d), d[:24]))
    else:
        print("%s vs %s: header IDENTICAL" % (name, rows[0][0]))
