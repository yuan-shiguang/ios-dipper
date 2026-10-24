# ios-dipper

**Apple's iOS 16, booting on a Xiaomi Mi 8.**

This project ports Apple's open-source XNU kernel (xnu-8796, the iOS 16 generation) to the Xiaomi Mi 8 (`dipper`, Qualcomm SDM845 / Snapdragon 845). On top of it runs the **unmodified iOS 16.7.16 (20H392) userland of the iPhone X**: launchd, the system daemons, backboardd, SpringBoard and the stock apps.

> **Bring-up in progress.** The bare-metal shim is verified on the phone (console, framebuffer, device-tree handover, PSCI, all eight cores). The XNU kernel builds in CI for the `DIPPER` machine config; booting it on the phone is the next step. See `NOTES.md` for the live status.

> Experimental bring-up, not a usable phone OS. Many things are missing or stubbed, and Apple's security model is mostly switched off. It installs into the `userdata` partition and so **erases Android's user data**.

## What works

| Area | State |
| --- | --- |
| Shim | Boots on the phone through the stock bootloader; full text console on the ABL framebuffer, `HOLD`s instead of resetting |
| CPU | All 8 cores (4× Cortex-A75 + 4× Cortex-A55) via PSCI `CPU_ON`, MPIDR `i << 8`; 16K pages, ARMv8.2 without pointer authentication |
| Timer / GIC | 19.2 MHz architected timer (INTID 19), GICv3 with TrustZone owning Group 0, so interrupts arrive as Group 1 IRQs |
| Device tree | The live FDT is handed over at a fixed address and parsed; the 65-tree DTB tail of the stock `boot.img` is what ABL matches on |
| Kernel build | `MACHINE_CONFIG=DIPPER` compiles the whole patch series on GitHub's macOS runners |
| iOS userland | Real iPhone X 16.7.16 root filesystem and shared cache, built from the IPSW images |
| Display | Kernel IOSurface + IOMobileFramebuffer (`AppleCLCD`) services; CoreAnimation renders in software; frames are copied into the bootloader's framebuffer. Presented to iOS as an iPhone X panel (1125×2436) |
| Device identity | iPhone X (`iPhone10,3` / `D22AP`): no Home button, gesture UI |

## What does not work (yet)

- **XNU is not booted on the phone yet.** The shim reaches its console and holds; the payload boot is the open bug (see `NOTES.md`).
- **No Wi-Fi, Bluetooth, cellular, audio, camera, sensors, GPS or SEP / Face ID.**
- **No GPU.** Everything would be drawn by the CPU.
- **Few apps.** The image has only what is on the system volume.
- **Security is off:** permissive code signing (AMFI) and sandbox policies, a stub AppleKeyStore, no data protection.

## How it boots

```
ABL (stock Xiaomi bootloader, unlocked)
  -> fastboot flash boot shim-boot.img        (ABL rejects `fastboot boot` on this board)
     -> shim: own MMU setup, reads the live FDT, loads the payload from the ramdisk
        -> XNU Mach-O + generated Apple Device Tree (ADT) + boot-args
           -> XNU -> UFS driver -> HFS+ root in the userdata partition
              -> dyld + shared cache -> launchd -> daemons -> backboardd + SpringBoard
```

`ABL` refuses `fastboot boot` for every image on this phone, stock one included, so the shim is only ever started by `fastboot flash boot`. That makes every bad build a trip through fastboot, and it is why the shim **holds** at the end of its run instead of rebooting: a self-reset would bounce straight back into the bootloader and the screen would never get read. Power 10 s to get back to fastboot.

The board has a debug UART (GENI SE9 at `0xa84000`), but the pads are under the back cover and a gated GENI stalls the interconnect instead of erroring, so it is compiled out by default (`SHIM_UART=0`). The kernel therefore draws its console into the framebuffer ABL leaves at `0x9d400000` (1080×2248, 4320 B stride) and keeps a persistent 4 MiB log ring at the end of `userdata`, which you read from recovery with `tools/dipper-log-pull.sh`.

## How to do it yourself

You need a Xiaomi Mi 8 (`dipper`) with an **unlocked bootloader**, a Linux PC to build the shim and the root image, and a GitHub account for the kernel build. **Everything on `userdata` is lost.** Bootloaders, recovery, modem and persist partitions are never touched.

### 0. Back up the phone

Boot OrangeFox (or any recovery with adb root) temporarily and pull at least `persist`, `modemst1`, `modemst2`, `fsg`, `fsc` and `boot`, `dtbo`, `vbmeta`. They contain per-device calibration data. Keep them off git; `.gitignore` already excludes `backup/`, `dtb/` and `recovery/`.

The shim needs the whole DTB tail of your stock `boot.img`. Unpack it once:

```sh
python3 tools/extract_dtb.py backup/boot.img dtb/dipper --list   # 65 trees, dipper board-ids
```

### 1. Build the kernel

The kernel builds on GitHub's macOS runners with [blacktop/darwin-xnu-build](https://github.com/blacktop/darwin-xnu-build). The workflow fetches xnu-8796, applies `xnu/patches/` in order, and also stages the pinned open-source libpthread, HFS and BearSSL sources it needs. Fork the repo (public repos get free macOS minutes) and run:

```sh
gh workflow run xnu.yml -R YOU/ios-dipper
gh run download RUN_ID -R YOU/ios-dipper -n xnu-kernel -D xnu/out
```

To check a patch locally before spending a CI run, `tools/xnu-cc.py` replays the compile commands of a previous CI log for single files.

### 2. Build the iOS root image

Download the iPhone X (iPhone10,3) iOS 16.7.16 (20H392) IPSW from Apple. Then, on Linux, with `apfs-fuse`, `hfsprogs` and sudo:

```sh
# OS, SystemOS and AppOS disk images from the IPSW (see BuildManifest.plist for their names)
tools/mkiosroot.sh OS.dmg SystemOS.dmg AppOS.dmg ios16-root.img 16G
sudo fsck.hfsplus -fy ios16-root.img
sudo mount -t hfsplus -o loop,force ios16-root.img /mnt/iosroot
sudo python3 tools/dipper-root-tweaks.py /mnt/iosroot
sudo umount /mnt/iosroot
img2simg ios16-root.img ios16-root.simg
```

`mkiosroot.sh` copies the OS volume and both cryptexes into one case-sensitive journaled HFS+ volume. It adds the dyld cache links, the plain system keybag and the log directories the daemons expect. `dipper-root-tweaks.py` applies the bring-up changes: it skips Setup, seeds the activation state, removes the baseband daemon, disables the watchdogs that kill slow apps and the crash-looping media paths, and so on. The script's header explains each change.

The device tree builder copies `/product` from the real iPhone X device tree, which MobileGestalt needs. Extract it once, without downloading the whole IPSW:

```sh
python3 tools/remotezip.py IPSW_URL DeviceTree.d22ap.im4p research/ipsw-x/dt
ipsw img4 im4p extract research/ipsw-x/dt/Firmware/all_flash/DeviceTree.d22ap.im4p -o research/ipsw-x/d22ap.adt
```

### 3. Build the boot image

Needs `aarch64-linux-gnu-gcc` and `mkbootimg`, and must run on Linux (a Windows checkout reaches the compiler through WSL, which is slow enough to look like a hang).

```sh
python3 tools/mkadt.py shim/out/dipper.adt --highmem
python3 tools/mkpayload.py xnu/out/kernel.development.dipper shim/out/dipper.adt shim/out/payload.bin \
    --cmdline 'debug=0xc serial=3 cpus=8 keepsyms=1 -enable_kprintf_spam -noprogress rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin ufsrw=1 rootrw=1 launchd_unsecure_cache=1 wdt=-1 dipperfb_noconsole=1 dipperfb_hz=30'
shim/build.sh ../shim/out/payload.bin      # -> shim/out/shim-boot.img
```

`shim/build.sh` refuses to emit an image `check_bootimg.py` does not like: the phone can only be flashed, so a bad `boot.img` means a trip through fastboot and a manual rollback.

What the less obvious boot-args do:

| Boot-arg | Effect |
| --- | --- |
| `rd=ufsroot0 ufsrw=1 rootrw=1` | Root on the `userdata` partition, writable |
| `cpus=8` | Bring up all eight cores (4 A75 + 4 A55); `cpus=1` is the safe fallback |
| `launchd_unsecure_cache=1` | launchd accepts its job cache without AMFI |
| `wdt=-1 wdt_test=no_user_checkin` | No hardware watchdog |
| `dipperfb_noconsole=1` | Stop drawing kernel text over the GUI. It still goes to the persistent log; panics are drawn |
| `dipperfb_hz=30` | Panel refresh rate of the CPU copy |
| `dippertouch=0`, `dipperkeys=0` | Disable the touch or button driver |

### 4. Flash the root and boot

```sh
fastboot -S 512M flash userdata ios16-root.simg   # ~7 minutes; do not unplug
# Xiaomi's fastboot often hangs after a large flash: re-enter fastboot with Power + Volume down
fastboot flash boot shim/out/shim-boot.img
fastboot reboot
```

Two commands, not one: `fastboot flash boot x.img | fastboot reboot` runs both at once, the reboot wins the race, the flash re-runs and prints OKAY, and nothing reboots the phone afterwards. That is where most "stuck at fastboot" reports came from. Always read the `BUILD` line (compile timestamp, second console line) before believing anything else on the screen - it is the only way to tell a stale boot image from a broken one.

To get back to fastboot, hold Power + Volume down.

### 5. Read the kernel log

```sh
fastboot flash boot recovery/recovery.img        # OrangeFox, temporary
fastboot reboot
tools/dipper-log-pull.sh logs/boot.txt      # 4 MiB console ring from the end of userdata
adb reboot bootloader
```

The log contains crash reports with backtraces (`CRASH name ...`), IOKit traces of backboardd and SpringBoard, and the drivers' `DIPPER ...` lines. `tools/dipper-crash.py` symbolicates them against the shared cache.

### QEMU

`tools/qemu-dipper.py` boots the same kernel on QEMU `virt` with a test RAM-disk root. It is useful for kernel-only changes; see `NOTES.md`.

## Repository layout

| Path | What |
| --- | --- |
| `shim/` | Bare-metal boot shim (C + asm) packed as an Android boot.img v1 with the stock 65-tree DTB tail |
| `xnu/patches/` | The XNU patch series (applied in order by CI), plus staging scripts for libpthread, HFS and BearSSL |
| `.github/workflows/xnu.yml` | Kernel CI build |
| `tools/mkadt.py` | Generates the Apple Device Tree from dipper's MMIO layout and the iPhone X identity |
| `tools/mkpayload.py` | Packs kernel + ADT + boot-args into the shim payload |
| `tools/mkiosroot.sh` | Builds the HFS+ iOS root from the IPSW images |
| `tools/dipper-root-tweaks.py` | Bring-up changes to the root (Setup skip, activation, daemons) |
| `tools/dipper-log-pull.sh`, `dipper-log.py` | Read the persistent kernel log from recovery |
| `tools/dipper-crash.py` | Symbolicate console crash reports |
| `tools/xnu-cc.py` | Local single-file compile check from a CI log |
| `tools/qemu-dipper.py` | QEMU test boots with framebuffer text decoding |
| `tools/adt.py`, `remotezip.py`, `extract_dtb.py`, `sparsediff.py` | Helpers |
| `NOTES.md` | Detailed engineering log: every boot, crash and decision, plus hardware facts |

Not in git: partition backups, live device trees, recovery images, Apple firmware and root images, and downloaded or generated build trees. They contain device serials and calibration data, or Apple's copyrighted files.

## Kernel patches

| # | What |
| --- | --- |
| 0001–0009 | Toolchain compatibility, DIPPER machine config, non-PAC arm64, stubs for Apple-internal pieces |
| 0010–0018 | Early framebuffer console, physmap fix, PRNG without corecrypto, device-tree platform expert, timer PPI, boot CPU driver |
| 0019–0020 | Apple open-source libpthread kernel provider; BearSSL AES |
| 0021–0027 | RAM-disk root, mockfs, SHA-256, minimal AMFI, Apple open-source HFS+, MD5 |
| 0028–0034 | UFS host driver and root device, read/write root, DMA above 4 GiB |
| 0035–0045 | Entitlements, bindfs, AppleKeyStore stub, IOWatchdog, console crash reports |
| 0046 | IOSurface (IOCoreSurfaceRoot) and IOMobileFramebuffer (AppleCLCD) services |
| 0047–0055 | Diagnostics: persistent log, /dev/null tee, IOKit trace, heap strings and user stack sampler on crash; permissive Sandbox policy; HFS rename-swap; AppleKeyStore selectors |
| 0056–0057 | Throttled panel copy; no external identity resolver |
| 0058 | HID event services. Apple's IOHIDFamily-1915.140.3 user client and event queue, matching the iOS 16.7 userland |
| 0059 | iPhone X identity (`hw.product` / `hw.target` from the device tree) |
| 0060 | Power and volume keys over SPMI; touch over GENI I²C |
| 0061 | Present the display as 1125×2436 |
| 0062 | RPMh regulator vote for the touch rail; battery power source |
| 0063 | GENI FIFO packing fix and the Goodix GT9896 protocol |
| 0064 | Grant PRIV_WORK_INTERVAL (UIKit's CoreAnimation work interval) |
| 0065 | SMP: secondary cores via PSCI CPU_ON |
| 0066–0067 | Fault-region crash info; console mute |
| 0068 | IOSurface lookup semantics for already-mapped surfaces |
| 0069 | Batched persistent-log flushes |
| 0070–0081 | USB gadget/ECM, live log, SMP tuning, display power, net trace |

## Key hardware facts

All of these were measured on the phone, not read from documentation - the SDM845 docs are wrong about at least the CPU topology.

- RAM: payloads use the upper bank, `0x100000000` + `0xfcc00000` (about 3.95 GiB), above Qualcomm's protected carve-outs.
- **MPIDR is flat: Aff1 = core number, Aff0 always 0.** There is no 4+4 cluster layer. The boot core reads `0x81000000`, the seven secondaries `0x8100nn00`, and PSCI `CPU_ON` only returns 0 with `reg = core << 8`; the textbook `(cluster << 8) | core` leaves cpu4..cpu7 stuck at `ON_PENDING`.
- GICv3 at `0x17a00000` (distributor) / `0x17a60000` (redistributors, 128 KB per PE). `GICD_CTLR.DS = 0`, so EL3 owns the security configuration: `ARE_NS` and `GICR_IGROUPR0` are write-ignored from EL1 and every interrupt, the timer included, arrives as a Group 1 IRQ.
- Timer: `CNTFRQ_EL0` = 19.2 MHz, virtual timer INTID 19 (Qualcomm numbers the PPIs 1/2/3/0, not 13/14/11/10).
- `AFFINITY_INFO` (SMC64 `0xc4000004`) is fatal on this device - an unknown id is answered with a reset rather than `NOT_SUPPORTED`. `CPU_ON` (`0xc4000003`) works for all seven secondaries.
- Display: 1080×2248 AMOLED, continuous splash from ABL at `0x9d400000`, 4320 B row pitch (no 32-pixel alignment), whole carve-out `0x2400000`.
- UFS host controller at `0x01d84000`; imem restart-reason window at `0x146bf65c`, PSHOLD at `0x0c264000`.
- The stock `boot.img` is header v1 with `Image.gz-dtb`: the kernel field is `gzip` followed by **65 concatenated DTBs**, and ABL picks the one whose `qcom,board-id` matches the phone (`0x1e/0x1f/0x24/0x25/0x26/0x32..0x37`).

More in [`NOTES.md`](NOTES.md).

## Credits

Built on Apple's open-source releases (xnu, IOHIDFamily, libpthread, hfs), [BearSSL](https://bearssl.org), [blacktop/darwin-xnu-build](https://github.com/blacktop/darwin-xnu-build), [ipsw](https://github.com/blacktop/ipsw), Qualcomm/Xiaomi's published Linux kernel sources for the hardware details, and [OrangeFox Recovery](https://orangefox.download). iOS itself is Apple's and is not distributed here.
