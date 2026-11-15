// WatchCraft browser extension: right-click an image → WatchCraft → Add to <board>.
// Works in Chrome (service worker) and Firefox (event page); uses the promise-based chrome.* API.
const MENU_ROOT = 'wc-root';

async function settings() {
  const { server = '', key = '' } = await chrome.storage.local.get(['server', 'key']);
  return { server: server.replace(/\/+$/, ''), key };
}

async function api(path, init = {}) {
  const { server, key } = await settings();
  if (!server || !key) throw new Error('Connect the extension to WatchCraft first (extension options).');
  const res = await fetch(`${server}/api/ext/v1${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}`, ...(init.headers || {}) },
  });
  let body = null;
  try { body = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const detail = body && typeof body.detail === 'string' ? body.detail : `WatchCraft answered ${res.status}.`;
    const err = new Error(detail); err.status = res.status; throw err;
  }
  return body;
}

async function rebuildMenus() {
  await chrome.contextMenus.removeAll();
  chrome.contextMenus.create({ id: MENU_ROOT, title: 'WatchCraft', contexts: ['image'] });
  let boards = [], problem = '';
  try { boards = (await api('/boards')).boards; } catch (e) { problem = e.message; }
  if (problem) {
    chrome.contextMenus.create({ id: 'wc-setup', parentId: MENU_ROOT, title: 'Connect to WatchCraft…', contexts: ['image'] });
  } else if (!boards.length) {
    chrome.contextMenus.create({ id: 'wc-none', parentId: MENU_ROOT, title: 'No mood boards yet', enabled: false, contexts: ['image'] });
  } else {
    for (const b of boards.slice(0, 60)) {
      chrome.contextMenus.create({ id: `wc-board-${b.id}`, parentId: MENU_ROOT, title: `Add to ${b.name.replace(/&/g, '&&')}`, contexts: ['image'] });
    }
  }
  chrome.contextMenus.create({ id: 'wc-sep', parentId: MENU_ROOT, type: 'separator', contexts: ['image'] });
  chrome.contextMenus.create({ id: 'wc-refresh', parentId: MENU_ROOT, title: 'Refresh board list', contexts: ['image'] });
  chrome.contextMenus.create({ id: 'wc-open', parentId: MENU_ROOT, title: 'Open Mood Boards', contexts: ['image'] });
}

// Runs inside the page when WatchCraft cannot download the image itself (logins, hotlink protection).
async function grabFromPage(src) {
  const toBase64 = (blob) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
  try {
    const res = await fetch(src, { credentials: 'include' });
    if (res.ok) { const blob = await res.blob(); if (blob.type.startsWith('image/')) return { data: await toBase64(blob) }; }
  } catch { /* try the rendered image next */ }
  const img = [...document.images].find((i) => i.currentSrc === src || i.src === src);
  if (img && img.complete && img.naturalWidth) {
    try {
      const canvas = document.createElement('canvas');
      canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
      canvas.getContext('2d').drawImage(img, 0, 0);
      const blob = await new Promise((r) => canvas.toBlob(r, 'image/png'));
      if (blob) return { data: await toBase64(blob) };
    } catch { /* cross-origin canvas: nothing more to try */ }
  }
  return { error: 'The page did not let the image be copied.' };
}

async function flash(ok, title, message) {
  try {
    await chrome.action.setBadgeBackgroundColor({ color: ok ? '#22a06b' : '#d64545' });
    await chrome.action.setBadgeText({ text: ok ? '✓' : '!' });
    setTimeout(() => chrome.action.setBadgeText({ text: '' }), 4000);
  } catch { /* badge unsupported */ }
  try {
    await chrome.notifications.create({ type: 'basic', iconUrl: chrome.runtime.getURL('icons/icon-128.png'), title, message });
  } catch { /* notifications blocked */ }
}

async function addImage(boardId, boardTitle, info, tab) {
  const src = info.srcUrl || '';
  const title = (tab && tab.title) ? tab.title.slice(0, 300) : '';
  const page = info.pageUrl || (tab && tab.url) || '';
  const pageUrl = /^https?:/i.test(page) ? page : undefined;
  try {
    let body;
    if (src.startsWith('data:image/')) {
      body = { data_base64: src.split(',')[1], title, page_url: pageUrl };
    } else {
      try {
        return report(await api(`/boards/${boardId}/images`, { method: 'POST', body: JSON.stringify({ url: src, title, page_url: pageUrl }) }));
      } catch (e) {
        if (e.status !== 422) throw e;
        // WatchCraft could not fetch it; copy it from the page the user is looking at.
        const [result] = await chrome.scripting.executeScript({
          target: { tabId: tab.id, frameIds: [info.frameId || 0] }, func: grabFromPage, args: [src],
        });
        if (!result || !result.result || !result.result.data) throw new Error((result && result.result && result.result.error) || 'Could not copy the image from this page.');
        body = { data_base64: result.result.data, source_url: src, title, page_url: pageUrl };
      }
    }
    report(await api(`/boards/${boardId}/images`, { method: 'POST', body: JSON.stringify(body) }));
  } catch (e) {
    await flash(false, 'Could not add image', e.message || String(e));
  }
  function report(result) { return flash(true, `Added to ${result.board || boardTitle}`, result.title || 'Image saved to your mood board.'); }
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const id = String(info.menuItemId);
  if (id === 'wc-setup') return chrome.runtime.openOptionsPage();
  if (id === 'wc-refresh') return rebuildMenus();
  if (id === 'wc-open') { const { server } = await settings(); if (server) chrome.tabs.create({ url: `${server}/boards` }); else chrome.runtime.openOptionsPage(); return; }
  if (id.startsWith('wc-board-')) await addImage(Number(id.slice(9)), '', info, tab);
});

chrome.runtime.onInstalled.addListener((details) => { rebuildMenus(); if (details.reason === 'install') chrome.runtime.openOptionsPage(); });
chrome.runtime.onStartup.addListener(rebuildMenus);
chrome.storage.onChanged.addListener((changes) => { if (changes.server || changes.key) rebuildMenus(); });
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
chrome.alarms.create('wc-refresh', { periodInMinutes: 30 });
chrome.alarms.onAlarm.addListener((a) => { if (a.name === 'wc-refresh') rebuildMenus(); });
chrome.runtime.onMessage.addListener((msg) => { if (msg === 'rebuild') rebuildMenus(); });
