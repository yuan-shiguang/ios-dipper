/*
 * Boot shim entry: an arm64 Linux Image header so Qualcomm ABL treats us like
 * a kernel. ABL enters at EL1, MMU/D-cache off, x0 = DTB pointer.
 *
 * The first thing this does - before the stack, before .bss is zeroed, before
 * any C - is paint three marks straight into the ABL splash framebuffer.
 * Nothing is read from it: a read of a protected carve-out faults before
 * anything is drawn, which is how an earlier dipper build managed to show a
 * black screen instead of a diagnostic.
 *
 *   green  bar across the top  -> ABL jumped here and the base address is
 *                                 right (pitch-independent: one linear run)
 *   red    column down the left edge -> the row pitch is 1088 px, i.e.
 *                                 ALIGN(1080,32), the vendor kernel's fb0
 *                                 stride; if it comes out slanted the pitch is
 *                                 something else and the slant direction says
 *                                 which way
 *   blue   square at the bottom right -> the buffer is big enough and row 0 is
 *                                 the top of the panel (no vertical flip)
 *
 * Any one of them missing narrows the failure down without a serial port.
 */
	.section .text.head, "ax"
	.globl _head
_head:
	b	_start			/* code0 */
	.long	0			/* code1 */
	.quad	0x80000			/* text_offset: the stock dipper kernel uses
					   0x80000 and ABL validates it; 0 is not a
					   legal arm64 Linux value and the image is
					   rejected with "Failed to load/authenticate
					   boot image: 00000050" */
	.quad	_end			/* image_size (linked at 0) */
	.quad	0xa			/* flags: LE, 4K pages, place anywhere */
	.quad	0			/* res2 */
	.quad	0			/* res3 */
	.quad	0			/* res4 */
	.ascii	"ARM\x64"		/* magic */
	.long	0			/* res5 (PE offset) */

/* Keep in sync with board.h (BOARD_FB_BASE / _STRIDE / _H). */
#if defined(BOARD_dipper)
# define FB_BASE_HI	0x9d40		/* 0x9d400000: cont_splash_region */
# define FB_BASE_LO	0x0000
# define FB_PITCH	1080		/* 1080 px * 4 B, no 32-pixel align */
# define FB_ROWS	2248
#else
# error "start.S knows no framebuffer for this board"
#endif

	.text
	.globl _start
_start:
	msr	daifset, #0xf

	movz	x1, #FB_BASE_HI, lsl #16
	movk	x1, #FB_BASE_LO
	movz	w2, #0xff00
	movk	w2, #0xff00, lsl #16	/* 0xff00ff00 = green in a8r8g8b8 */

	/* 1. Full-width bar, 48 rows, written as one linear run so it does not
	 *    depend on the pitch at all. 1080 * 48 words. */
	mov	x3, #(1080 * 48)
1:	str	w2, [x1], #4
	subs	x3, x3, #1
	b.ne	1b

	/* 2. Red column, 32 px wide, down the left edge, stepping by the
	 *    candidate pitch. Slanted on screen = pitch is not 1088. */
	movz	x4, #0xffff, lsl #16		/* 0xffff0000 = red */
	movz	x5, #FB_ROWS
	mov	x6, #(FB_PITCH * 4)
	movz	x1, #FB_BASE_HI, lsl #16
	movk	x1, #FB_BASE_LO
2:	mov	x8, #32
3:	str	w4, [x1], #4
	subs	x8, x8, #1
	b.ne	3b
	sub	x1, x1, #(32 * 4)		/* back to the column's x */
	add	x1, x1, x6			/* next row */
	subs	x5, x5, #1
	b.ne	2b

	/* 3. Blue square at the bottom-right corner, addressed with the same
	 *    pitch: it only lands there if pitch and orientation are both right. */
	movz	x4, #0x00a0
	movk	x4, #0xff00, lsl #16		/* 0xff0000a0 = blue */
	movz	x1, #FB_BASE_HI, lsl #16
	movk	x1, #FB_BASE_LO
	mov	x9, #(FB_PITCH * 4)
	mov	x10, #(FB_ROWS - 1)
	madd	x1, x10, x9, x1			/* base + (rows-1) * pitch * 4 */
	mov	x6, #((1080 - 32) * 4)		/* x = 1080-32 (add imm is 12-bit) */
	add	x1, x1, x6
	mov	x5, #32
4:	mov	x8, #32
5:	str	w4, [x1], #4
	subs	x8, x8, #1
	b.ne	5b
	sub	x1, x1, #(32 * 4)
	sub	x1, x1, x9
	subs	x5, x5, #1
	b.ne	4b

	/* Now a stack, vectors and .bss, then C. */
	mov	x19, x0			/* DTB pointer from ABL */
	msr	spsel, #1
	adr	x1, _stack_top
	mov	sp, x1
	adr	x1, vectors
	msr	vbar_el1, x1
	isb
	/* zero .bss */
	adr	x1, _bss_start
	adr	x2, _bss_end
6:	cmp	x1, x2
	b.hs	7f
	str	xzr, [x1], #8
	b	6b
7:	mov	x0, x19
	bl	shim_main
8:	wfe
	b	8b

/*
 * Second entry, at image offset 0x80000 = the header's text_offset.
 *
 * Nothing says whether ABL jumps to the load address or to load + text_offset;
 * the stock kernel boots either way (0x80000 into a 42 MB kernel is still
 * kernel text), so its behaviour cannot be inferred from a working stock boot.
 * Our image is 180 KB, so the wrong choice would jump into zeros and the phone
 * would drop straight back to fastboot - the exact symptom we are chasing.
 * A branch here makes both interpretations land in the same place, and 330 KB
 * of padding costs ~400 bytes once gzipped.
 */
	.section .altentry, "ax"
	.globl _alt_entry
_alt_entry:
	b	_head

	.text

/* PSCI CPU_ON target. x0 = context id = cpu index (1..7). */
	.globl secondary_entry
secondary_entry:
	msr	daifset, #0xf
	msr	spsel, #1
	adr	x1, _sec_stacks
	add	x1, x1, x0, lsl #12
	add	x1, x1, #0x1000
	mov	sp, x1
	adr	x1, vectors
	msr	vbar_el1, x1
	isb
	bl	secondary_main
9:	wfe
	b	9b

/*
 * Exception vectors. Each slot stashes x29/x30, loads its slot number
 * and jumps to the common path, which saves x0-x28 and calls
 * exc_handler(slot, regs). IRQs return; everything else ends in the
 * on-screen panic.
 */
.macro vent slot
	.balign	0x80
	stp	x29, x30, [sp, #-16]!
	mov	x29, #\slot
	b	exc_common
.endm

	.balign	0x800
	.globl vectors
vectors:
	vent 0
	vent 1
	vent 2
	vent 3
	vent 4
	vent 5
	vent 6
	vent 7
	vent 8
	vent 9
	vent 10
	vent 11
	vent 12
	vent 13
	vent 14
	vent 15

exc_common:
	sub	sp, sp, #(30 * 8)
	stp	x0, x1, [sp, #0]
	stp	x2, x3, [sp, #16]
	stp	x4, x5, [sp, #32]
	stp	x6, x7, [sp, #48]
	stp	x8, x9, [sp, #64]
	stp	x10, x11, [sp, #80]
	stp	x12, x13, [sp, #96]
	stp	x14, x15, [sp, #112]
	stp	x16, x17, [sp, #128]
	stp	x18, x19, [sp, #144]
	stp	x20, x21, [sp, #160]
	stp	x22, x23, [sp, #176]
	stp	x24, x25, [sp, #192]
	stp	x26, x27, [sp, #208]
	str	x28, [sp, #224]
	mov	x0, x29
	mov	x1, sp
	bl	exc_handler
	ldp	x0, x1, [sp, #0]
	ldp	x2, x3, [sp, #16]
	ldp	x4, x5, [sp, #32]
	ldp	x6, x7, [sp, #48]
	ldp	x8, x9, [sp, #64]
	ldp	x10, x11, [sp, #80]
	ldp	x12, x13, [sp, #96]
	ldp	x14, x15, [sp, #112]
	ldp	x16, x17, [sp, #128]
	ldp	x18, x19, [sp, #144]
	ldp	x20, x21, [sp, #160]
	ldp	x22, x23, [sp, #176]
	ldp	x24, x25, [sp, #192]
	ldp	x26, x27, [sp, #208]
	ldr	x28, [sp, #224]
	add	sp, sp, #(30 * 8)
	ldp	x29, x30, [sp], #16
	eret
