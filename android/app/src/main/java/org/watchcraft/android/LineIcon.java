package org.watchcraft.android;

import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.RectF;
import android.graphics.drawable.Drawable;

/** Small vector icon set; no font glyphs or downloaded icon assets. */
final class LineIcon extends Drawable {
    private final String name;
    private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
    LineIcon(String name, int color) {
        this.name = name; p.setColor(color); p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(1.7f); p.setStrokeCap(Paint.Cap.ROUND); p.setStrokeJoin(Paint.Join.ROUND);
    }
    @Override public void draw(Canvas c) {
        c.save(); c.translate(getBounds().left, getBounds().top);
        c.scale(getBounds().width() / 24f, getBounds().height() / 24f);
        switch (name) {
            case "tv": rect(c, 3, 5, 21, 18, 3); line(c, 8, 22, 16, 22); line(c, 12, 18, 12, 22); break;
            case "today": rect(c, 4, 4, 20, 21, 3); line(c, 4, 9, 20, 9); line(c, 8, 2, 8, 6); line(c, 16, 2, 16, 6); line(c, 8, 13, 16, 13); line(c, 8, 17, 13, 17); break;
            case "library": case "book": rect(c, 3, 4, 9, 21, 1); rect(c, 11, 4, 16, 21, 1); line(c, 18, 5, 22, 20); break;
            case "bell": path(c, 5, 17, 6, 14, 6, 9, 8, 5, 12, 3, 16, 5, 18, 9, 18, 14, 20, 17, 5, 17); c.drawArc(9, 18, 15, 23, 0, 180, false, p); break;
            case "search": c.drawCircle(10.5f, 10.5f, 6.5f, p); line(c, 16, 16, 21, 21); break;
            case "more": for (int x = 5; x < 22; x += 7) c.drawCircle(x, 12, 1, p); break;
            case "arrow": path(c, 10, 5, 17, 12, 10, 19); break;
            case "back": path(c, 15, 5, 8, 12, 15, 19); break;
            case "play": path(c, 8, 4, 21, 12, 8, 20, 8, 4); break;
            case "music": path(c, 9, 18, 9, 5, 21, 3, 21, 16); c.drawOval(3, 16, 9, 21, p); c.drawOval(15, 14, 21, 19, p); break;
            case "compass": c.drawCircle(12, 12, 9, p); path(c, 16, 8, 14, 14, 8, 16, 10, 10, 16, 8); break;
            case "globe": c.drawCircle(12, 12, 9, p); c.drawOval(8, 3, 16, 21, p); line(c, 3, 12, 21, 12); break;
            case "activity": path(c, 2, 13, 7, 13, 10, 5, 14, 20, 17, 10, 22, 10); break;
            case "spark": path(c, 12, 2, 15, 9, 22, 12, 15, 15, 12, 22, 9, 15, 2, 12, 9, 9, 12, 2); break;
            case "smile": c.drawCircle(12, 12, 9, p); c.drawPoint(9, 9, p); c.drawPoint(15, 9, p); c.drawArc(7, 10, 17, 17, 0, 180, false, p); break;
            case "settings": c.drawCircle(12, 12, 7, p); c.drawCircle(12, 12, 2, p); for (int a = 0; a < 8; a++) { c.save(); c.rotate(a * 45, 12, 12); line(c, 12, 2, 12, 5); c.restore(); } break;
            case "share": c.drawCircle(18, 5, 3, p); c.drawCircle(5, 12, 3, p); c.drawCircle(18, 19, 3, p); line(c, 8, 11, 15, 6); line(c, 8, 13, 15, 18); break;
            case "refresh": c.drawArc(4, 4, 20, 20, 35, 290, false, p); path(c, 20, 3, 20, 9, 14, 9); break;
            case "server": rect(c, 3, 3, 21, 10, 2); rect(c, 3, 14, 21, 21, 2); c.drawPoint(7, 6.5f, p); c.drawPoint(7, 17.5f, p); line(c, 13, 6.5f, 17, 6.5f); line(c, 13, 17.5f, 17, 17.5f); break;
            case "mark": p.setStrokeWidth(2.7f); path(c, 2, 5, 6, 19, 12, 8, 18, 19, 22, 5); break;
            default: rect(c, 4, 4, 20, 20, 3); line(c, 8, 9, 16, 9); line(c, 8, 13, 16, 13); line(c, 8, 17, 13, 17);
        }
        c.restore();
    }
    private void rect(Canvas c, float l, float t, float r, float b, float rad) { c.drawRoundRect(new RectF(l, t, r, b), rad, rad, p); }
    private void line(Canvas c, float x, float y, float x2, float y2) { c.drawLine(x, y, x2, y2, p); }
    private void path(Canvas c, float... xy) { Path path = new Path(); path.moveTo(xy[0], xy[1]); for (int i = 2; i < xy.length; i += 2) path.lineTo(xy[i], xy[i + 1]); c.drawPath(path, p); }
    @Override public void setAlpha(int alpha) { p.setAlpha(alpha); }
    @Override public void setColorFilter(ColorFilter filter) { p.setColorFilter(filter); }
    @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
}
