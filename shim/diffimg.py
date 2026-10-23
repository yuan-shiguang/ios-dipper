import gzip, struct, re, sys

FDT = b"\xd0\x0d\xfe\xed"


def load(p):
    d = open(p, "rb").read()
    ks = struct.unpack("<I", d[8:12])[0]
    i = 4096
    while i < len(d) - 4:
        if d[i:i + 4] == FDT:
            break
        i += 1
    return gzip.decompress(d[4096:i]), ks, i


ra, ka, ta = load(sys.argv[1])
rb, kb, tb = load(sys.argv[2])
print("A %-28s ks=%d tail@%d imgsz=%d" % (sys.argv[1], ka, ta, len(ra)))
print("B %-28s ks=%d tail@%d imgsz=%d" % (sys.argv[2], kb, tb, len(rb)))
print("same image size:", len(ra) == len(rb), len(ra))
d = [i for i in range(min(len(ra), len(rb))) if ra[i] != rb[i]]
print("differing bytes:", len(d), d[:20])
if d:
    lo, hi = max(0, d[0] - 24), min(len(ra), d[-1] + 25)
    print("A ctx:", re.findall(rb"[ -~]{4,}", ra[lo:hi]))
    print("B ctx:", re.findall(rb"[ -~]{4,}", rb[lo:hi]))
