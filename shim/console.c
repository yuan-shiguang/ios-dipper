/*
 * Text console: ABL splash framebuffer + optional GENI UART mirror.
 *
 * Why the mirror. dipper's panel is a DSI *command mode* AMOLED: it holds
 * whatever frame was last pushed into it. ABL leaves SDE scanning out of
 * cont_splash_region, so framebuffer writes do reach the panel - but only if
 * the base and the stride are right. When they are not, the screen simply
 * never changes and there is no way to tell "wrong address" from "shim
 * crashed" from "SDE stopped". The UART is the only channel that survives all
 * three, so everything written to the screen is also pushed out of 0xa84000.
 *
 * With the MMU off every access is Device memory, so stay aligned
 * (-mstrict-align) and use 32-bit loads/stores only.
 */
#include "console.h"
#include "font.h"

#define SCALE		3
#define CH_W		(8 * SCALE)
#define CH_H		(16 * SCALE)
#define MARGIN		24
#define LABEL_COLS	8

volatile uint32_t *con_fb = (volatile uint32_t *)FB_BASE;
static uint32_t con_bg;
static int row;

/* ---- GENI UART (qupv3_se9_2uart) --------------------------------------
 *
 * Register offsets from include/linux/qcom-geni-se.h in the vendor tree.
 * ABL already programmed the baud rate, word length and the GENI protocol
 * engine, so all that is left is feeding the TX FIFO.
 */
#define GENI_STATUS		0x40
#define SE_GENI_TX_FIFOn	0x700
#define SE_GENI_TX_FIFO_STATUS	0x800
#define SE_HW_PARAM_0		0xe24

#define TX_FIFO_WC_MSK		0x0fffffffU	/* bits 27:0, words in FIFO */
#define TX_FIFO_DEPTH_MSK	0x003f0000U	/* bits 21:16 of SE_HW_PARAM_0 */

static int uart_state;	/* stays 0 until con_uart_init() proves it usable */

/*
 * Everything printed before the UART probe is remembered here and replayed
 * once the port is proven live. The probe itself must not run early: reading
 * SE_HW_PARAM_0 on a clock-gated GENI can hang the NoC, and if that happens
 * before anything is drawn you get a black screen with no evidence at all.
 */
#define HIST_LEN 2048
static char hist[HIST_LEN];
static unsigned hist_len;

#if defined(CON_HOST)
/* host_test links this file into an x86 binary to render the layout; there is
 * no GENI block at 0xa84000 there, so reads must not touch memory. */
static uint32_t ureg(uint32_t off)
{
	(void)off;
	return 0;
}
#else
static uint32_t ureg(uint32_t off)
{
	return *(volatile uint32_t *)(UART_BASE + off);
}
#endif

static void uart_raw(char c)
{
	uint32_t depth = (ureg(SE_HW_PARAM_0) & TX_FIFO_DEPTH_MSK) >> 16;
	for (int i = 0; i < 200000; i++)
		if ((ureg(SE_GENI_TX_FIFO_STATUS) & TX_FIFO_WC_MSK) < depth)
			break;
	*(volatile uint32_t *)(UART_BASE + SE_GENI_TX_FIFOn) = (uint8_t)c;
}

static void uart_putc(char c)
{
	if (!uart_state) {
		if (hist_len < HIST_LEN)
			hist[hist_len++] = c;
		return;
	}
	uart_raw(c);
}

void con_uart_init(void)
{
	if (!UART_BASE) {
		uart_state = 0;
		return;
	}

	uint32_t depth = (ureg(SE_HW_PARAM_0) & TX_FIFO_DEPTH_MSK) >> 16;

	/* SE_HW_PARAM_0 is a hard-wired parameter register. A GENI whose clocks
	 * ABL gated reads back 0 or all-ones; a live one reports a real FIFO
	 * depth (16/32/64). Poking the FIFO of a gated block hangs the NoC, so
	 * refuse to write unless the depth looks sane. */
	uart_state = (depth >= 4 && depth <= 64) ? 1 : 0;
	if (!uart_state)
		return;

	/* Replay what the screen already has, so the serial log is complete. */
	uart_raw('\r');
	uart_raw('\n');
	for (unsigned i = 0; i < hist_len; i++)
		uart_raw(hist[i]);
	uart_raw('\r');
	uart_raw('\n');
	hist_len = 0;
}

int con_uart_ok(void)
{
	return uart_state;
}

static void uart_puts(const char *s)
{
	while (*s)
		uart_putc(*s++);
}

/* ---- framebuffer ------------------------------------------------------ */

void con_fill(uint32_t c)
{
	/* One linear run over the whole carve-out: no stride multiplication, so
	 * it cannot shear and it covers every pitch a panel could be using. */
	uint32_t n = (uint32_t)(BOARD_FB_BYTES / 4);
	for (uint32_t i = 0; i < n; i++)
		con_fb[i] = c;
}

void con_clear(uint32_t bg)
{
	for (int y = 0; y < FB_H; y++) {
		volatile uint32_t *line = con_fb + (uint32_t)y * (FB_STRIDE / 4);
		for (int x = 0; x < FB_W; x++)
			line[x] = bg;
	}
	con_bg = bg;
	row = 0;
	uart_puts("\r\n");
}

static void put_char(int col, char ch, uint32_t fg)
{
	if (ch < 0x20 || ch > 0x7e)
		ch = '?';
	const unsigned char *g = font8x16[ch - 0x20];
	int x0 = MARGIN + col * CH_W, y0 = MARGIN + row * CH_H;
	if (x0 + CH_W > FB_W || y0 + CH_H > FB_H)
		return;
	for (int gy = 0; gy < 16; gy++)
		for (int gx = 0; gx < 8; gx++) {
			uint32_t c = (g[gy] & (0x80 >> gx)) ? fg : con_bg;
			for (int sy = 0; sy < SCALE; sy++) {
				volatile uint32_t *p = con_fb + (uint32_t)(y0 + gy * SCALE + sy) * (FB_STRIDE / 4)
						       + x0 + gx * SCALE;
				for (int sx = 0; sx < SCALE; sx++)
					p[sx] = c;
			}
		}
}

static int put_str(int col, const char *s, uint32_t fg)
{
	while (*s) {
		uart_putc(*s);
		put_char(col++, *s++, fg);
	}
	return col;
}

static void fmt_hex(char *buf, uint64_t v, int digits)
{
	static const char hex[] = "0123456789ABCDEF";
	for (int i = 0; i < digits; i++)
		buf[i] = hex[(v >> (4 * (digits - 1 - i))) & 0xf];
	buf[digits] = 0;
}

void con_skip(void)
{
	row++;
	uart_puts("\r\n");
}

void con_str(const char *label, const char *s)
{
	put_str(0, label, YELLOW);
	put_str(LABEL_COLS, s, WHITE);
	row++;
	uart_puts("\r\n");
}

void con_hex(const char *label, uint64_t v)
{
	char buf[17];
	fmt_hex(buf, v, 16);
	con_str(label, buf);
}

void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb)
{
	char buf[9];
	int col = put_str(0, label, YELLOW);
	col = col < LABEL_COLS ? LABEL_COLS : col + 1;
	col = put_str(col, a, YELLOW);
	fmt_hex(buf, va, 8);
	col = put_str(col, buf, WHITE);
	if (b) {
		col = put_str(col + 1, b, YELLOW);
		fmt_hex(buf, vb, 8);
		put_str(col, buf, WHITE);
	}
	row++;
	uart_puts("\r\n");
}
