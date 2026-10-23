#!/usr/bin/env python3
"""Synthesise a minimal arm64 XNU-shaped Mach-O to exercise mkpayload.py.

Not a kernel: just enough header, two LC_SEGMENT_64 and one LC_UNIXTHREAD for
the packer's parser, so the ADT -> payload path can be tested without a CI
kernel build.

  python3 mkfakekernel.py out.bin [--fileset]
"""
import struct
import sys

MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
MH_EXECUTE, MH_FILESET = 0x2, 0xC
LC_SEGMENT_64, LC_UNIXTHREAD = 0x19, 0x5
ARM_THREAD_STATE64, ARM_THREAD_STATE64_COUNT = 6, 68

TEXT_VA = 0xFFFFFFF007004000
TEXT_SIZE = 0x10000
DATA_VA = TEXT_VA + TEXT_SIZE
DATA_SIZE = 0x8000
ENTRY = TEXT_VA + 0x8000


def seg(name, vmaddr, vmsize, fileoff, filesize, maxprot=7, initprot=7):
    return struct.pack('<II16s4QIIII', LC_SEGMENT_64, 72, name.encode(), vmaddr,
                       vmsize, fileoff, filesize, maxprot, initprot, 0, 0)


def unixthread(pc):
    # arm_thread_state64: x0..x28, fp, lr, sp, pc, cpsr, flags (272 bytes)
    state = bytearray(272)
    struct.pack_into('<Q', state, 256, pc)   # pc
    return struct.pack('<IIII', LC_UNIXTHREAD, 16 + len(state),
                       ARM_THREAD_STATE64, ARM_THREAD_STATE64_COUNT) + bytes(state)


def main():
    out = sys.argv[1]
    cmds = (seg('__TEXT', TEXT_VA, TEXT_SIZE, 0, TEXT_SIZE) +
            seg('__DATA', DATA_VA, DATA_SIZE, TEXT_SIZE, DATA_SIZE) +
            unixthread(ENTRY))
    hdr = struct.pack('<8I', MH_MAGIC_64, CPU_TYPE_ARM64, 0, MH_EXECUTE,
                      3, len(cmds), 0, 0)
    body = bytearray(hdr + cmds)
    body += b'\0' * (TEXT_SIZE - len(body))
    body += bytes(DATA_SIZE)
    with open(out, 'wb') as f:
        f.write(body)
    print('%s: %d bytes, entry %#x, text %#x, data %#x'
          % (out, len(body), ENTRY, TEXT_VA, DATA_VA))


if __name__ == '__main__':
    main()
