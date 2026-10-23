/*
 * Board constants. Everything the shim needs to know about the SoC it runs on
 * lives here, so a port is "add a block" instead of "grep for 0x17a00000 and
 * hope you found them all".
 *
 * Select with -DBOARD_dipper or -DBOARD_sweet; build.sh passes it.
 *
 * Sources are the vendor kernel trees (arch/arm64/boot/dts/qcom) and, where
 * marked "measured", what a flashed image actually did on the phone.
 */
#pragma once
#include <stdint.h>

/* Qualcomm imem restart reasons, decoded by ABL on the next boot. */
#define RESTART_REASON_NORMAL		0x77665501U
#define RESTART_REASON_BOOTLOADER	0x77665500U
#define RESTART_REASON_RECOVERY		0x77665502U

#if defined(BOARD_dipper)

/* --- Xiaomi Mi 8, SDM845 (Kryo 385: 4x A75 + 4x A55) --- */
#define BOARD_NAME		"dipper"
#define BOARD_SOC		"sdm845"

/*
 * Display: the ABL splash framebuffer.
 *
 * BASE. sdm845.dtsi reserved-memory:
 *     cont_splash_memory: cont_splash_region@9d400000 { size 0x2400000 }
 * 0x9c000000 is the SDM670 / SM7150 address (what sweet uses). On dipper that
 * range sits between pil_spss_region (ends 0x98000000) and cont_splash_region:
 * plain free DRAM nobody scans out. Writes there never fault, they just never
 * reach the panel - which is exactly the "black screen, then fastboot"
 * symptom. Measured: a 200x200 green square at 0x9d400000 showed up on the
 * panel, so this base is right.
 *
 * STRIDE. The vendor kernel's mdss_mdp_fb_stride() (drivers/video/fbdev/msm/
 * mdss_mdp.c) returns ALIGN(xres, 32) * bpp for fb0 because Adreno wants a
 * 32-pixel pitch: ALIGN(1080, 32) * 4 = 1088 * 4 = 4352. Using 1080 * 4
 * shears the image 8 pixels sideways per row, which at the bottom of a 2248
 * row panel is 17 984 pixels - it reads as a diagonal smear, not as a small
 * offset.
 *
 * PANEL. Samsung EA8074 AMOLED, 1080x2248, DSI *command* mode
 * (dsi-panel-ss-notch-fhd-ea8074-cmd.dtsi). A command-mode panel holds the
 * last frame it was given, so if SDE ever stops scanning nothing we write can
 * appear. That is why the shim paints something before it touches anything
 * else, and why the GENI UART mirror exists.
 */
#define BOARD_FB_BASE		0x9d400000UL
#define BOARD_FB_W		1080
#define BOARD_FB_H		2248
/*
 * Row pitch, 4320 B. Measured, not read from a driver, and confirmed on the
 * phone: start.S draws a red column stepping by this pitch before any C runs,
 * and on hardware it comes out dead straight - so the panel really scans 1080
 * pixels per row with no 32-pixel alignment. The earlier 1088 (ALIGN(1080,32))
 * left 32 bytes of the previous frame between rows, which is what turned text
 * into a diagonal smear.
 */
#define BOARD_FB_STRIDE		(1080 * 4)
#define BOARD_FB_BYTES		0x02400000UL	/* the whole carve-out */

/*
 * Debug UART: qupv3_se9_2uart (GENI SE) at 0xa84000, the console that
 * dipper-p0.dtsi leaves enabled. ABL initialises it, so console.c only has to
 * push bytes into SE_GENI_TX_FIFOn. The pads are on the mainboard under the
 * back cover, 115200 8N1 - keep it compiled in anyway: it is the only channel
 * that survives a dead panel.
 */
#define BOARD_UART_BASE		0xa84000UL

/* GICv3: sdm845.dtsi interrupt-controller@17a00000, redistributors at
 * 0x17a60000 with a 0x20000 per-PE stride (8 PEs -> 0x100000). */
#define BOARD_GICD_BASE		0x17a00000UL
#define BOARD_GICR_BASE		0x17a60000UL
#define BOARD_GICR_SIZE		0x100000UL
#define BOARD_GICR_PE_SIZE	0x20000UL

/* sdm845.dtsi timer {}: <GIC_PPI 1>, <2>, <3>, <0>. DT PPI numbers are
 * relative to 16, so these are INTIDs 17/18/19/16: the non-secure physical
 * timer is 18 and the virtual timer 19 - the usual Qualcomm remap, not 26/27. */
#define BOARD_VTIMER_INTID	19
#define BOARD_PTIMER_INTID	18

#define BOARD_NR_CPUS		8
#define BOARD_TIMEBASE_HZ	19200000UL

/* SDM845: 4x Cortex-A55 in cluster 0 (MPIDR 0x000..0x003) and 4x Cortex-A75 in
 * cluster 1 (MPIDR 0x100..0x103). PSCI CPU_ON takes the full MPIDR, so core i
 * is at ((i >> 2) << 8) | (i & 3) - NOT sweet's i << 8. */
#define BOARD_CPU_ON_MPIDR(i)	((((((uint64_t)(i)) >> 2) & 0xff) << 8) | \
				 (((uint64_t)(i)) & 0x3))

/* sdm845.dtsi: qcom,msm-imem@146bf000, restart_reason@65c -> 0x146bf65c.
 * (0x146aa65c is the SM7150 imem window sweet uses; on dipper it lands outside
 * the node and the write is silently dropped.)
 * PSHOLD: restart@10ac000, first reg = 0xc264000. */
#define BOARD_IMEM_RESTART_REASON 0x146bf65cUL
#define BOARD_PSHOLD		0x0c264000UL

/* UFS host controller: sdm845.dtsi ufshc@1d84000. */
#define BOARD_UFS_BASE		0x01d84000UL

/* dipper's stock boot.img (MIUI 12 / Android 10) is header v1 with the kernel
 * stored as Image.gz-dtb. v0 and v2 are both rejected. */
#define BOARD_BOOT_HEADER_VERSION 1

/* `fastboot boot` is disabled on dipper (the stock image fails with
 * "Failed to load/authenticate boot image: 00000050" too), so the shim can
 * only ever be started by `fastboot flash boot`. That makes every bad build a
 * trip through fastboot: the shim must NOT reboot on its own, or the phone
 * bounces straight back into the bootloader and the screen never gets read. */
#define BOARD_HOLD_ON_EXIT	1

#elif defined(BOARD_sweet)

/* --- Xiaomi Redmi Note 10 Pro, SM7150 (Kryo 470) --- */
#define BOARD_NAME		"sweet"
#define BOARD_SOC		"sm7150"

#define BOARD_FB_BASE		0x9c000000UL	/* cont_splash_region */
#define BOARD_FB_W		1080
#define BOARD_FB_H		2400
#define BOARD_FB_STRIDE		(1088 * 4)	/* ALIGN(1080,32) * 4 */
#define BOARD_FB_BYTES		(BOARD_FB_H * BOARD_FB_STRIDE)

/* sweet's debug UART was never wired up here; 0 disables the mirror. */
#define BOARD_UART_BASE		0UL

#define BOARD_GICD_BASE		0x17a00000UL
#define BOARD_GICR_BASE		0x17a60000UL
#define BOARD_GICR_SIZE		0x100000UL
#define BOARD_GICR_PE_SIZE	0x20000UL

#define BOARD_VTIMER_INTID	19	/* DT: <GIC_PPI 3 ...>, not the usual 27 */
#define BOARD_PTIMER_INTID	18	/* DT: <GIC_PPI 2 ...> */

#define BOARD_NR_CPUS		8
#define BOARD_TIMEBASE_HZ	19200000UL

/* sweet reports one core per Aff1 (0x000, 0x100, ... 0x700). */
#define BOARD_CPU_ON_MPIDR(i)	(((uint64_t)(i)) << 8)

#define BOARD_IMEM_RESTART_REASON 0x146aa65cUL
#define BOARD_PSHOLD		0x0c264000UL

#define BOARD_UFS_BASE		0x01d84000UL

#define BOARD_BOOT_HEADER_VERSION 2

/* sweet's ABL allows `fastboot boot`, so a self-reset is a cheap retry. */
#define BOARD_HOLD_ON_EXIT	0

#else
#error "no board selected: pass -DBOARD_dipper or -DBOARD_sweet"
#endif

#ifndef BOARD_FB_BYTES
#define BOARD_FB_BYTES		(BOARD_FB_H * BOARD_FB_STRIDE)
#endif
