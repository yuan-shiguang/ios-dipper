#!/usr/bin/env python3
"""Verify the host console render: scrolling, text area, milestone bar.

Reads the PPM written by host_test plus the glyph table from font.h and
checks what actually matters for the UFS build:

  1. the text area holds exactly MAX_ROW lines, packed from the top,
  2. the milestone bar is a uniform strip at the very bottom, and
  3. no glyph is ever painted into the bar,
  4. after overflowing by 12 lines the *last* 45 lines are the ones shown
     (L12 at the top, L56 at the bottom) - i.e. the scroll really moved
     the text instead of dropping it.
"""
import re
import sys

CH_H, CH_W, CON_TOP, MARGIN, MARK_H, MAX_ROW = 48, 24, 24, 24, 64, 45
FB_W, FB_H = 1080, 2248
BAR_TOP = FB_H - MARK_H
NCOLS = 3                       # characters of the label we OCR


def load_font(path="font.h"):
    txt = open(path).read()
    body = txt[txt.index("{") + 1: txt.rindex("}")]
    rows = re.findall(r"\{([^}]*)\}", body)
    font = []
    for r in rows:
        font.append([int(v, 16) for v in r.split(",")])
    assert len(font) == 95, len(font)
    return font


def main():
    ppm = sys.argv[1] if len(sys.argv) > 1 else "host.ppm"
    font = load_font(sys.argv[2] if len(sys.argv) > 2 else "font.h")

    d = open(ppm, "rb").read()
    nl = d.index(b"\n")
    assert d[:2] == b"P6", d[:nl]
    w, h = map(int, d[:nl].split()[1:3])
    assert (w, h) == (FB_W, FB_H), (w, h)
    px = d[nl + 1:]

    def pixel(x, y):
        i = (y * w + x) * 3
        return (px[i] << 16) | (px[i + 1] << 8) | px[i + 2]

    def is_on(x, y):
        return pixel(x, y) > 0x808080

    def ocr(row_y, ncols=NCOLS):
        """Read ncols glyphs out of the text line starting at row_y."""
        out = []
        for c in range(ncols):
            x0 = MARGIN + c * CH_W
            best, bestscore = "?", -1
            for gi, g in enumerate(font):
                ch = chr(gi + 0x20)
                if not (32 <= ord(ch) < 127):
                    continue
                hit = tot = 0
                for gy in range(16):
                    for gx in range(8):
                        tot += 1
                        if is_on(x0 + gx * 3, row_y + gy * 3) == bool(g[gy] & (0x80 >> gx)):
                            hit += 1
                if hit > bestscore:
                    best, bestscore = ch, hit
            out.append(best if bestscore > tot * 0.9 else "?")
        return "".join(out)

    fails = []

    # --- text area -------------------------------------------------------
    lines = {}
    for i in range(MAX_ROW):
        y = CON_TOP + i * CH_H
        lines[i] = (y, ocr(y))

    used = [i for i in range(MAX_ROW) if lines[i][1].strip("?")]
    if len(used) != MAX_ROW:
        fails.append("expected %d readable lines, got %d" % (MAX_ROW, len(used)))

    top = lines[0][1]
    bot = lines[MAX_ROW - 1][1]
    if top != "L12":
        fails.append("top line reads %r, expected 'L12'" % top)
    if bot != "L56":
        fails.append("bottom line reads %r, expected 'L56'" % bot)

    # --- milestone bar ---------------------------------------------------
    bar_rows = range(BAR_TOP, FB_H)
    nonuni = [y for y in bar_rows if len({pixel(x, y) for x in range(w)}) != 1]
    if nonuni:
        fails.append("bar not uniform at y=%s" % nonuni[:5])
    # the PPM has no alpha channel, so 0xff808080 shows up as 0x00808080
    bar_col = pixel(w // 2, BAR_TOP + MARK_H // 2)
    if bar_col != 0x00808080:
        fails.append("bar colour %08x, expected 00808080" % bar_col)

    # --- no glyphs in the bar -------------------------------------------
    inside = [y for y in bar_rows if any(is_on(x, y) for x in range(0, w, 7))]
    if inside:
        fails.append("glyphs painted into the bar at y=%s" % inside[:5])

    # --- text never crosses into the bar ---------------------------------
    below = [i for i in range(MAX_ROW)
             if any(is_on(x, y) for y in range(BAR_TOP - 2, FB_H) for x in range(0, w, 7))]
    if below:
        fails.append("text reaches into the bar from line %s" % below[:5])

    print("lines shown    : %d of %d rows" % (len(used), MAX_ROW))
    print("top line       : %r   (expected L12)" % top)
    print("bottom line    : %r   (expected L56)" % bot)
    print("last text y    : %d, bar starts at %d" % (CON_TOP + (MAX_ROW - 1) * CH_H + CH_H, BAR_TOP))
    print("bar            : %08x, uniform=%s" % (bar_col, not nonuni))
    print("glyphs in bar  : %s" % (inside[:3] or "none"))

    if fails:
        print("\nFAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("\nPASS: scroll keeps the last %d lines, the bar stays put and clean" % MAX_ROW)
    return 0


sys.exit(main())
