/** Parse local generated WebVTT into plain-text cues; never render subtitle HTML. */
export interface ReelCue { start: number; end: number; text: string }
export function parseReelCaptions(vtt: string): ReelCue[] {
 if (!/^WEBVTT(?:\s|$)/.test(vtt.replace(/^\uFEFF/, ''))) throw new Error('The caption file is not valid WebVTT.');
 const time = (s: string) => { const p = s.split(':').map(Number); return p.reduce((n, v) => n * 60 + v, 0); };
 const cues: ReelCue[] = [];
 for (const block of vtt.replace(/\r/g, '').split(/\n\s*\n/)) {
  const lines = block.split('\n');
  const at = lines.findIndex(line => /-->/.test(line));
  if (at < 0) continue;
  const match = lines[at].match(/^\s*((?:\d+:)?\d{2}:\d{2}\.\d{3})\s+-->\s+((?:\d+:)?\d{2}:\d{2}\.\d{3})(?:\s|$)/);
  if (!match) throw new Error('The caption file contains invalid timing.');
  const node = document.createElement('div');
  node.innerHTML = lines.slice(at + 1).join('\n').replace(/<[^>]*>/g, '');
  const text = node.textContent?.trim() ?? '';
  const start = time(match[1]), end = time(match[2]);
  if (!Number.isFinite(start) || end <= start) throw new Error('The caption file contains invalid timing.');
  if (text) cues.push({ start, end, text });
 }
 if (!cues.length) throw new Error('The caption file has no readable subtitles.');
 return cues;
}
