#!/usr/bin/env python3
"""生成 dipper（小米 8 / SDM845）启动 XNU 所需的最小 Apple Device Tree (ADT)。

用法（必须放在 ios-sweet/tools/ 目录下，依赖同目录的 adt.py）：

    cp mkadt_dipper.py  ios-sweet/tools/
    cd ios-sweet/tools
    python3 mkadt_dipper.py dipper.adt --dump --highmem \
        --dram-base 0x100000000 --dram-size 0xf0000000 --fb 0x9c000000

设计原则
--------
1. 结构照抄 ios-sweet/tools/mkadt.py —— 那些属性不是随便加的，每一个都对应
   XNU 早期启动路径里的一次"读不到就卡死"（见 docs/02 第五节）。
2. 只换 dipper 的硬件常量，逻辑不创新。**不要在这里加自己的东西**，
   加之前先确认 XNU 真的会读它。
3. 所有地址默认值都是"大概率对"，标 ⚠️ 的必须用真机 live FDT 核对。

Apple DT 的二进制格式没有公开文档，序列化/解析全部交给 ios-sweet 的 adt.py。
"""
import argparse
import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt  # noqa: E402  (ios-sweet/tools/adt.py)

# ---------------------------------------------------------------------------
# dipper / SDM845 硬件常量
# ---------------------------------------------------------------------------
SOC_BASE = 0x01000000          # /arm-io 子节点地址相对基准；不能为 0，否则 serial_init 直接退出

GICD, GICD_SIZE = 0x17A00000, 0x10000      # SDM845 GICD（与 SM7150 同段）
GICR, GICR_SIZE = 0x17A60000, 0x100000     # SDM845 GICR
GICR_PE_SIZE = 0x20000                     # ✅ 实测：GICR_TYPER.PLPIS=1 VLPIS=0
                                           #   -> 128 KB 重分发器粒度，两个 64 KB 帧
TIMEBASE = 19200000                        # ✅ 实测 CNTFRQ_EL0
VTIMER_INTID = 19                          # ✅ 实测 ICC_IAR1_EL1 返回 19
PTIMER_INTID = 18                          # DT: <GIC_PPI 2 ...>

UFS, UFS_SIZE = 0x01D84000, 0x3000         # ufshc@1d84000，SDM845 与 SM7150 同址

# 默认 DRAM：低 bank 走 0xb0000000（避开 Qualcomm XPU carve-out），高 bank 走整块
LOW_PHYS_BASE, LOW_MEM_SIZE = 0xB0000000, 0x4D000000
HIGH_PHYS_BASE = 0x100000000
HIGH_MEM_SIZE = 0xF0000000                 # ⚠️ 6G 机型按 3.75G 保守取值，以 live FDT 为准

PANIC_LOG_SIZE = 0x100000                  # 1 MiB，从 dram 顶部切出来给 /pram

# 面板：Mi 8 是 1080x2248 AMOLED；但画布按 iPhone X 的 1125x2436 呈现，
# 由 framebuffer 驱动做缩放（和 sweet 的 1080x2400 → 1125x2436 同理）
FB_W, FB_H = 1080, 2248
FB_STRIDE = FB_W * 4
CANVAS_W, CANVAS_H = 1125, 2436
FB_BASE_DEFAULT = 0x9D400000               # ✅ 实测：ABL 留下的 splash framebuffer，
                                           #   shim 在上面画了整屏控制台并被人眼确认
FB_SIZE = 0x2400000                        # ✅ 实测：board.h BOARD_FB_BYTES，整个 carve-out
                                           #   0x9d400000 + 0x2400000 = 0x9f800000

DIPPER_ECID = 0x4449505045520001           # 'DIPPER' + 1，合成值，不是真机 ECID
IPHONE_X_CHIP_ID = 0x8015                  # A11，因为跑的是 iPhone X 用户态
BOOT_MANIFEST_HASH = hashlib.sha384(b'dipper boot manifest').digest()

# CPU 拓扑：SDM845 = 4x A55 派生 (Silver) + 4x A75 派生 (Gold)。
#
# ✅ 实测 MPIDR 布局（2026-10-23，shim PSCI CPU_ON 往返）：
#     引导核 mpidr = 0x81000000，七个次核 mpidr = 0x8100nn00 (n = 1..7)
#   即 **Aff1 = 核号，Aff0 恒为 0**，扁平编号，没有集群层。
#   这不是推测：board.h 里先用 ((i>>2)<<8)|(i&3)（标准 4+4 集群图）试过，
#   次核回来的是 0x81000200/0x81000100/0x81000300（上下文 2/5/7 对应 cpu2/cpu1/cpu3），
#   cpu4..7 始终起不来，三个上下文返回 -5 (ON_PENDING)。改成 DT 原值 i<<8 后
#   七个次核全部 ret=0。Qualcomm 把核号放在 Aff1，和 sweet 一样。
#
# 所以 reg 必须是 core<<8。之前这里写的是 (cluster<<8)|(core&0xF)，正好是
# 被实测否定的那张表，会让 XNU 的 ml_parse_cpu_topology 一个 CPU 都匹配不上。
CPU_LAYOUT = [
    # (core, clock_hz)
    (0, 1766400000),   # Kryo 385 Silver，A55 派生
    (1, 1766400000),
    (2, 1766400000),
    (3, 1766400000),
    (4, 2803200000),   # Kryo 385 Gold，A75 派生
    (5, 2803200000),
    (6, 2803200000),
    (7, 2803200000),
]

u32 = lambda *v: struct.pack('<%dI' % len(v), *v)
u64 = lambda *v: struct.pack('<%dQ' % len(v), *v)
cstr = lambda s: s.encode() + b'\0'


def node(name, props=(), children=()):
    n = adt.Node()
    n.props = [('name', 0, cstr(name))] + [(k, 0, v) for k, v in props]
    n.children = list(children)
    return n


def iphone_x_product(path):
    """/product 节点取自真机 iPhone X (D22AP) 设备树，MobileGestalt 靠它识别机型。

    这是 Apple 的产物，不进 git。提取方法：
        tools/remotezip.py IPSW_URL DeviceTree.d22ap.im4p research/ipsw-x/dt
        ipsw img4 im4p extract research/ipsw-x/dt/Firmware/all_flash/DeviceTree.d22ap.im4p \
            -o research/ipsw-x/d22ap.adt
    """
    if not path or not os.path.exists(path):
        print('warning: 没有 d22ap.adt，跳过 /product 节点（MobileGestalt 会认不出机型）',
              file=sys.stderr)
        return None
    root, _ = adt.parse(open(path, 'rb').read())
    for c in root.children:
        if dict((k, v) for k, f, v in c.props).get('name') == b'product\0':
            return [(k, v) for k, f, v in c.props
                    if not f and k not in ('name', 'AAPL,phandle')]
    raise SystemExit('%s: 没有 /product 节点' % path)


def build(args):
    phys_base = args.dram_base
    mem_size = args.dram_size
    # 从顶部切 panic log，XNU 不会分配进去
    panic_base = phys_base + mem_size - PANIC_LOG_SIZE
    mem_size -= PANIC_LOG_SIZE

    seed = args.seed if args.seed is not None else os.urandom(64)
    phandle = iter(range(1, 1000))
    ph = lambda: ('AAPL,phandle', u32(next(phandle)))

    # ---------------- /chosen ----------------
    chosen = node('chosen', [
        ph(),
        ('dram-base', u64(phys_base)),
        ('dram-size', u64(mem_size)),
        ('random-seed', seed),
        ('debug-enabled', u32(1)),
        ('embedded-panic-log-size', u32(PANIC_LOG_SIZE)),
        ('firmware-version', cstr('dipper-shim')),
        ('system-firmware-version', cstr('dipper-shim')),
        # /sbin/mount -P 1 找不到它就退出
        ('boot-manifest-hash', BOOT_MANIFEST_HASH),
        # cryptexd 的 libimg4 需要，否则 abort: failed to get crypto algorithm
        ('crypto-hash-method', cstr('sha2-384')),
        # 1080x2248 面板呈现为 iPhone X 的 360x812pt @3x
        ('display-scale', u32(3)),
        # 没有这两个，CFPreferences 用不了 ByHost，每次启动都重跑 DataMigration
        ('unique-chip-id', struct.pack('<Q', args.ecid)),
        ('chip-id', u32(IPHONE_X_CHIP_ID)),
        # 开发版熔断状态 (0,0,1,1)：mobileactivationd 走原生 Hactivation 分支
        ('certificate-production-status', u32(0)),
        ('effective-production-status-ap', u32(0)),
        ('certificate-security-mode', u32(1)),
        ('effective-security-mode-sep', u32(1)),
    ], [
        node('memory-map', [ph()]),
        # SIP 放宽：dyld 没有共享缓存的 trust cache，必须允许未封装根
        node('asmb', [ph(), ('lp-sip0', u64(0x6F)), ('lp-sip1', u32(1))]),
    ])

    # ---------------- /cpus ----------------
    # 与 sweet 一致：reg 是真实 MPIDR 的 Aff1:Aff0，不是逻辑核号；
    # cluster-* 只是描述性的，XNU 按 DT 顺序分配逻辑 ID（见 ml_parse_cpu_topology）。
    # 八个核一个同构 SMP 集群（cluster-type 0），大小核差异只体现在 clock-frequency。
    cpu_nodes = []
    for core, clock in CPU_LAYOUT:
        cpu_nodes.append(node('cpu%d' % core, [
            ph(),
            ('device_type', cstr('cpu')),
            ('compatible', cstr('qcom,kryo385') + cstr('ARM,v8')),
            ('reg', u32(core << 8)),                 # 实测 MPIDR Aff1:Aff0
            ('cpu-id', u32(core)),
            ('cluster-id', u32(0)),                  # 同构 SMP
            ('cluster-type', u32(0)),                # CLUSTER_TYPE_SMP，不是 AMP
            ('die-cluster-id', u32(0)),
            ('cluster-core-id', u32(core)),
            ('l2-cache-id', u32(core)),
            ('l3-cache-id', u32(0)),
            ('state', cstr('running' if core == 0 else 'stopped')),
            ('timebase-frequency', u32(args.timebase)),
            ('clock-frequency', u32(clock)),
            ('bus-frequency', u32(100000000)),
            ('memory-frequency', u32(1866000000)),
            ('peripheral-frequency', u32(args.timebase)),
            ('fixed-frequency', u32(args.timebase)),
        ]))
    cpus = node('cpus', [ph(), ('#address-cells', u32(1)), ('#size-cells', u32(0))], cpu_nodes)

    # ---------------- /arm-io ----------------
    gic = node('gic', [
        ph(),
        ('compatible', cstr('arm,gic-v3')),
        ('interrupt-controller', b''),
        ('virtual-timer-intid', u32(args.vtimer_intid)),
        ('reg', u64(GICD - SOC_BASE, GICD_SIZE, GICR - SOC_BASE, GICR_SIZE)),
    ])
    arm_io = node('arm-io', [
        ph(),
        ('device_type', cstr('dipper-io')),
        ('compatible', cstr('dipper-io') + cstr('arm-io')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
        ('ranges', u64(0, SOC_BASE, 0x100000000 - SOC_BASE)),
    ], [
        gic,
        # watchdogd 检查 /arm-io/wdt 的 no-pmu，否则崩在设 PMU boot stage
        node('wdt', [ph(), ('no-pmu', u32(1))]),
        # IOMobileFramebuffer 数 /arm-io 下 device_type=display-subsystem 的子节点，
        # 画布尺寸从节点读；内核的 AppleCLCD 匹配 compatible
        node('disp0', [
            ph(),
            ('device_type', cstr('display-subsystem')),
            # iokit/KernelConfigTables.cpp 里 IOMobileFramebuffer 的
            # IONameMatch 写死 'sweet,display'，画布尺寸靠这个节点传递
            ('compatible', cstr('sweet,display')),
            ('canvas-width', u32(CANVAS_W)),
            ('canvas-height', u32(CANVAS_H)),
        ]),
    ])

    # ---------------- 平台私有节点 ----------------
    # 节点名必须与 sweet 版一致：pexpert/arm/pe_serial.c 用
    # SecureDTFindEntry("name", "sweet-fbcon") 找它，改名内核就看不见
    # framebuffer，控制台直接黑屏。
    fbcon = node('sweet-fbcon', [
        ph(),
        ('reg', u64(args.fb, FB_SIZE)),
        ('width', u32(FB_W)),
        ('height', u32(FB_H)),
        ('stride', u32(FB_STRIDE)),
    ])

    # bsd/dev/sweet_ufs.c 用 SecureDTLookupEntry(NULL, "/sweet-ufs") 绑定，
    # 且只读 reg 一个属性
    ufs = node('sweet-ufs', [
        ph(),
        ('compatible', cstr('qcom,ufshc')),
        ('reg', u64(UFS, UFS_SIZE)),
    ])

    defaults = node('defaults', [ph(), ('no-effaceable-storage', u32(1))])

    # MobileGestalt HomeButtonType = IODeviceTree:/buttons home-button-type
    # D22AP = 2（无 Home 键，手势 UI）
    buttons = node('buttons', [
        ph(),
        ('button-names', cstr('volup') + cstr('voldown') + cstr('hold')),
        ('home-button-type', u32(2)),
        ('opposed-power-vol-buttons', u32(1)),
    ])

    pram = node('pram', [ph(), ('reg', u64(panic_base) + u64(PANIC_LOG_SIZE))])

    product_props = iphone_x_product(args.d22ap_adt)
    product = [node('product', [ph()] + product_props)] if product_props else []

    # ---------------- 根 ----------------
    # compatible[0] → hw.model/hw.target (D22AP)
    # model         → hw.machine/hw.product (iPhone10,3)
    # 'xiaomi,dipper' 留给 DipperPlatformExpert 匹配
    return node('device-tree', [
        ph(),
        ('compatible', cstr('D22AP') + cstr('iPhone10,3') + cstr('xiaomi,dipper')
                       + cstr('qcom,sdm845') + cstr('AppleARM')),
        ('model', cstr('iPhone10,3')),
        ('target-type', cstr('D22')),
        ('target-sub-type', cstr('D22AP')),
        ('manufacturer', cstr('Apple Inc.')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
    ], [chosen, defaults, cpus, arm_io, fbcon, buttons, pram, ufs] + product)


def parse_int(s):
    return int(s, 0)


def main():
    ap = argparse.ArgumentParser(description='生成 dipper (SDM845) 的 Apple Device Tree')
    ap.add_argument('out')
    ap.add_argument('--dump', action='store_true', help='序列化后反解析并打印树')
    ap.add_argument('--highmem', action='store_true',
                    help='用高 bank（0x100000000）作为内核可用 RAM（真机必用）')
    ap.add_argument('--dram-base', type=parse_int, default=None, help='覆盖 dram-base')
    ap.add_argument('--dram-size', type=parse_int, default=None, help='覆盖 dram-size')
    ap.add_argument('--fb', type=parse_int, default=FB_BASE_DEFAULT, help='ABL framebuffer 物理地址')
    ap.add_argument('--timebase', type=parse_int, default=TIMEBASE)
    ap.add_argument('--vtimer-intid', type=parse_int, default=VTIMER_INTID)
    ap.add_argument('--ecid', type=parse_int, default=DIPPER_ECID, help='合成 ECID')
    ap.add_argument('--seed', type=lambda s: bytes.fromhex(s), default=None, help='64 字节随机种子')
    ap.add_argument('--d22ap-adt', default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '../research/ipsw-x/d22ap.adt'))
    args = ap.parse_args()

    if args.dram_base is None:
        args.dram_base = HIGH_PHYS_BASE if args.highmem else LOW_PHYS_BASE
    if args.dram_size is None:
        args.dram_size = HIGH_MEM_SIZE if args.highmem else LOW_MEM_SIZE
    if args.seed is not None and len(args.seed) != 64:
        raise SystemExit('--seed 必须是 64 字节')

    root = build(args)
    blob = adt.serialize(root)
    with open(args.out, 'wb') as f:
        f.write(blob)
    if args.dump:
        adt.dump(adt.parse(blob)[0])

    print('%s: %d bytes  dram=%#x+%#x  fb=%#x  cpus=%d'
          % (args.out, len(blob), args.dram_base, args.dram_size - PANIC_LOG_SIZE,
             args.fb, len(CPU_LAYOUT)), file=sys.stderr)


if __name__ == '__main__':
    main()
