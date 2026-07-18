"""Generate the DeepSeek Deck app icon.

Motif: a stack of live agent panels (a 'deck') in Claude coral on a dark
squircle — parallel DeepSeek workers, streaming transcripts.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("icon_1024.png")


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def vgrad(size, top, bot):
    img = Image.new("RGB", (1, size))
    for y in range(size):
        img.putpixel((0, y), lerp(top, bot, y / max(1, size - 1)))
    return img.resize((size, size))


def rounded_mask(size, radius, pad):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=radius, fill=255)
    return m


def panel(w, h, radius, base, header):
    """A single rounded panel: coral body + lighter header strip + text lines."""
    p = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(p)
    d.rounded_rectangle([0, 0, w, h], radius=radius, fill=base + (255,))
    # header strip
    hh = int(h * 0.24)
    strip = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ds = ImageDraw.Draw(strip)
    ds.rounded_rectangle([0, 0, w, hh + radius], radius=radius, fill=header + (255,))
    ds.rectangle([0, hh, w, hh + radius], fill=header + (255,))
    # clip strip to panel shape
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w, h], radius=radius, fill=255)
    p.paste(strip, (0, 0), Image.composite(strip.split()[-1], Image.new("L", (w, h), 0), mask))
    # header dots
    dot = int(h * 0.05)
    cy = hh // 2
    for i, col in enumerate([(255, 255, 255, 230)] * 3):
        cx = int(w * 0.12) + i * int(dot * 2.3)
        d.ellipse([cx, cy - dot, cx + dot * 1.2, cy + dot * 0.2], fill=col)
    # body transcript lines
    lc = (255, 250, 245, 240)
    x0 = int(w * 0.13)
    y = hh + int(h * 0.16)
    for wid in (0.62, 0.74, 0.5, 0.68):
        d.rounded_rectangle([x0, y, x0 + int(w * wid), y + int(h * 0.055)],
                            radius=int(h * 0.03), fill=lc)
        y += int(h * 0.14)
    return p


def main():
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # squircle background with vertical gradient
    pad = 84
    radius = 210
    mask = rounded_mask(S, radius, pad)
    grad = vgrad(S, (0x2a, 0x27, 0x22), (0x12, 0x11, 0x10)).convert("RGBA")
    base.paste(grad, (0, 0), mask)

    # subtle inner top highlight
    hi = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(hi).rounded_rectangle([pad, pad, S - pad, S - pad], radius=radius,
                                         outline=(255, 255, 255, 34), width=3)
    base = Image.alpha_composite(base, hi)

    # three stacked panels, offset diagonally (back -> front)
    pw, ph = int(S * 0.44), int(S * 0.47)
    pr = 46
    specs = [
        (int(S * 0.28), int(S * 0.35), (0x8f, 0x4c, 0x30), (0xa6, 0x5c, 0x3c)),
        (int(S * 0.35), int(S * 0.27), (0xba, 0x63, 0x41), (0xd0, 0x76, 0x50)),
        (int(S * 0.42), int(S * 0.19), (0xdd, 0x83, 0x59), (0xf0, 0x9d, 0x74)),
    ]
    for x, y, bcol, hcol in specs:
        pnl = panel(pw, ph, pr, bcol, hcol)
        # drop shadow
        sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        shp = Image.new("RGBA", (pw, ph), (0, 0, 0, 130))
        smask = Image.new("L", (pw, ph), 0)
        ImageDraw.Draw(smask).rounded_rectangle([0, 0, pw, ph], radius=pr, fill=255)
        sh.paste(shp, (x + 10, y + 18), smask)
        sh = sh.filter(ImageFilter.GaussianBlur(16))
        base = Image.alpha_composite(base, sh)
        layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        layer.paste(pnl, (x, y), pnl)
        base = Image.alpha_composite(base, layer)

    # re-clip everything to the squircle so shadows don't spill past the edge
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(base, (0, 0), mask)
    out.save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
