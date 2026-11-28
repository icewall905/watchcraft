import { inAndroidApp } from './navigation';

/** Human-readable name for this client, sent as device_label on sign-in. */
export function deviceLabel(): string {
	if (typeof navigator === 'undefined') return 'Browser';
	const ua = navigator.userAgent;
	if (inAndroidApp()) return /TV|AFT|Leanback/i.test(ua) ? 'Android TV app' : 'Android app';
	if (/Tizen|Web0S|WebOS|SMART-TV|SmartTV|AFT|BRAVIA|Leanback|Android TV|CrKey|HbbTV/i.test(ua)) return 'TV browser';
	const browser = /Edg\//.test(ua) ? 'Edge' : /OPR\/|Opera/.test(ua) ? 'Opera' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\/|CriOS/.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'Browser';
	const os = /Android/.test(ua) ? 'Android' : /iPhone|iPad|iPod/.test(ua) ? 'iOS' : /Windows/.test(ua) ? 'Windows' : /Mac OS X|Macintosh/.test(ua) ? 'macOS' : /CrOS/.test(ua) ? 'ChromeOS' : /Linux/.test(ua) ? 'Linux' : '';
	return os ? `${browser} on ${os}` : browser;
}
