/* Brewfather Reports service worker: makes the site installable as an app.
   It caches nothing. When the server isn't running, page loads show a short note instead of the browser's error. */
const PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Brewfather Reports</title><style>
:root { color-scheme: light dark; --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --border: rgba(11,11,11,.1); }
@media (prefers-color-scheme: dark) { :root { --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --border: rgba(255,255,255,.12); } }
body { margin: 0; background: var(--page); color: var(--ink); font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 520px; margin: 12vh auto 0; padding: 0 16px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 20px; }
h1 { font-size: 18px; margin: 0 0 8px; } p { color: var(--ink-2); margin: 0 0 12px; }
button { font: inherit; color: var(--ink); background: var(--page); border: 1px solid var(--border); border-radius: 6px; padding: 7px 14px; cursor: pointer; }
</style></head><body><main><div class="card"><h1>Brewfather Reports isn't running</h1>
<p>Start it from the <b>Brewfather Reports</b> shortcut in the Start Menu or on your desktop (or run <code>start.bat</code> / <code>start.sh</code>), then try again.</p>
<button onclick="location.reload()">Try again</button></div></main></body></html>`;

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  if (e.request.mode !== 'navigate') return;
  e.respondWith(fetch(e.request).catch(() =>
    new Response(PAGE, { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } })));
});
