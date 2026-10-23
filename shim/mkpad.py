#!/usr/bin/env python3
"""Build a size-only variant of a known-good dipper boot image.

Every image that has ever run on the phone has had a kernel field of 7413
pages; every image ABL refused has had 7414. That is a correlation, not a
proof - the two sets also differ in code. This tool removes the confound: it
takes a boot image known to boot and grows only its gzip stream, by
appending incompressible bytes to the Image before recompressing. The code
is untouched, the DTB tail stays flush against the stream (so
check_bootimg.py still passes), and the image crosses exactly one more
4096-byte page boundary.

    python3 mkpad.py <src.img> <dst.img> [extra_bytes]

Needs mkbootimg on PATH. The ramdisk and the DTB tail are copied out of the
source image, so nothing else can drift between the two builds.
"""
import os
import struct
import subprocess
import sys
import tempfile
import zlib

PAGE = 4096


def unpack(src):
    """Return (image_bytes, ramdisk_bytes, dtb_tail, header) from a boot image."""
    d = open(src, "rb").read()
    if d[:8] != b"ANDROID!":
        sys.exit("error: %s is not an Android boot image" % src)
    ksize, kaddr, rsize, raddr = struct.unpack_from("<4I", d, 8)
    psize, hver = struct.unpack_from("<2I", d, 36)
    if psize != PAGE:
        sys.exit("error: unexpected page size %d" % psize)
    kpad = (ksize + PAGE - 1) // PAGE * PAGE
    rpad = (rsize + PAGE - 1) // PAGE * PAGE
    if len(d) != PAGE + kpad + rpad:
        sys.exit("error: %s is %d bytes, expected %d"
                 % (src, len(d), PAGE + kpad + rpad))
    field = d[PAGE:PAGE + ksize]
    dobj = zlib.decompressobj(16 + zlib.MAX_WBITS)
    img = dobj.decompress(field)
    tail = dobj.unused_data          # the 65-tree DTB collection
    rd = d[PAGE + kpad:PAGE + kpad + rsize]
    return img, rd, tail, {"kernel_size": ksize, "ramdisk_size": rsize,
                           "page_size": psize, "header_version": hver}


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    src, dst = sys.argv[1], sys.argv[2]
    extra = int(sys.argv[3]) if len(sys.argv) > 3 else PAGE

    img, rd, tail, hdr = unpack(src)
    print("source : kernel_size=%d (%.2f pages) ramdisk_size=%d dtb_tail=%d"
          % (hdr["kernel_size"], hdr["kernel_size"] / PAGE,
             hdr["ramdisk_size"], len(tail)))

    work = tempfile.mkdtemp(prefix="mkpad-")
    try:
        open(os.path.join(work, "Image"), "wb").write(img + os.urandom(extra))
        open(os.path.join(work, "ramdisk.bin"), "wb").write(rd)
        with open(os.path.join(work, "Image"), "rb") as f:
            gz = subprocess.run(["gzip", "-9", "-c"], stdin=f,
                                capture_output=True, check=True).stdout
        with open(os.path.join(work, "kernel.gz-dtb"), "wb") as f:
            f.write(gz)
            f.write(tail)
        subprocess.run(["mkbootimg",
                        "--header_version", str(hdr["header_version"]),
                        "--kernel", os.path.join(work, "kernel.gz-dtb"),
                        "--ramdisk", os.path.join(work, "ramdisk.bin"),
                        "--base", "0",
                        "--kernel_offset", "0x8000",
                        "--ramdisk_offset", "0x1000000",
                        "--tags_offset", "0x100",
                        "--pagesize", str(PAGE),
                        "--os_version", "10.0.0",
                        "--os_patch_level", "2020-12",
                        "-o", dst], check=True)
    finally:
        for n in os.listdir(work):
            os.unlink(os.path.join(work, n))
        os.rmdir(work)

    nimg, nrd, ntail, nhdr = unpack(dst)
    print("result : kernel_size=%d (%.2f pages) ramdisk_size=%d dtb_tail=%d"
          % (nhdr["kernel_size"], nhdr["kernel_size"] / PAGE,
             nhdr["ramdisk_size"], len(ntail)))
    print("image  : %d -> %d bytes (+%d random)" % (len(img), len(nimg), extra))
    print("wrote  : %s (%d bytes)" % (dst, os.path.getsize(dst)))
    if ntail != tail:
        print("WARNING: DTB tail changed, this experiment is not clean")


if __name__ == "__main__":
    main()
