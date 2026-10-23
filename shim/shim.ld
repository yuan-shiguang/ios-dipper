ENTRY(_head)
SECTIONS
{
	. = 0;
	.text : { *(.text.head) *(.text*) }
	.rodata : { *(.rodata*) }
	.data : { *(.data*) }
	. = ALIGN(16);
	_bss_start = .;
	.bss : { *(.bss*) *(COMMON) }
	. = ALIGN(16);
	_bss_end = .;
	. += 0x8000;
	. = ALIGN(16);
	_stack_top = .;
	. = ALIGN(4096);
	_sec_stacks = .;
	. += 8 * 0x1000;

	/* Alias entry at the header's text_offset (0x80000). ABL may jump to the
	 * load address or to load + text_offset, and the stock kernel cannot tell
	 * us which: 0x80000 into a 42 MB kernel is still kernel text, so both work
	 * there. Our image is 180 KB, so the wrong choice jumps into zeros and the
	 * phone returns to fastboot. A branch at 0x80000 back to _head makes the
	 * shim work either way; the padding gzips down to a few hundred bytes. */
	. = ALIGN(16);
	. = 0x80000;
	.altentry : { *(.altentry) }

	. = ALIGN(4096);
	_end = .;
	/DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
