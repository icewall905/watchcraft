// Presentation belongs to this browser, so changing a desktop never changes the TV.
class DeviceMode {
	mode = $state<'desktop' | 'tv'>('desktop');
	init() {
		try { this.mode = localStorage.getItem('wc.deviceMode') === 'tv' ? 'tv' : 'desktop'; } catch { /* Storage may be unavailable. */ }
	}
	set(mode: 'desktop' | 'tv') {
		this.mode = mode;
		try { localStorage.setItem('wc.deviceMode', mode); } catch { /* Keep the session preference. */ }
	}
}

export const deviceMode = new DeviceMode();
