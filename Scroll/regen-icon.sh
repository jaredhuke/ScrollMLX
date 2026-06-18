#!/usr/bin/env bash
# Regenerate the app icon from Scroll/icon-master.svg with TRANSPARENT corners.
#
# Why this script: qlmanage rasterizes SVG onto an opaque WHITE matte, so a plain
# `qlmanage -t … | sips` pipeline gives square white corners. We render with qlmanage,
# then apply a rounded-rect alpha mask (matching the SVG's rx=256 @1024) in Pillow so the
# squircle corners are truly transparent — then emit every appiconset size + AppIcon.png +
# AppIcon.icns. Pillow is pulled ephemerally via `uv run --with pillow` (no repo dep).
set -e
cd "$(dirname "$0")/.."
SVG="Scroll/icon-master.svg"
ICONSET="Scroll/Sources/Scroll/Resources/Assets.xcassets/AppIcon.appiconset"
RES="Scroll/Sources/Scroll/Resources"

rm -f /tmp/iconbase.png
qlmanage -t -s 1024 -o /tmp "$SVG" >/dev/null 2>&1
mv /tmp/icon-master.svg.png /tmp/iconbase.png

uv run --with pillow python - "$ICONSET" "$RES" <<'PY'
import sys, os
from PIL import Image, ImageDraw
ICONSET, RES = sys.argv[1], sys.argv[2]
base = Image.open("/tmp/iconbase.png").convert("RGBA")
if base.size != (1024,1024): base = base.resize((1024,1024), Image.LANCZOS)
# supersampled rounded-rect mask → crisp anti-aliased transparent corners (rx 256 @1024)
S=4; W=1024*S; R=256*S
m = Image.new("L",(W,W),0)
ImageDraw.Draw(m).rounded_rectangle([0,0,W-1,W-1], radius=R, fill=255)
base.putalpha(m.resize((1024,1024), Image.LANCZOS))
for s in (16,32,64,128,256,512,1024):
    base.resize((s,s), Image.LANCZOS).save(f"{ICONSET}/icon_{s}.png")
base.save(f"{RES}/AppIcon.png")
os.makedirs("/tmp/Scroll.iconset", exist_ok=True)
for sz,name in [(16,"icon_16x16"),(32,"icon_16x16@2x"),(32,"icon_32x32"),(64,"icon_32x32@2x"),
                (128,"icon_128x128"),(256,"icon_128x128@2x"),(256,"icon_256x256"),
                (512,"icon_256x256@2x"),(512,"icon_512x512"),(1024,"icon_512x512@2x")]:
    base.resize((sz,sz), Image.LANCZOS).save(f"/tmp/Scroll.iconset/{name}.png")
print("icon sizes written with transparent corners")
PY

iconutil -c icns /tmp/Scroll.iconset -o "$RES/AppIcon.icns"
echo "✓ icon regenerated (transparent corners) — appiconset + AppIcon.png + AppIcon.icns"
