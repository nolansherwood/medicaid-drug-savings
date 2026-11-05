"""HTML tooltips for Plotly charts, laid out like the original dashboard's:
bold title, muted subtitle, label/value rows with right-aligned values, and a
muted footnote line.

Plotly's own hover labels are plain text runs (no columns or padding), so a
chart opts in by setting hoverinfo="none" and putting a tooltip spec in each
point's customdata (see spec()). install() adds a small script to the page
that listens for Plotly hover events and draws the tooltip as fixed-position
HTML, so it can also extend past the chart and card edges. The same script
stops maps from being dragged (Streamlit overrides dragmode=False).

Streamlit 1.50 (the last release for Python 3.9) has no two-way custom
component API, so the script is injected into the parent page from a
zero-height components.html frame. The script lives in the parent window,
which keeps it working after Streamlit re-renders a chart or changes page.
"""
import json

import streamlit as st
import streamlit.components.v1 as components

_JS = r"""
(() => {
  // Runs in the app's window (install() injects it there), not the helper frame.
  // window.parent would be wrong here: on Community Cloud the app is itself framed.
  const W = window, D = W.document;
  if (W.__mrTips) return;
  W.__mrTips = true;
  const css = D.createElement('style');
  css.textContent = `
    .mr-tip { position: fixed; z-index: 100000; pointer-events: none; background: var(--mr-card-raised);
      border: 1px solid var(--mr-tip-line); border-radius: 10px; box-shadow: 0 8px 24px var(--mr-shadow);
      padding: 8px 12px; font: 13px/1.45 Inter, sans-serif; color: var(--mr-ink); max-width: 300px;
      display: grid; gap: 2px; font-variant-numeric: tabular-nums; }
    .mr-tip[hidden] { display: none; }
    .mr-tip-title { font-weight: 600; }
    .mr-tip-sub { color: var(--mr-lede-ink); margin-bottom: 6px; }
    .mr-tip-row { display: flex; justify-content: space-between; gap: 24px; }
    .mr-tip-row span:first-child { color: var(--mr-lede-ink); }
    .mr-tip-split { color: var(--mr-lede-ink); margin-top: 6px; white-space: normal; }`;
  D.head.append(css);
  const tip = D.createElement('div'); tip.className = 'mr-tip'; tip.hidden = true;
  D.body.append(tip);

  const div = (cls, text) => { const e = D.createElement('div'); e.className = cls; e.textContent = text; return e; };
  function draw(spec) {
    const kids = [div('mr-tip-title', spec.t)];
    if (spec.s) kids.push(div('mr-tip-sub', spec.s));
    for (const [label, value] of spec.r || []) {
      const row = D.createElement('div'); row.className = 'mr-tip-row';
      const a = D.createElement('span'); a.textContent = label;
      const b = D.createElement('span'); b.textContent = value;
      row.append(a, b); kids.push(row);
    }
    if (spec.f) kids.push(div('mr-tip-split', spec.f));
    tip.replaceChildren(...kids);
  }
  function place(ev) {
    // Right of and below the pointer; flip or clamp to stay 8px inside the window.
    const tw = tip.offsetWidth, th = tip.offsetHeight, edge = 8;
    let left = ev.clientX + 14, top = ev.clientY + 12;
    if (left + tw > W.innerWidth - edge) left = Math.max(edge, ev.clientX - tw - 14);
    if (top + th > W.innerHeight - edge) top = Math.max(edge, W.innerHeight - th - edge);
    tip.style.left = left + 'px'; tip.style.top = top + 'px';
  }
  function attach(gd) {
    if (gd.__mrTip || typeof gd.on !== 'function') return;
    gd.__mrTip = true;
    gd.on('plotly_hover', (e) => {
      const pt = e.points && e.points[0];
      const cd = pt && pt.customdata;
      if (typeof cd !== 'string' || !cd.startsWith('{"mrtip"')) return;
      draw(JSON.parse(cd).mrtip);
      tip.hidden = false;
      if (e.event) place(e.event);
    });
    gd.on('plotly_unhover', () => { tip.hidden = true; });
    gd.addEventListener('mousemove', (ev) => { if (!tip.hidden) place(ev); });
    gd.addEventListener('mouseleave', () => { tip.hidden = true; });
  }
  W.setInterval(() => D.querySelectorAll('.js-plotly-plot').forEach(attach), 400);

  // Maps don't pan. Streamlit replaces layout.dragmode=false with "pan" when a chart has
  // on_select, so drags that start on a geo chart are stopped before Plotly sees them
  // (window capture phase runs first). Clicks still select a state.
  let lock = false;
  const onMap = (ev) => { const gd = ev.target.closest && ev.target.closest('.js-plotly-plot');
                          return !!(gd && gd._fullLayout && gd._fullLayout.geo); };
  W.addEventListener('mousedown', (ev) => { lock = onMap(ev); }, true);
  W.addEventListener('touchstart', (ev) => { lock = onMap(ev); }, true);
  const block = (ev) => { if (lock && (ev.type !== 'mousemove' || ev.buttons)) ev.stopImmediatePropagation(); };
  W.addEventListener('mousemove', block, true);
  W.addEventListener('touchmove', block, true);
  W.addEventListener('mouseup', () => { lock = false; }, true);
  W.addEventListener('touchend', () => { lock = false; }, true);
})();
"""


def spec(title: str, sub: str = "", rows=(), foot: str = "") -> str:
    """One point's tooltip, as the customdata string the script looks for."""
    return json.dumps({"mrtip": {"t": title, "s": sub, "r": [list(r) for r in rows], "f": foot}})


def install() -> None:
    """Add the tooltip script to the page (once per browser tab)."""
    code = _JS.replace("</", "<\\/")
    # The frame sits in a hidden container (ui.py) so it adds no gap to the page;
    # a hidden frame still runs its script.
    with st.container(key="mr-tooltips"):
        components.html(
            "<script>(() => { const s = window.parent.document.createElement('script');"
            f" s.textContent = {json.dumps(code)}; window.parent.document.head.append(s); }})();</script>",
            height=0,
        )
