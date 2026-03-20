#!/usr/bin/env python3
"""Generate the DMG installer background image with drag-to-install instructions.

Uses macOS-native CoreGraphics via PyObjC.
Output: media/dmg_background.png (660x400, matches create-dmg --window-size exactly)

Layout (660x400 window, 1x coords):
  - Instruction text at top (~y=40)
  - App icon at (160, 175), Applications at (500, 175), size 120
  - Arrow between icons at ~y=175
"""

import math
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    import Cocoa
    import CoreText
    import Quartz
except ImportError:
    print("Required: pip install pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-CoreText")
    sys.exit(1)


def generate_dmg_background():
    # Match window size exactly (1x) — Finder tiles/stretches if mismatched
    W, H = 660, 400
    # Icon positions from create-dmg
    ICON_APP_X, ICON_APPS_X = 160, 500
    ICON_Y = 175
    ICON_SIZE = 120

    color_space = Quartz.CGColorSpaceCreateDeviceRGB()
    ctx = Quartz.CGBitmapContextCreate(
        None, W, H, 8, W * 4, color_space,
        Quartz.kCGImageAlphaPremultipliedLast,
    )

    # ── White background ──
    Quartz.CGContextSetRGBFillColor(ctx, 1.0, 1.0, 1.0, 1.0)
    Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, W, H))

    # ── Instruction text at top, centered ──
    # CG origin is bottom-left; y=40 from top in window = H - 40 in CG
    _draw_text(ctx, "Drag to Applications to install", W / 2, H - 40,
               size=21, color=(0.3, 0.3, 0.3, 1.0))

    # ── Arrow between icons ──
    # Arrow at icon center height: CG y = H - ICON_Y
    arrow_y = H - ICON_Y
    arrow_x1 = ICON_APP_X + ICON_SIZE // 2 + 20
    arrow_x2 = ICON_APPS_X - ICON_SIZE // 2 - 20
    _draw_arrow(ctx, arrow_x1, arrow_y, arrow_x2, arrow_y,
                color=(0.5, 0.5, 0.5, 0.7), line_width=2.5, head_size=12)

    # ── Save ──
    output_path = os.path.join(PROJECT_ROOT, "media", "dmg_background.png")
    cg_image = Quartz.CGBitmapContextCreateImage(ctx)
    url = Cocoa.NSURL.fileURLWithPath_(output_path)
    dest = Quartz.CGImageDestinationCreateWithURL(url, "public.png", 1, None)
    Quartz.CGImageDestinationAddImage(dest, cg_image, None)
    Quartz.CGImageDestinationFinalize(dest)
    print(f"Generated: {output_path} ({W}x{H})")


def _draw_text(ctx, text, cx, cy, size=36, color=(1, 1, 1, 1)):
    """Draw centered text at (cx, cy)."""
    font = Cocoa.NSFont.systemFontOfSize_weight_(size, 0.23)  # medium weight
    attrs = {
        CoreText.kCTFontAttributeName: font,
        CoreText.kCTForegroundColorFromContextAttributeName: True,
    }
    attr_string = Cocoa.NSAttributedString.alloc().initWithString_attributes_(
        text, attrs)
    line = CoreText.CTLineCreateWithAttributedString(attr_string)
    bounds = CoreText.CTLineGetBoundsWithOptions(line, 0)
    tx = cx - bounds.size.width / 2
    ty = cy - bounds.size.height / 2

    Quartz.CGContextSetRGBFillColor(ctx, *color)
    Quartz.CGContextSetTextPosition(ctx, tx, ty)
    CoreText.CTLineDraw(line, ctx)


def _draw_arrow(ctx, x1, y1, x2, y2, color=(1, 1, 1, 0.7),
                line_width=4.0, head_size=20):
    """Draw a horizontal arrow from (x1,y1) to (x2,y2)."""
    Quartz.CGContextSetRGBStrokeColor(ctx, *color)
    Quartz.CGContextSetRGBFillColor(ctx, *color)
    Quartz.CGContextSetLineWidth(ctx, line_width)
    Quartz.CGContextSetLineCap(ctx, Quartz.kCGLineCapRound)

    # Shaft
    Quartz.CGContextMoveToPoint(ctx, x1, y1)
    Quartz.CGContextAddLineToPoint(ctx, x2 - head_size, y2)
    Quartz.CGContextStrokePath(ctx)

    # Arrowhead
    angle = math.atan2(y2 - y1, x2 - x1)
    Quartz.CGContextMoveToPoint(ctx, x2, y2)
    Quartz.CGContextAddLineToPoint(
        ctx, x2 - head_size * math.cos(angle - math.pi / 6),
        y2 - head_size * math.sin(angle - math.pi / 6))
    Quartz.CGContextAddLineToPoint(
        ctx, x2 - head_size * math.cos(angle + math.pi / 6),
        y2 - head_size * math.sin(angle + math.pi / 6))
    Quartz.CGContextClosePath(ctx)
    Quartz.CGContextFillPath(ctx)


if __name__ == "__main__":
    generate_dmg_background()
