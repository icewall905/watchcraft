// Small popup opened from the right-click menu: name a new board, then add the clicked image to it.
const params = new URLSearchParams(location.search);
const job = { srcUrl: params.get('src') || '', pageUrl: params.get('page') || '', frameId: Number(params.get('frame') || 0),
  tabId: Number(params.get('tab') || -1), tabTitle: params.get('title') || '' };
const $ = (id) => document.getElementById(id);
$('preview').onerror = () => { $('preview').hidden = true; };
if (/^(https?:|data:image\/)/i.test(job.srcUrl)) $('preview').src = job.srcUrl; else $('preview').hidden = true;
$('name').focus();
$('cancel').addEventListener('click', () => window.close());
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') window.close(); });
$('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = $('name').value.trim();
  if (!name) return;
  $('save').disabled = true; $('status').textContent = '';
  try {
    const res = await chrome.runtime.sendMessage({ type: 'new-board', name, interests: $('interests').value.trim(), curate: $('curate').checked, job });
    if (res && res.ok) return window.close();
    $('status').textContent = (res && res.error) || 'Something went wrong.';
  } catch (err) {
    $('status').textContent = err.message || String(err);
  }
  $('save').disabled = false;
});
