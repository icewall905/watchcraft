class Palette {
	open = $state(false);
	show() { this.open = true; }
	hide() { this.open = false; }
}
export const palette = new Palette();
