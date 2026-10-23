#!/bin/bash
# Build the shim and wrap it in an Android boot image for one board.
#
#   BOARD=sweet|dipper     board constants from board.h (default: sweet)
#   DTBS=path              dipper: the whole device tree collection from the
#                          stock boot.img (65 trees, ~30 MB). ABL picks one by
#                          qcom,board-id, so this is not optional.
#   DTB=path               sweet (v2 dtb field) / dipper fallback single tree
#   HEADER_VERSION=0|1|2   default: dipper 1, sweet 2
#   SHIM_UFS=0|1           UFS probe (default 1; 0 = minimal image)
#   SHIM_GIC=0|1           GIC/timer probe (default 1)
#
# Examples:
#   ./build.sh                                    # sweet, diagnostics
#   BOARD=dipper ./build.sh                       # Mi 8, diagnostics
#   BOARD=dipper ./build.sh ../out/payload.bin    # Mi 8, boot XNU
#
# dipper notes (measured against the stock boot.img in shim/out):
#   * ABL rejects `fastboot boot` for every image, stock included. The shim is
#     started with `fastboot flash boot out/shim-boot.img` and nothing else.
#   * The kernel field must be Image.gz-dtb: a gzip stream with the device tree
#     collection flush against its end. A bare Image, or a gzip with no trees
#     behind it, comes back as "Failed to load/authenticate boot image:
#     00000050" and the phone lands in fastboot.
#   * That collection has to be the *whole* 65-tree tail, not the first tree:
#     ABL matches qcom,board-id against the phone, and tree #0 is a Polaris P3
#     (0x2b), not a dipper (0x1e/0x1f/0x24/0x25/0x26/0x32..0x37).
#   * Header must be v1 with the stock addresses, or ABL refuses the image the
#     same way.
set -euo pipefail
cd "$(dirname "$0")"

CC=aarch64-linux-gnu-gcc
OBJCOPY=aarch64-linux-gnu-objcopy
BOARD="${BOARD:-sweet}"
OUT="${OUT:-out}"

case "$BOARD" in
dipper)
	DTBS="${DTBS:-../dtb/dipper/dtbs.bin}"
	DTB="${DTB:-../dtb/dipper/dtb}"
	HEADER_VERSION="${HEADER_VERSION:-1}"
	;;
sweet)
	DTB="${DTB:-../dtb/boot_unpacked/dtb}"
	HEADER_VERSION="${HEADER_VERSION:-2}"
	;;
*)
	echo "error: unknown BOARD=$BOARD (sweet|dipper)" >&2
	exit 1
	;;
esac

CFLAGS="-O2 -ffreestanding -nostdlib -fno-pic -mgeneral-regs-only -mstrict-align \
	-fno-stack-protector -Wall -Wextra -DBOARD_$BOARD"
[ "${SHIM_UFS:-1}" = 1 ] || CFLAGS="$CFLAGS -DSHIM_UFS=0"
[ "${SHIM_GIC:-1}" = 1 ] || CFLAGS="$CFLAGS -DSHIM_GIC=0"

echo "board: $BOARD  header: v$HEADER_VERSION  dtb: ${DTBS:-$DTB}"

for t in "$CC" "$OBJCOPY" aarch64-linux-gnu-readelf aarch64-linux-gnu-nm mkbootimg gcc python3; do
	command -v "$t" >/dev/null 2>&1 || {
		echo "error: missing '$t'" >&2
		echo "  apt install gcc-aarch64-linux-gnu" >&2
		echo "  mkbootimg: android-sdk-libsparse-utils / AOSP system/tools/mkbootimg" >&2
		exit 1
	}
done

# binutils < 2.39 does not know --no-warn-rwx-segments (Ubuntu 22.04 ships 2.38).
LD_EXTRA=(-Wl,--build-id=none -Wl,-N)
if aarch64-linux-gnu-ld --help 2>/dev/null | grep -q -- '--no-warn-rwx-segments'; then
	LD_EXTRA+=(-Wl,--no-warn-rwx-segments)
fi

mkdir -p "$OUT"
for f in start.S shim.c console.c mmu.c fdt.c loader.c ufs.c; do
	$CC $CFLAGS -c $f -o "$OUT/${f%.*}.o"
done

# The image runs wherever ABL puts it with no relocation pass: only the
# header's image_size (start.o, linked at 0) may be an absolute address.
abs=$(aarch64-linux-gnu-readelf -r "$OUT"/shim.o "$OUT"/console.o "$OUT"/mmu.o \
	"$OUT"/fdt.o "$OUT"/loader.o "$OUT"/ufs.o | grep -c ABS64 || true)
[ "$abs" = 0 ] || { echo "error: $abs absolute relocations in C objects" >&2; exit 1; }

$CC $CFLAGS -static -T shim.ld "${LD_EXTRA[@]}" \
	"$OUT"/start.o "$OUT"/shim.o "$OUT"/console.o "$OUT"/mmu.o \
	"$OUT"/fdt.o "$OUT"/loader.o "$OUT"/ufs.o -o "$OUT/shim.elf"
$OBJCOPY -O binary "$OUT/shim.elf" "$OUT/Image"

# ABL copies the file; pad to image_size so .bss/stacks exist as zeroed bytes.
# (strtonum() is a gawk extension and Ubuntu's default awk is mawk, so do the
# hex conversion in the shell instead.)
size=$(aarch64-linux-gnu-nm "$OUT/shim.elf" | awk '$3=="_end"{print $1}')
size=$((16#$size))
truncate -s "$size" "$OUT/Image"

# Ramdisk: an xnu payload (tools/mkpayload.py) if given as $1, else an empty
# newc cpio (ABL dislikes a zero-size ramdisk on some builds) -> diagnostics.
if [ -n "${1:-}" ]; then
	cp "$1" "$OUT/ramdisk.bin"
else
	(cd "$OUT" && rm -rf rd && mkdir rd && cd rd && find . | cpio -o -H newc --quiet | gzip -9 > ../ramdisk.bin)
fi

if [ "$BOARD" = "dipper" ]; then
	# Image.gz-dtb: the gzip stream must be followed *immediately* by the
	# device trees, with nothing in between. ABL decompresses the kernel and
	# then walks that tail looking for the entry whose qcom,board-id matches
	# this phone. dipper ships 65 of them (one per board-id / panel / hw
	# revision); appending a single tree boots on one revision at most, and
	# the first tree in the stock image is not even a dipper (0x2b, Polaris
	# P3) - which is why such an image is refused before any pixel is drawn.
	[ -f "$DTBS" ] || {
		echo "error: no device tree collection at $DTBS" >&2
		echo "  python3 tools/extract_dtb.py <stock boot.img> $DTBS" >&2
		echo "  (without it ABL cannot match this phone's board-id)" >&2
		exit 1
	}
	python3 ../tools/mkimage_gz_dtb.py "$OUT/Image" "$DTBS" "$OUT/kernel.gz-dtb"
	KERNEL_FILE="$OUT/kernel.gz-dtb"
	OSVER=(--os_version 10.0.0 --os_patch_level 2020-12)
else
	KERNEL_FILE="$OUT/Image"
	OSVER=()
fi

MKBOOT=(--header_version "$HEADER_VERSION" --kernel "$KERNEL_FILE"
	--ramdisk "$OUT/ramdisk.bin" --base 0 --kernel_offset 0x8000
	--ramdisk_offset 0x1000000 --tags_offset 0x100 --pagesize 4096
	-o "$OUT/shim-boot.img")
if [ "$HEADER_VERSION" -ge 2 ]; then
	MKBOOT+=(--dtb "$DTB" --dtb_offset 0x1f00000)
elif [ "$BOARD" = "dipper" ]; then
	echo "note: header v1 carries no dtb field; the trees travel appended to" >&2
	echo "      the kernel (Image.gz-dtb) and ABL picks one by board-id" >&2
fi
if [ "${#OSVER[@]}" -gt 0 ]; then
	MKBOOT+=("${OSVER[@]}")
elif [ "$HEADER_VERSION" -ge 1 ]; then
	MKBOOT+=(--os_version 17.0.0 --os_patch_level 2035-06)
fi
if [ "$BOARD" = "sweet" ]; then
	MKBOOT+=(--cmdline "console=null")
fi
mkbootimg "${MKBOOT[@]}"

# Never hand out an image ABL will reject: the phone can only be flashed, so a
# bad boot.img means a trip through fastboot and a manual rollback.
CHECK_ARGS=(--board "$BOARD" --ramdisk "$OUT/ramdisk.bin")
if [ "$BOARD" = "dipper" ]; then
	CHECK_ARGS+=(--dtbs "$DTBS")
else
	CHECK_ARGS+=(--dtb "$DTB")
fi
python3 ../tools/check_bootimg.py "$OUT/shim-boot.img" "${CHECK_ARGS[@]}"

# Host render of the console layout. Best effort: the image above is already
# built and validated, so a missing host gcc must not fail the build.
if gcc -O1 -Wall -DCON_HOST -DBOARD_$BOARD -o "$OUT/host_test" console.c host_test.c; then
	./"$OUT"/host_test "$OUT/host.ppm" || true
fi
ls -l "$OUT/Image" "$OUT/shim-boot.img"
