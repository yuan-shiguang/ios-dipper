/* Render the console layout on the host: ./out/host_test out/host.ppm
 *
 * This is how a console change gets checked without a flash: the same
 * console.c, the same font, the same stride arithmetic, but the framebuffer
 * is a static array instead of 0x9d400000 and the GENI UART is stubbed out
 * (-DCON_HOST).
 */
#include <stdio.h>
#include "console.h"

static uint32_t host_fb[FB_H * FB_STRIDE / 4];

int main(int argc, char **argv)
{
	const char *path = argc > 1 ? argv[1] : "out/host.ppm";

	con_fb = host_fb;
	con_clear(RED);
	con_str("SHIM", BOARD_NAME " " BOARD_SOC " console render");
	con_hex2("FB", "base=", (uint32_t)FB_BASE, "stride=", FB_STRIDE);
	con_hex("MMFR0", 0x0000000000101122ULL);
	con_hex2("PSCI", "ver=", 0x10001, "smccc=", 0x10002);
	con_hex2("CPU7", "ret=", 0, "mpidr=", 0x81000700);
	con_clear(BLUE);
	con_str("PANIC", "sync exception");
	con_hex("ESR", 0x96000010);

	/*
	 * Overflow on purpose: print 12 lines more than the screen holds, with
	 * a milestone bar underneath. A correct console keeps the last MAX_ROW
	 * lines, keeps the bar exactly where it was, and never paints a glyph
	 * into it.
	 */
	con_clear(BLACK);
	con_mark(0xff008080U);		/* teal: the UFS milestone colour */
	for (int i = 0; i < MAX_ROW + 12; i++) {
		char label[8], val[48];
		snprintf(label, sizeof label, "L%02d", i);
		snprintf(val, sizeof val, "line %d - scrolled, still readable", i);
		con_str(label, val);
	}
	con_mark(0xff808080U);		/* grey: last milestone wins */

	FILE *f = fopen(path, "wb");
	if (!f) {
		perror(path);
		return 1;
	}
	fprintf(f, "P6 %d %d 255\n", FB_W, FB_H);
	/* Walk by stride: a panel row may be padded past FB_W pixels, and a
	 * flat index would shear the output. */
	for (int y = 0; y < FB_H; y++)
		for (int x = 0; x < FB_W; x++) {
			uint32_t p = host_fb[y * (FB_STRIDE / 4) + x];
			unsigned char px[3] = { p >> 16, p >> 8, p };
			fwrite(px, 1, 3, f);
		}
	fclose(f);
	return 0;
}
