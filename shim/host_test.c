/* Render the console layout on the host: ./out/host_test && view out/host.ppm
 *
 * This is how a console change gets checked without a flash: the same
 * console.c, the same font, the same stride arithmetic, but the framebuffer
 * is a static array instead of 0x9d400000 and the GENI UART is stubbed out
 * (-DCON_HOST).
 */
#include <stdio.h>
#include "console.h"

static uint32_t host_fb[FB_H * FB_STRIDE / 4];

int main(void)
{
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

	FILE *f = fopen("out/host.ppm", "wb");
	if (!f) {
		perror("out/host.ppm");
		return 1;
	}
	fprintf(f, "P6 %d %d 255\n", FB_W, FB_H);
	/* Walk by stride: FB_STRIDE is ALIGN(FB_W, 32) * 4, so a panel row is
	 * wider than FB_W pixels and a flat index would shear the output. */
	for (int y = 0; y < FB_H; y++)
		for (int x = 0; x < FB_W; x++) {
			uint32_t p = host_fb[y * (FB_STRIDE / 4) + x];
			unsigned char px[3] = { p >> 16, p >> 8, p };
			fwrite(px, 1, 3, f);
		}
	fclose(f);
	return 0;
}
