import gzip
import hashlib
import struct
import sys

FDT = b"\xd0\x0d\xfe\xed"


def split(p):
    d = open(p, "rb").read()
    pg = struct.unpack("<I", d[36:40])[0]
    ks = struct.unpack("<I", d[8:12])[0]
    rs = struct.unpack("<I", d[16:20])[0]
    hdr = d[:pg]
    # locate the DTB tail: the last long run that starts with FDT magic at a
    # 4-byte boundary and continues with a sane FDT header
    i = pg
    tail = None
    while i < len(d) - 4:
        if d[i:i + 4] == FDT:
            # candidate: verify totalsize field is plausible
            tot = struct.unpack(">I", d[i + 4:i + 8])[0]
            if 0x1000 <= tot <= 0x20000 and i + tot <= len(d):
                tail = i
                break
        i += 4
    gz = d[pg:tail]
    tailb = d[tail:]
    # ramdisk = the page(s) after the kernel field
    kfield = (ks + pg - 1) // pg * pg
    rd = d[pg + kfield: pg + kfield + ((rs + pg - 1) // pg * pg)]
    return dict(path=p, pg=pg, ks=ks, rs=rs, hdr=hdr, gz=gz, tail=tailb,
                rd=rd, total=len(d), tailoff=tail, imglen=len(gzip.decompress(gz)))


paths = sys.argv[1:]
parts = [split(p) for p in paths]

print("%-26s %10s %8s %8s %10s %8s" %
      ("image", "kernel_size", "gzip", "dtbtail", "imglen", "ramdisk"))
for p in parts:
    print("%-26s %10d %8d %8d %10d %8d" %
          (p["path"].split("/")[-1], p["ks"], len(p["gz"]), len(p["tail"]),
           p["imglen"], p["rs"]))

print()
for key in ("gz", "tail", "rd"):
    print("--- %s ---" % key)
    base = parts[0]
    for p in parts[1:]:
        a, b = base[key], p[key]
        if a == b:
            print("  %-26s IDENTICAL to %s" %
                  (p["path"].split("/")[-1], base["path"].split("/")[-1]))
        else:
            n = min(len(a), len(b))
            d = [i for i in range(n) if a[i] != b[i]]
            print("  %-26s differs: len %d vs %d, %d bytes differ (first %s)" %
                  (p["path"].split("/")[-1], len(a), len(b), len(d), d[:6]))
    print("  sha1 %s: %s" % (key, hashlib.sha1(base[key]).hexdigest()[:16]))
