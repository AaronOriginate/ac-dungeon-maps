/* Map switcher: replaces the "All maps" link with Maps / previous / next controls, adds a pager at the foot of the panel,
   and a searchable list of every map (M to open, [ and ] to step, Esc to close). Data comes from window.MAPNAV above. */
(function () {
  const NAV = window.MAPNAV; if (!NAV) return;
  const crumb = document.querySelector('.crumb'); if (!crumb) return;
  const maps = NAV.maps;
  const here = decodeURIComponent(location.pathname.split('/').pop() || '').replace(/\.html$/, '');
  const i = maps.findIndex(m => m.slug === here);
  const cur = maps[i] || null;
  const prev = i > 0 ? maps[i - 1] : null, next = i >= 0 && i < maps.length - 1 ? maps[i + 1] : null;
  const href = m => encodeURIComponent(m.slug) + '.html';
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const inGroup = cur ? maps.filter(m => m.group === cur.group) : [];
  const short = cur ? ((NAV.short || {})[cur.group] || cur.group) : '';
  const pos = cur ? short + ' · ' + (inGroup.indexOf(cur) + 1) + ' of ' + inGroup.length : 'Dungeon maps';
  const posFull = cur ? cur.group + ', map ' + (inGroup.indexOf(cur) + 1) + ' of ' + inGroup.length : '';

  const nav = document.createElement('nav');
  nav.className = 'mnav'; nav.setAttribute('aria-label', 'Maps');
  nav.innerHTML =
    '<button class="btn" type="button" id="mOpen" aria-haspopup="dialog">' +
      '<svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1l5 7-5 7-5-7z" fill="currentColor"/></svg>Maps<kbd>M</kbd></button>' +
    '<span class="where" title="' + esc(posFull) + '">' + esc(pos) + '</span>' +
    '<a class="btn sq" rel="prev" ' + (prev ? 'href="' + href(prev) + '" title="Previous: ' + esc(prev.title) + ' ([)" aria-label="Previous map: ' + esc(prev.title) + '"' : 'aria-disabled="true" aria-label="No previous map"') + '>‹</a>' +
    '<a class="btn sq" rel="next" ' + (next ? 'href="' + href(next) + '" title="Next: ' + esc(next.title) + ' (])" aria-label="Next map: ' + esc(next.title) + '"' : 'aria-disabled="true" aria-label="No next map"') + '>›</a>';
  crumb.replaceWith(nav);

  const panel = document.querySelector('aside.panel');
  if (panel && (prev || next)) {
    const pg = document.createElement('nav'); pg.className = 'mpager'; pg.setAttribute('aria-label', 'Previous and next map');
    pg.innerHTML = (prev ? '<a href="' + href(prev) + '"><small>Previous</small><b>' + esc(prev.title) + '</b></a>' : '') +
                   (next ? '<a class="next" href="' + href(next) + '"><small>Next</small><b>' + esc(next.title) + '</b></a>' : '');
    panel.appendChild(pg);
  }

  // ---- dialog
  let back = null, lastFocus = null, kb = -1;
  function items() { return back ? [...back.querySelectorAll('a.it:not([hidden])')] : []; }
  function mark(n) {
    const it = items(); it.forEach(a => a.classList.remove('kb'));
    kb = Math.max(0, Math.min(n, it.length - 1));
    if (it[kb]) { it[kb].classList.add('kb'); it[kb].scrollIntoView({ block: 'nearest' }); }
  }
  function build() {
    back = document.createElement('div'); back.className = 'mdlg-back'; back.hidden = true;
    let html = '<div class="mdlg" role="dialog" aria-modal="true" aria-labelledby="mTitle">' +
      '<div class="head"><h2 class="gilt" id="mTitle">Dungeon maps</h2><button class="btn x" type="button" id="mClose" aria-label="Close">×</button></div>' +
      '<input class="field" id="mQ" type="search" placeholder="Search maps, monsters, items" aria-label="Search maps" autocomplete="off">' +
      '<div class="list" id="mList">';
    NAV.groups.forEach(g => {
      const ms = maps.filter(m => m.group === g); if (!ms.length) return;
      html += '<div class="grp" data-g="' + esc(g) + '">' + esc(g) + '</div>';
      ms.forEach(m => {
        html += '<a class="it" href="' + href(m) + '" data-q="' + esc(m.search || (m.title + ' ' + m.sub + ' ' + m.group).toLowerCase()) + '" data-g="' + esc(g) + '"' +
          (cur && m.slug === cur.slug ? ' aria-current="page"' : '') + '><b>' + esc(m.title) + '</b><span>' + esc(m.sub) + '</span></a>';
      });
    });
    html += '<p class="none" id="mNone" hidden>No maps match that search.</p></div>' +
      '<div class="foot"><a href="index.html">All maps index</a><span class="keys"><kbd>↑</kbd><kbd>↓</kbd> move · <kbd>Enter</kbd> open · <kbd>Esc</kbd> close</span></div></div>';
    back.innerHTML = html;
    document.body.appendChild(back);
    const q = back.querySelector('#mQ');
    q.addEventListener('input', () => {
      const t = q.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      let shown = 0;
      back.querySelectorAll('a.it').forEach(a => { const ok = t.every(w => a.dataset.q.includes(w)); a.hidden = !ok; shown += ok; });
      back.querySelectorAll('.grp').forEach(h => { h.hidden = !back.querySelector('a.it[data-g="' + CSS.escape(h.dataset.g) + '"]:not([hidden])'); });
      back.querySelector('#mNone').hidden = shown > 0;
      mark(0);
    });
    back.addEventListener('click', e => { if (e.target === back) close(); });
    back.querySelector('#mClose').addEventListener('click', close);
    back.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown') { e.preventDefault(); mark(kb + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); mark(kb - 1); }
      else if (e.key === 'Enter' && document.activeElement === q) { const a = items()[kb]; if (a) { e.preventDefault(); location.href = a.href; } }
      else if (e.key === 'Tab') {   // keep focus inside the dialog
        const f = [back.querySelector('#mClose'), q, ...items(), back.querySelector('.foot a')];
        const at = f.indexOf(document.activeElement);
        if (e.shiftKey && at <= 0) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && at === f.length - 1) { e.preventDefault(); f[0].focus(); }
      }
    });
  }
  function open() {
    if (!back) build();
    lastFocus = document.activeElement; back.hidden = false;
    const q = back.querySelector('#mQ'); q.value = ''; q.dispatchEvent(new Event('input'));
    const c = back.querySelector('a.it[aria-current]'); if (c) { mark(items().indexOf(c)); }
    q.focus();
  }
  function close() { if (!back || back.hidden) return; back.hidden = true; if (lastFocus) lastFocus.focus(); }
  document.getElementById('mOpen').addEventListener('click', open);
  addEventListener('keydown', e => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName || '');
    if (e.key === 'Escape') { close(); return; }
    if (typing || (back && !back.hidden)) return;
    if (e.key === 'm' || e.key === 'M' || e.key === '/') { e.preventDefault(); open(); }
    else if (e.key === '[' && prev) location.href = href(prev);
    else if (e.key === ']' && next) location.href = href(next);
  });
})();
