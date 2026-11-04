/* Hop flavour radar, shared by report.html and next_brew.html (inlined at build time by hop_flavour.py).
   HopRadar.inline(profile)            -> SVG string for the page (theme colours, hover tips)
   HopRadar.card(profile, meta)        -> 1000x1000 SVG image card (fixed light colours)
   HopRadar.savePng(profile, meta)     -> downloads the card as a PNG, for a Brewfather recipe/batch image
   profile comes from hop_flavour.profile(); meta = {title, sub, file}. */
(function () {
  const AROMAS = ['Citrus', 'Resin/Pine', 'Spice', 'Herbal', 'Grassy', 'Floral', 'Berry', 'Stone Fruit', 'Tropical Fruit'];
  const MAX = 5;
  const e = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const f1 = v => (Math.round(v * 10) / 10).toFixed(1);
  const ang = i => (-90 + i * 360 / AROMAS.length) * Math.PI / 180;

  function body(p, o) {
    const { cx, cy, R, fs, col, tips } = o;
    const pt = (i, v) => [cx + Math.cos(ang(i)) * R * v / MAX, cy + Math.sin(ang(i)) * R * v / MAX];
    let s = '';
    for (let k = 1; k <= MAX; k++) {
      s += `<polygon points="${AROMAS.map((_, i) => pt(i, k).map(n => n.toFixed(1)).join(',')).join(' ')}" fill="none" stroke="${col.grid}" stroke-width="${k === MAX ? 1.5 : 1}"/>`;
    }
    AROMAS.forEach((_, i) => { const [x, y] = pt(i, MAX); s += `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="${col.grid}"/>`; });
    const vals = AROMAS.map(c => Math.max(0, Math.min(MAX, p.aromas[c] || 0)));
    const poly = vals.map((v, i) => pt(i, v).map(n => n.toFixed(1)).join(',')).join(' ');
    s += `<polygon points="${poly}" fill="${col.accent}" fill-opacity="0.2" stroke="${col.accent}" stroke-width="${o.stroke}" stroke-linejoin="round"/>`;
    const top = new Set(p.top.slice(0, 2));
    AROMAS.forEach((c, i) => {
      const [x, y] = pt(i, vals[i]);
      s += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${o.dot}" fill="${col.accent}" stroke="${col.surface}" stroke-width="${o.dot / 2}"/>`;
      const cos = Math.cos(ang(i)), sin = Math.sin(ang(i));
      const lx = cx + cos * (R + o.gap), ly = cy + sin * (R + o.gap);
      const anchor = Math.abs(cos) < 0.2 ? 'middle' : cos > 0 ? 'start' : 'end';
      const dy = sin < -0.5 ? -fs * 0.3 : sin > 0.5 ? fs * 0.9 : fs * 0.35;
      s += `<text x="${lx.toFixed(1)}" y="${(ly + dy).toFixed(1)}" text-anchor="${anchor}" font-size="${fs}" fill="${top.has(c) ? col.ink : col.muted}"${top.has(c) ? ' font-weight="600"' : ''}>${e(c)}${top.has(c) ? ' ' + f1(vals[i]) : ''}</text>`;
      if (tips) {
        const who = (p.contrib[c] || []).map(([v, n]) => `${e(v)} ${f1(n)}`).join('<br>') || 'none';
        const tip = `<b>${e(c)}: ${f1(vals[i])} / 5</b><br><span style='color:var(--muted)'>Biggest contributors</span><br>${who}`;
        s += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="14" fill="transparent" data-tip="${e(tip)}"/>`;
      }
    });
    return s;
  }

  const PAGE = { grid: 'var(--grid)', accent: 'var(--accent)', surface: 'var(--surface)', ink: 'var(--ink)', muted: 'var(--muted)' };
  const LIGHT = { grid: '#e1e0d9', accent: '#2a78d6', surface: '#fcfcfb', ink: '#0b0b0b', muted: '#6f6d68' };
  const FONT = "'Segoe UI', Helvetica, Arial, sans-serif";

  function inline(p) {
    if (!p) return '<p class="note">No flavour data for these hops.</p>';
    return `<svg viewBox="0 0 400 300" width="100%" style="max-width:420px;display:block" role="img" aria-label="Hop flavour radar">` +
      body(p, { cx: 200, cy: 150, R: 100, fs: 11, col: PAGE, tips: true, stroke: 2, dot: 4, gap: 10 }) + '</svg>';
  }

  function wrap(text, max) {
    const out = []; let line = '';
    text.split(' · ').forEach(w => { if (line && (line + ' · ' + w).length > max) { out.push(line); line = w; } else line = line ? line + ' · ' + w : w; });
    if (line) out.push(line);
    return out;
  }

  function card(p, meta) {
    const c = LIGHT;
    let s = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000" width="1000" height="1000" font-family="${FONT}">` +
      `<rect width="1000" height="1000" fill="${c.surface}"/>` +
      `<text x="60" y="92" font-size="46" font-weight="700" fill="${c.ink}">${e(meta.title)}</text>` +
      `<text x="60" y="134" font-size="24" fill="#52514e">${e(meta.sub || 'Hop flavour profile')}</text>`;
    s += body(p, { cx: 500, cy: 500, R: 255, fs: 25, col: c, tips: false, stroke: 4, dot: 8, gap: 26 });
    const hops = p.hops.filter(h => h.g > 0).map(h => `${h.variety} ${h.g} g`).join(' · ');
    wrap(hops, 62).slice(0, 2).forEach((l, i) => { s += `<text x="60" y="${870 + i * 34}" font-size="24" fill="${c.ink}">${i ? '' : '<tspan font-weight="600">Flavour hops </tspan>'}${e(l)}</text>`; });
    s += `<text x="60" y="958" font-size="17" fill="${c.muted}">Aroma 0-5 from HopDatabase supplier data, weighted by grams of late boil (x0.5), whirlpool and dry hops.${p.covered < 100 ? ' Covers ' + p.covered + '% of those hops.' : ''}</text>`;
    return s + '</svg>';
  }

  function savePng(p, meta) {
    const img = new Image();
    img.onload = () => {
      const cv = document.createElement('canvas'); cv.width = 1000; cv.height = 1000;
      cv.getContext('2d').drawImage(img, 0, 0);
      cv.toBlob(b => {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(b);
        a.download = (meta.file || meta.title || 'hop-flavour').replace(/[\\/:*?"<>|#]+/g, '').trim() + ' - hop flavour.png';
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      }, 'image/png');
    };
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(card(p, meta));
  }

  function hopList(p) {
    if (!p) return '';
    return '<table><thead><tr><th>Flavour hop</th><th class="n">g</th><th class="n">Share</th></tr></thead><tbody>' +
      p.hops.map(h => `<tr><td>${e(h.variety)}${h.estimate ? ' <span class="muted" data-tip="Estimated in hop_flavours_extra.json (not supplier data)">est.</span>' : ''}${h.known ? '' : ' <span class="muted">no data</span>'}</td><td class="n">${h.g}</td><td class="n">${h.known ? h.share + '%' : '–'}</td></tr>`).join('') +
      '</tbody></table>';
  }

  window.HopRadar = { AROMAS, inline, card, savePng, hopList };
})();
