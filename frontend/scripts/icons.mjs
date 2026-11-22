// Generates web and Android icons from brand/hub_logo.png (run: npm run icons). Output is committed.
import sharp from 'sharp';
import { mkdir } from 'node:fs/promises';

const root = new URL('../../', import.meta.url);
const src = new URL('brand/hub_logo.png', root).pathname;
const path = (p) => new URL(p, root).pathname;
const WEB_BG = '#0a0817';

const logo = (size) => sharp(src).resize(size, size, { kernel: 'lanczos3' }).png({ compressionLevel: 9 });

// Logo centred on a canvas; scale = logo diameter / canvas size.
async function padded(size, scale, background) {
	const inner = Math.round(size * scale);
	const off = Math.round((size - inner) / 2);
	return sharp({ create: { width: size, height: size, channels: 4, background } })
		.composite([{ input: await logo(inner).toBuffer(), top: off, left: off }])
		.png({ compressionLevel: 9 });
}

const clear = { r: 0, g: 0, b: 0, alpha: 0 };
const jobs = [
	[logo(512), 'frontend/static/logo.png'],
	[logo(64), 'frontend/static/favicon.png'],
	[logo(192), 'frontend/static/icons/icon-192.png'],
	[logo(512), 'frontend/static/icons/icon-512.png'],
	[await padded(180, 0.94, WEB_BG), 'frontend/static/icons/apple-touch-icon.png'],
	[await padded(512, 0.8, WEB_BG), 'frontend/static/icons/maskable-512.png']
];
// Android: launcher foreground on a 108dp canvas with the disc inside the 66dp safe zone;
// in-app header badge full bleed.
for (const [dpi, f] of Object.entries({ mdpi: 1, hdpi: 1.5, xhdpi: 2, xxhdpi: 3, xxxhdpi: 4 })) {
	jobs.push([await padded(108 * f, 66 / 108, clear), `android/app/src/main/res/mipmap-${dpi}/ic_launcher_foreground.png`]);
	jobs.push([logo(108 * f), `android/app/src/main/res/drawable-${dpi}/ic_badge.png`]);
}
await mkdir(path('frontend/static/icons/'), { recursive: true });
await Promise.all(jobs.map(([img, out]) => img.toFile(path(out))));
console.log(`${jobs.length} icons written`);
