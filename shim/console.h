#pragma once
#include <stdint.h>
#include "board.h"

/* The ABL splash framebuffer, per board (see board.h). */
#define FB_BASE		BOARD_FB_BASE
#define FB_W		BOARD_FB_W
#define FB_H		BOARD_FB_H
#define FB_STRIDE	BOARD_FB_STRIDE	/* a8r8g8b8, ALIGN(w,32)*4 */

/* Debug UART mirror, 0 when the board has none wired up. */
#define UART_BASE	BOARD_UART_BASE

/*
 * Vertical offset of the first text row, in pixels. Boards with a display
 * notch push the console below it (see BOARD_CON_TOP in board.h); everything
 * else uses the same value as the left margin.
 */
#define CON_TOP		BOARD_CON_TOP

/* Glyph geometry, shared with host_test so the layout can be checked
 * without a flash. */
#define SCALE		3
#define CH_W		(8 * SCALE)
#define CH_H		(16 * SCALE)
#define MARGIN		24
#define LABEL_COLS	8

/* Milestone strip: full width, pinned below the last text row. */
#define MARK_H		64
#define MAX_ROW		((FB_H - MARK_H - CON_TOP) / CH_H)

#define RED	0xffff0000U
#define BLUE	0xff0000a0U
#define WHITE	0xffffffffU
#define YELLOW	0xffffff00U
#define GREEN	0xff00ff00U
#define CYAN	0xff00ffffU
#define MAGENTA	0xffff00ffU
#define BLACK	0xff000000U
#define ORANGE	0xffff8000U

extern volatile uint32_t *con_fb;

/*
 * Paint the whole splash carve-out with one colour, in a single linear run
 * that never multiplies by the stride. That makes it independent of the row
 * pitch: whatever the panel is scanning, it turns this colour if the base
 * address is right. Use it as the very first thing after the asm beacon,
 * before trusting any stride-dependent drawing.
 */
void con_fill(uint32_t c);

/*
 * Probe the UART before the first mirrored line. Everything printed before
 * this call is kept in a small history buffer and replayed when the probe
 * succeeds, so the early lines (which must not touch the GENI, see below) are
 * still readable over the serial port.
 */
void con_uart_init(void);
int con_uart_ok(void);
void con_uart_drain(void);

void con_clear(uint32_t bg);

/*
 * Set the text background and cursor without touching the framebuffer.
 * Drawing text straight over whatever the screen already shows is the cheapest
 * possible proof that C is running: a full clear is millions of uncached
 * stores, and at the clock ABL leaves the boot core that can outlast the
 * window before the phone gives up and resets.
 */
void con_begin(uint32_t bg);

void con_skip(void);
void con_str(const char *label, const char *s);
void con_hex(const char *label, uint64_t v);
/* label, then "a=xxxxxxxx b=xxxxxxxx" style pairs of 32-bit values */
void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb);

/*
 * Milestone colour bar: a full-width strip pinned to the bottom of the
 * screen, below the last text row. Paint it *before* doing the thing that
 * might hang, so the colour left on screen names the last step that ran.
 * The console owns the strip - it survives scrolling and con_clear().
 */
void con_mark(uint32_t c);
