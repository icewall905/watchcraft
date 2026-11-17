const $ = (id) => document.getElementById(id);
const status = (text, cls = '') => { $('status').textContent = text; $('status').className = cls; };

chrome.storage.local.get(['server', 'key']).then(({ server = '', key = '' }) => { $('server').value = server; $('key').value = key; if (server && key) check(server, key); });

async function check(server, key) {
  $('boards').replaceChildren();
  try {
    const res = await fetch(`${server}/api/ext/v1/boards`, { headers: { Authorization: `Bearer ${key}` } });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `The Hub answered ${res.status}.`);
    status(`Connected${body.profile ? ` as ${body.profile}` : ''}: ${body.boards.length} board${body.boards.length === 1 ? '' : 's'}.`, 'ok');
    for (const b of body.boards) { const li = document.createElement('li'); li.textContent = `${b.name} (${b.pin_count})`; $('boards').append(li); }
  } catch (e) { status(`Not connected: ${e.message}`, 'bad'); }
}

$('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  let server;
  try { const u = new URL($('server').value.trim()); server = u.origin + u.pathname.replace(/\/+$/, ''); } catch { return status('Enter the full address, starting with https://', 'bad'); }
  const key = $('key').value.trim();
  if (!key.startsWith('wcx_')) return status('The key starts with wcx_. Create one in The Hub.', 'bad');
  // Ask only for this one site; the extension has no other host access.
  const granted = await chrome.permissions.request({ origins: [`${new URL(server).origin}/*`] }).catch(() => false);
  if (!granted) return status('Permission to reach your Hub address was not granted.', 'bad');
  await chrome.storage.local.set({ server, key });
  chrome.runtime.sendMessage('rebuild').catch(() => {});
  status('Saved. Checking…');
  check(server, key);
});
