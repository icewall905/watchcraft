// The Hub browser extension: right-click an image → The Hub → Add to <board> (or New board…).
// Works in Chrome (service worker) and Firefox (event page); uses the promise-based chrome.* API.
const MENU_ROOT = 'wc-root';

async function settings() {
  const { server = '', key = '' } = await chrome.storage.local.get(['server', 'key']);
  return { server: server.replace(/\/+$/, ''), key };
}

async function api(path, init = {}) {
  const { server, key } = await settings();
  if (!server || !key) throw new Error('Connect the extension to The Hub first (extension options).');
  const res = await fetch(`${server}/api/ext/v1${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}`, ...(init.headers || {}) },
  });
  let body = null;
  try { body = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const detail = body && typeof body.detail === 'string' ? body.detail : `The Hub answered ${res.status}.`;
    const err = new Error(detail); err.status = res.status; throw err;
  }
  return body;
}

async function rebuildMenus() {
  await chrome.contextMenus.removeAll();
  chrome.contextMenus.create({ id: MENU_ROOT, title: 'The Hub', contexts: ['image'] });
  let boards = [], problem = '';
  try { boards = (await api('/boards')).boards; } catch (e) { problem = e.message; }
  if (problem) {
    chrome.contextMenus.create({ id: 'wc-setup', parentId: MENU_ROOT, title: 'Connect to The Hub…', contexts: ['image'] });
  } else if (!boards.length) {
    chrome.contextMenus.create({ id: 'wc-none', parentId: MENU_ROOT, title: 'No mood boards yet', enabled: false, contexts: ['image'] });
  } else {
    for (const b of boards.slice(0, 60)) {
      chrome.contextMenus.create({ id: `wc-board-${b.id}`, parentId: MENU_ROOT, title: `Add to ${b.name.replace(/&/g, '&&')}`, contexts: ['image'] });
    }
  }
  if (!problem) chrome.contextMenus.create({ id: 'wc-new', parentId: MENU_ROOT, title: 'New board…', contexts: ['image'] });
  chrome.contextMenus.create({ id: 'wc-sep', parentId: MENU_ROOT, type: 'separator', contexts: ['image'] });
  chrome.contextMenus.create({ id: 'wc-refresh', parentId: MENU_ROOT, title: 'Refresh board list', contexts: ['image'] });
  chrome.contextMenus.create({ id: 'wc-open', parentId: MENU_ROOT, title: 'Open Mood Boards', contexts: ['image'] });
}

// Runs inside the page when The Hub cannot download the image itself (logins, hotlink protection).
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

// Sends the clicked image to a board; throws on failure. Returns the server's answer ({board, title}).
async function saveImage(boardId, info, tab) {
  const src = info.srcUrl || '';
  const title = (tab && tab.title) ? tab.title.slice(0, 300) : '';
  const page = info.pageUrl || (tab && tab.url) || '';
  const pageUrl = /^https?:/i.test(page) ? page : undefined;
  let body;
  if (src.startsWith('data:image/')) {
    body = { data_base64: src.split(',')[1], title, page_url: pageUrl };
  } else {
    try {
      return await api(`/boards/${boardId}/images`, { method: 'POST', body: JSON.stringify({ url: src, title, page_url: pageUrl }) });
    } catch (e) {
      if (e.status !== 422) throw e;
      // The Hub could not fetch it; copy it from the page the user is looking at.
      const [result] = await chrome.scripting.executeScript({
        target: { tabId: tab.id, frameIds: [info.frameId || 0] }, func: grabFromPage, args: [src],
      });
      if (!result || !result.result || !result.result.data) throw new Error((result && result.result && result.result.error) || 'Could not copy the image from this page.');
      body = { data_base64: result.result.data, source_url: src, title, page_url: pageUrl };
    }
  }
  return api(`/boards/${boardId}/images`, { method: 'POST', body: JSON.stringify(body) });
}

async function addImage(boardId, boardTitle, info, tab) {
  try {
    const result = await saveImage(boardId, info, tab);
    await flash(true, `Added to ${result.board || boardTitle}`, result.title || 'Image saved to your mood board.');
  } catch (e) {
    await flash(false, 'Could not add image', e.message || String(e));
  }
}

// Opens the small "New mood board" window for the image the user right-clicked.
async function openNewBoard(info, tab) {
  const q = new URLSearchParams({ src: info.srcUrl || '', page: info.pageUrl || (tab && tab.url) || '',
    frame: String(info.frameId || 0), tab: String(tab ? tab.id : -1), title: (tab && tab.title) || '' });
  const url = chrome.runtime.getURL(`newboard.html?${q}`);
  try { await chrome.windows.create({ url, type: 'popup', width: 460, height: 540 }); }
  catch { await chrome.tabs.create({ url }); }
}

// Creates (or reuses, same name) the board, refreshes the menu, then adds the image. Answers the dialog.
async function newBoard(msg) {
  const job = msg.job || {};
  let board;
  try {
    board = (await api('/boards', { method: 'POST', body: JSON.stringify({ name: msg.name, interests: msg.interests || '', curate: !!msg.curate }) })).board;
  } catch (e) {
    return { ok: false, error: e.message || String(e) };
  }
  rebuildMenus();
  const info = { srcUrl: job.srcUrl, pageUrl: job.pageUrl, frameId: job.frameId };
  const tab = { id: job.tabId, title: job.tabTitle, url: job.pageUrl };
  try {
    const result = await saveImage(board.id, info, tab);
    await flash(true, `Added to ${result.board || board.name}`, result.title || 'Image saved to your new mood board.');
    return { ok: true };
  } catch (e) {
    return { ok: false, error: `Board “${board.name}” is ready, but the image could not be added: ${e.message || e}` };
  }
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const id = String(info.menuItemId);
  if (id === 'wc-setup') return chrome.runtime.openOptionsPage();
  if (id === 'wc-refresh') return rebuildMenus();
  if (id === 'wc-open') { const { server } = await settings(); if (server) chrome.tabs.create({ url: `${server}/boards` }); else chrome.runtime.openOptionsPage(); return; }
  if (id === 'wc-new') return openNewBoard(info, tab);
  if (id.startsWith('wc-board-')) await addImage(Number(id.slice(9)), '', info, tab);
});

chrome.runtime.onInstalled.addListener((details) => { rebuildMenus(); if (details.reason === 'install') chrome.runtime.openOptionsPage(); });
chrome.runtime.onStartup.addListener(rebuildMenus);
chrome.storage.onChanged.addListener((changes) => { if (changes.server || changes.key) rebuildMenus(); });
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
chrome.alarms.create('wc-refresh', { periodInMinutes: 30 });
chrome.alarms.onAlarm.addListener((a) => { if (a.name === 'wc-refresh') rebuildMenus(); });
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg === 'rebuild') { rebuildMenus(); return false; }
  if (msg && msg.type === 'new-board' && sender.id === chrome.runtime.id) { newBoard(msg).then(sendResponse); return true; }
  return false;
});
