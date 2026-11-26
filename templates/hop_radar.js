/* Hop flavour charts, used by report.html and import.html (inlined at build time by hop_flavour.py).
   HopRadar.inline(profile)        -> radar SVG for the page (theme colours, hover tips)
   HopRadar.makeup(profile)        -> legend + stacked bars: how much each hop adds to each aroma
   HopRadar.card(profile, meta)    -> 1000x1000 SVG image card with both charts (fixed light colours)
   HopRadar.savePng(profile, meta) -> downloads the card as a PNG, for a Brewfather recipe/batch image
   profile comes from hop_flavour.profile(); meta = {title, sub, file}. */
(function () {
  const AROMAS = ['Citrus', 'Resin/Pine', 'Spice', 'Herbal', 'Grassy', 'Floral', 'Berry', 'Stone Fruit', 'Tropical Fruit'];
  const MAX = 5;
  const e = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const f1 = v => (Math.round(v * 10) / 10).toFixed(1);
  const ang = i => (-90 + i * 360 / AROMAS.length) * Math.PI / 180;

  /* Hop colours: the first six categorical slots in their validated order (distinct as neighbours in a
     stacked bar, including for colour-blind viewers), stepped separately for dark mode. "Other" is grey. */
  const HOP_LIGHT = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300'];
  const HOP_DARK = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300'];
  const OTHER = { light: '#b5b3ab', dark: '#5c5b57' };
  const vars = (list, other) => list.map((c, i) => `--hop${i + 1}:${c};`).join('') + `--hop-other:${other};`;
  if (typeof document !== 'undefined' && !document.getElementById('hop-radar-css')) {
    const st = document.createElement('style');
    st.id = 'hop-radar-css';
    st.textContent = `:root{${vars(HOP_LIGHT, OTHER.light)}}
@media (prefers-color-scheme: dark){:root:where(:not([data-theme="light"])){${vars(HOP_DARK, OTHER.dark)}}}
:root[data-theme="dark"]{${vars(HOP_DARK, OTHER.dark)}}
.hr-legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--ink-2);margin:0 0 6px}
.hr-legend span{white-space:nowrap}
.hr-legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
.hr-legend small{color:var(--muted);font-size:11px}`;
    document.head.appendChild(st);
  }
  const pageColor = (part, i) => part.variety === 'Other' ? 'var(--hop-other)' : `var(--hop${i + 1})`;
  const cardColor = (part, i) => part.variety === 'Other' ? OTHER.light : HOP_LIGHT[i];

  /* ---------------------------------------------------------------- radar (the total) */
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

  /* ---------------------------------------------------------------- makeup (stacked bars per aroma) */
  // A bar segment; the last one in a bar gets a 4px rounded data end.
  function segShape(x, y, w, h, last, fill, extra) {
    w = Math.max(0, w);
    if (w < 0.5) return '';
    const r = last ? Math.min(4, w, h / 2) : 0;
    if (!r) return `<rect x="${x.toFixed(1)}" y="${y}" width="${w.toFixed(1)}" height="${h}" fill="${fill}"${extra}/>`;
    const d = `M${x.toFixed(1)},${y}h${(w - r).toFixed(1)}a${r},${r} 0 0 1 ${r},${r}v${h - 2 * r}a${r},${r} 0 0 1 -${r},${r}h-${(w - r).toFixed(1)}z`;
    return `<path d="${d}" fill="${fill}"${extra}/>`;
  }
  function barsBody(p, o) {
    const { x0, LW, W, rowH, barH, fs, col, colorOf, tips } = o;
    const rows = AROMAS.slice().sort((a, b) => (p.aromas[b] || 0) - (p.aromas[a] || 0));
    const bx = x0 + LW;
    let s = '';
    for (let k = 1; k <= MAX; k++) {  // recessive gridlines at 1-5
      const gx = bx + W * k / MAX;
      s += `<line x1="${gx.toFixed(1)}" y1="${o.y0 - 2}" x2="${gx.toFixed(1)}" y2="${o.y0 + rows.length * rowH - (rowH - barH) + 2}" stroke="${col.grid}"/>`;
    }
    rows.forEach((c, r) => {
      const y = o.y0 + r * rowH;
      s += `<text x="${bx - 8}" y="${(y + barH / 2 + fs * 0.35).toFixed(1)}" text-anchor="end" font-size="${fs}" fill="${col.ink2}">${e(c)}</text>`;
      const parts = p.parts.map((pt, i) => ({ pt, i, v: pt.aromas[c] || 0 })).filter(x => x.v > 0.005);
      let x = bx;
      parts.forEach((q, j) => {
        const full = W * q.v / MAX;
        const last = j === parts.length - 1;
        const w = last ? full : full - 2;  // 2px surface gap between segments
        const tip = tips ? ` data-tip="${e(`<b>${e(q.pt.variety)}</b><br>${e(c)}: ${f1(q.v)} of ${f1(p.aromas[c])} (${Math.round(q.v / p.aromas[c] * 100)}%)<br><span style='color:var(--muted)'>${q.pt.g} g, ${q.pt.share}% of the flavour hops</span>`)}"` : '';
        s += segShape(x, y, w, barH, last, colorOf(q.pt, q.i), tip);
        x += full;
      });
      s += `<text x="${(x + 6).toFixed(1)}" y="${(y + barH / 2 + fs * 0.35).toFixed(1)}" font-size="${fs}" fill="${col.ink}" font-weight="600">${f1(p.aromas[c] || 0)}</text>`;
    });
    if (o.axis) {
      const ay = o.y0 + rows.length * rowH - (rowH - barH) + fs + 6;
      for (let k = 0; k <= MAX; k++) s += `<text x="${(bx + W * k / MAX).toFixed(1)}" y="${ay}" text-anchor="middle" font-size="${fs - 1}" fill="${col.muted}">${k}</text>`;
    }
    return s;
  }
  function legendHtml(p) {
    return '<div class="hr-legend">' + p.parts.map((pt, i) =>
      `<span${pt.others ? ` data-tip="${e('Other: ' + pt.others.join(', '))}"` : ''}><i style="background:${pageColor(pt, i)}"></i>${e(pt.variety)} <small>${pt.g} g · ${pt.share}%${pt.estimate ? ' · est.' : ''}</small></span>`).join('') + '</div>';
  }
  function makeup(p) {
    if (!p || !p.parts || !p.parts.length) return '';
    const H = 9 * 22 - 8 + 28;
    return legendHtml(p) + `<svg viewBox="0 0 420 ${H}" width="100%" style="max-width:560px;display:block" role="img" aria-label="Hop flavour makeup: each hop's share of every aroma">` +
      barsBody(p, { x0: 0, y0: 4, LW: 96, W: 280, rowH: 22, barH: 14, fs: 11, col: PAGE, colorOf: pageColor, tips: true, axis: true }) + '</svg>';
  }

  const PAGE = { grid: 'var(--grid)', accent: 'var(--accent)', surface: 'var(--surface)', ink: 'var(--ink)', ink2: 'var(--ink-2)', muted: 'var(--muted)' };
  const LIGHT = { grid: '#e1e0d9', accent: '#2a78d6', surface: '#fcfcfb', ink: '#0b0b0b', ink2: '#52514e', muted: '#6f6d68' };
  const FONT = "'Segoe UI', Helvetica, Arial, sans-serif";

  function inline(p) {
    if (!p) return '<p class="note">No flavour data for these hops.</p>';
    return `<svg viewBox="0 0 400 300" width="100%" style="max-width:420px;display:block" role="img" aria-label="Hop flavour radar">` +
      body(p, { cx: 200, cy: 150, R: 100, fs: 11, col: PAGE, tips: true, stroke: 2, dot: 4, gap: 10 }) + '</svg>';
  }

  function card(p, meta) {
    const c = LIGHT;
    let s = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000" width="1000" height="1000" font-family="${FONT}">` +
      `<rect width="1000" height="1000" fill="${c.surface}"/>` +
      `<text x="60" y="92" font-size="46" font-weight="700" fill="${c.ink}">${e(meta.title)}</text>` +
      `<text x="60" y="134" font-size="24" fill="${c.ink2}">${e(meta.sub || 'Hop flavour profile')}</text>`;
    s += body(p, { cx: 500, cy: 372, R: 160, fs: 22, col: c, tips: false, stroke: 4, dot: 7, gap: 22 });
    if (p.parts && p.parts.length) {
      s += barsBody(p, { x0: 60, y0: 612, LW: 190, W: 600, rowH: 28, barH: 18, fs: 19, col: c, colorOf: cardColor, tips: false, axis: false });
      // legend: swatch + name + grams, wrapped across the width
      let x = 60, y = 908;
      p.parts.forEach((pt, i) => {
        const label = `${pt.variety} ${pt.g} g`;
        const w = 26 + label.length * 10.5 + 22;
        if (x + w > 950) { x = 60; y += 30; }
        s += `<rect x="${x}" y="${y - 15}" width="16" height="16" rx="4" fill="${cardColor(pt, i)}"/><text x="${x + 24}" y="${y}" font-size="19" fill="${c.ink}">${e(label)}</text>`;
        x += w;
      });
    }
    s += `<text x="60" y="972" font-size="16" fill="${c.muted}">Aroma 0-5 from HopDatabase supplier data, weighted by grams of late boil (x0.5), whirlpool and dry hops.${p.covered < 100 ? ' Covers ' + p.covered + '% of those hops.' : ''}</text>`;
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

  window.HopRadar = { AROMAS, inline, makeup, card, savePng, hopList };
})();
