"""Generate The Hub logo SVGs (hexagon hub with eight spokes). Run: python3 brand/make_logo.py"""
import math, pathlib
OUT = pathlib.Path(__file__).parent
BG = '#151518'

def mark(cx, cy, s, fill=None):
    """Mark centred at (cx, cy); s = hexagon circumradius. fill=None -> gradient."""
    paint = fill or 'url(#g)'
    w, dot, reach = s * 0.23, s * 0.19, s * 1.79
    hexagon = ' '.join(f'{cx + s * math.sin(math.radians(a)):.1f},{cy - s * math.cos(math.radians(a)):.1f}' for a in range(0, 360, 60))
    parts = [f'<polygon points="{hexagon}" fill="none" stroke="{paint}" stroke-width="{w:.1f}" stroke-linejoin="round"/>']
    for a in range(0, 360, 45):
        dx, dy = math.sin(math.radians(a)), -math.cos(math.radians(a))
        normal = 30 + 60 * round((a - 30) / 60)  # nearest edge normal of a pointy-top hexagon
        inner = s * math.sqrt(3) / 2 / math.cos(math.radians(a - normal))
        x1, y1, x2, y2 = cx + dx * inner, cy + dy * inner, cx + dx * reach, cy + dy * reach
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{paint}" stroke-width="{w * 0.62:.1f}"/>')
        parts.append(f'<circle cx="{x2:.1f}" cy="{y2:.1f}" r="{dot:.1f}" fill="{paint}"/>')
    return '\n  '.join(parts)

def svg(size, s, bg=None, radius=0, fill=None):
    c = size / 2
    grad = '' if fill else (f'<defs><linearGradient id="g" gradientUnits="userSpaceOnUse" x1="{c - 1.5 * s}" y1="{c - 0.6 * s}" x2="{c + 1.5 * s}" y2="{c + 0.6 * s}">'
                            '<stop offset="0" stop-color="#00F0FF"/><stop offset=".45" stop-color="#2BB8FF"/><stop offset=".72" stop-color="#6A5CF5"/><stop offset="1" stop-color="#7C4DEE"/></linearGradient></defs>\n  ')
    back = f'<rect width="{size}" height="{size}" rx="{radius}" fill="{bg}"/>\n  ' if bg else ''
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">\n  {grad}{back}{mark(c, c, s, fill)}\n</svg>\n'

variants = {
    'logo.svg': svg(1024, 222),                       # mark only, fills the square
    'icon.svg': svg(1024, 150, BG, 0),                # app icon, full bleed
    'icon-rounded.svg': svg(1024, 160, BG, 224),      # extension / touch icon
    'maskable.svg': svg(1024, 128, BG, 0),            # inside the 80% safe zone
    'android-foreground.svg': svg(1024, 150),         # 108dp canvas, 66dp safe zone
    'mono.svg': svg(1024, 150, fill='#FFFFFF'),
}
for name, text in variants.items():
    (OUT / name).write_text(text)
print('wrote', ', '.join(variants))
