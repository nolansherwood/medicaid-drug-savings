"""Shared look: page CSS, number formats, card helpers and the light/dark theme.

The light theme (the default) is .streamlit/config.toml. The dark theme is the
same config with the colors in THEMES["dark"]; the side-menu toggle switches between them
in the browser (sync_theme()). The CSS only covers what the theme can't
(cards, sidebar, the hero number) and reads its colors from --mr-* variables.
"""
import functools
import json
from html import escape

import streamlit as st
import streamlit.components.v1 as components

# Colors per mode. bg/bg2/text/line/sidebar* also go to Streamlit's own theme
# (widgets, tables); the rest are the --mr-* CSS variables and chart colors.
THEMES = {
    "dark": dict(bg="#17181C", bg2="#24262C", text="#E8E9EC", line="#2A2B31", sidebar="#1B1C21",
                 sidebar2="#2A2C33", card="#1F2025", card_alt="#24262C", raised="#2A2C33", tip_line="#3A3C44",
                 strong="#F3F4F6", soft="#C9CBD2", lede="#A9ACB5", muted="#8B8E98", good="#4ADE80", bad="#F87171",
                 shadow="rgba(0,0,0,.45)"),
    "light": dict(bg="#F6F6F3", bg2="#ECECE7", text="#1F2328", line="#E1E1DA", sidebar="#EEEEE9",
                  sidebar2="#E2E2DC", card="#FFFFFF", card_alt="#F2F2EE", raised="#E8E8E2", tip_line="#D4D4CC",
                  strong="#111318", soft="#3B3F47", lede="#545962", muted="#6A6F78", good="#15803D", bad="#DC2626",
                  shadow="rgba(0,0,0,.14)"),
}


def mode() -> str:
    """The current mode, from the side-menu toggle (Home.py)."""
    return "dark" if st.session_state.get("theme_dark") else "light"


def colors() -> dict:
    return THEMES[mode()]

CSS = """
<style>
@import url("https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,500;8..60,600&display=swap");
/* The side menu stays open: hide the collapse and expand controls. */
[data-testid="stSidebarCollapseButton"], [data-testid="stExpandSidebarButton"],
[data-testid="stSidebarCollapsedControl"] { display: none !important; }
[data-testid="stSidebarNavSeparator"] { display: none; }
[data-testid="stSidebarNav"] a { border-radius: 10px; padding: 0.45rem 0.75rem; margin: 2px 0; }
[data-testid="stSidebarNav"] a[aria-current="page"] { background: var(--mr-card-raised); }
[data-testid="stSidebarNav"] header, [data-testid="stNavSectionHeader"] {
  font-size: 0.7rem; letter-spacing: 0.06em; text-transform: uppercase; color: var(--mr-muted);
}

.block-container { padding-top: 2.25rem; max-width: 1200px; }
:root { --mr-serif: "Source Serif 4", "Iowan Old Style", Charter, Georgia, serif; }

/* Page header (page_header()): serif title, muted lede, optional state picker on the right. */
h1, h2, h3, [data-testid="stDialog"] h2 { font-family: var(--mr-serif) !important; }
h1 { letter-spacing: -0.01em; padding: 0 0 0.6rem !important; line-height: 1.15 !important; }
.mr-lede { color: var(--mr-lede-ink); font-size: 0.95rem; line-height: 1.55; max-width: 60rem; margin: 0 0 1.1rem; }
.st-key-page-picker { padding-top: 0.45rem; }
.st-key-mr-tooltips, .st-key-mr-theme-sync { display: none; }

/* Cards: st.container(border=True, key="card-...") blocks; Streamlit owns the padding. */
[class*="st-key-card-"] {
  background: var(--mr-card); border-color: var(--mr-line) !important; border-radius: 14px !important; min-height: 290px;
}
[class*="st-key-card-"] > div:last-child { margin-top: auto; }
.mr-card-title { display: flex; align-items: center; gap: 0.65rem; color: var(--mr-soft); font-size: 0.95rem; font-weight: 500; }
.mr-icon {
  display: inline-grid; place-items: center; width: 30px; height: 30px; border-radius: 8px;
  background: var(--mr-card-raised);
  font-family: 'Material Symbols Rounded'; font-size: 18px; font-weight: 400; line-height: 1;
}
.mr-hero { font-family: var(--mr-serif); font-size: clamp(1.9rem, 2.8vw, 2.7rem); font-weight: 400; line-height: 1.1; margin: 0.9rem 0 0.35rem; color: var(--mr-strong); }
.mr-hero-pre { display: block; font-family: Inter, sans-serif; font-size: 0.95rem; font-weight: 500; color: var(--mr-muted); margin-bottom: 0.15rem; }
.mr-hero-sub { color: var(--mr-soft); font-size: 1.05rem; }
.mr-note { color: var(--mr-muted); font-size: 0.85rem; margin-top: 0.6rem; }
.mr-pill { color: var(--mr-good); font-weight: 500; }

/* "View ›" links sit at the bottom of a card, styled like the reference's "View All". */
[class*="st-key-card-"] [data-testid="stPageLink"] a { padding-left: 0; }
[class*="st-key-card-"] [data-testid="stPageLink"] a p { color: var(--mr-muted); font-size: 0.9rem; }
[class*="st-key-card-"] [data-testid="stPageLink"] a:hover { background: none; }
[class*="st-key-card-"] [data-testid="stPageLink"] a:hover p { color: var(--mr-strong); }
/* Card rows (Overview's three cards, KPI tiles): every card in the row is the same height. */
:is(.st-key-home-cards, .st-key-kpi-row, [class*="st-key-cat-row"]) [data-testid="stColumn"] { display: flex; flex-direction: column; }
:is(.st-key-home-cards, .st-key-kpi-row, [class*="st-key-cat-row"]) [data-testid="stColumn"] > [data-testid="stVerticalBlock"] { flex: 1; }
:is(.st-key-home-cards, .st-key-kpi-row, [class*="st-key-cat-row"]) [data-testid="stColumn"] > [data-testid="stVerticalBlock"] > div { flex: 1; }
:is(.st-key-home-cards, .st-key-kpi-row, [class*="st-key-cat-row"]) [class*="st-key-card-"] { height: 100%; }
/* State map card: a side menu of metrics, styled like the sidebar nav. */
.st-key-card-map { min-height: 0; }
.st-key-card-map [data-testid="stMarkdownContainer"] { margin-bottom: 0; }
.mr-menu-head {
  font-size: 0.7rem; letter-spacing: 0.06em; text-transform: uppercase; color: var(--mr-muted);
  padding: 0.7rem 0 0.2rem 0.7rem; line-height: 1.4;
}
.mr-map-title { font-size: 1.05rem; font-weight: 600; color: var(--mr-strong); }
.st-key-card-map [data-testid="stButton"] button { justify-content: flex-start; padding: 0.35rem 0.7rem; min-height: 0; }
.st-key-card-map [data-testid="stButton"] button > div { justify-content: flex-start; }
.st-key-card-map [data-testid="stButton"] button p { text-align: left; font-size: 0.9rem; white-space: nowrap; }
.st-key-card-map [data-testid="stBaseButton-tertiary"] p { color: var(--mr-soft); }
.st-key-card-map [data-testid="stBaseButton-tertiary"]:hover p { color: var(--mr-strong); }
.st-key-card-map [data-testid="stBaseButton-primary"] { background: var(--mr-card-raised); border-color: var(--mr-card-raised); }
.st-key-card-map [data-testid="stBaseButton-primary"] p { color: var(--mr-strong); font-weight: 500; }
.st-key-card-map [data-testid="stVerticalBlock"] { gap: 0.15rem; }
/* Section headings inside cards, and KPI tiles (the reference's top row). */
.mr-section { font-size: 1.05rem; font-weight: 600; color: var(--mr-strong); }
.mr-subsection { font-size: 0.95rem; font-weight: 600; color: var(--mr-ink); margin-top: 0.5rem; }
[class*="st-key-card-kpi"] { min-height: 0; }
/* KPI values line up across a row even when a sub-line wraps (no bottom-pinning). */
[class*="st-key-card-kpi"] > div:last-child { margin-top: 0; }
[class*="st-key-card-"] [data-testid="stMarkdownContainer"] { margin-bottom: 0; }
.mr-kpi-value { font-family: var(--mr-serif); font-size: 1.9rem; font-weight: 400; line-height: 1.15; color: var(--mr-strong);
  margin: 0.1rem 0 0.15rem; font-variant-numeric: tabular-nums; }
.mr-mix-head { color: var(--mr-soft); font-size: 0.95rem; margin-top: 0.35rem; }
.mr-mix-head b { font-family: var(--mr-serif); font-size: 1.3rem; font-weight: 400; color: var(--mr-strong); }
.mr-legend { display: flex; flex-wrap: wrap; gap: 0.3rem 1rem; color: var(--mr-muted); font-size: 0.8rem; margin-top: 0.6rem; }
.mr-legend span { display: inline-flex; align-items: center; gap: 0.4rem; }
.mr-legend i { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
/* Drug Categories cards */
[class*="st-key-card-cat"] { min-height: 0; }
[class*="st-key-card-cat"] > div:last-child { margin-top: 0; }
.mr-swatch { width: 10px; height: 10px; border-radius: 3px; display: inline-block; margin-right: 0.55rem; vertical-align: 1px; }
.mr-cat-what { color: var(--mr-ink); font-size: 0.92rem; margin-top: 0.5rem; }
.mr-cat-how { color: var(--mr-muted); font-size: 0.85rem; margin-top: 0.5rem; }
.mr-cat-how b { color: var(--mr-soft); font-weight: 600; }
/* Rebates page: the brand-rate calculation as a column of labeled steps. */
.mr-steps { display: grid; gap: 0; margin: 0.4rem 0 0.6rem; max-width: 46rem; }
.mr-step { display: grid; grid-template-columns: 15rem 7rem 1fr; gap: 1rem; align-items: baseline;
  padding: 0.45rem 0; border-bottom: 1px solid var(--mr-line); font-size: 0.92rem; }
.mr-step span { color: var(--mr-soft); }
.mr-step b { font-family: var(--mr-serif); font-weight: 400; font-size: 1.15rem; color: var(--mr-strong); text-align: right;
  font-variant-numeric: tabular-nums; }
.mr-step em { font-style: normal; color: var(--mr-muted); font-size: 0.85rem; }
.mr-step-total { border-bottom: 0; }
.mr-step-total b { font-size: 1.5rem; }
@media (max-width: 640px) { .mr-step { grid-template-columns: 1fr auto; } .mr-step em { grid-column: 1 / -1; } }
.mr-kpi-sub { color: var(--mr-muted); font-size: 0.85rem; }
/* Let Plotly tooltips spill past the chart and card edges instead of clipping. */
[class*="st-key-card-"], [data-testid="stPlotlyChart"], [data-testid="stPlotlyChart"] .js-plotly-plot,
[data-testid="stPlotlyChart"] .plot-container, [data-testid="stPlotlyChart"] .svg-container,
[data-testid="stPlotlyChart"] .main-svg { overflow: visible !important; }
.mr-callout {
  background: var(--mr-card); border: 1px solid var(--mr-line); border-left: 3px solid var(--mr-accent); border-radius: 10px;
  padding: 0.75rem 1rem; color: var(--mr-soft); font-size: 0.92rem; line-height: 1.5; margin-bottom: 0.5rem;
}
.mr-callout-warn { border-left-color: #F59E5B; }

/* Explore Alternatives */
.mr-fact { display: grid; gap: 2px; padding: 0.4rem 0; }
.mr-fact span { color: var(--mr-muted); font-size: 0.82rem; }
.mr-fact b { font-family: var(--mr-serif); color: var(--mr-strong); font-size: 1.35rem; font-weight: 400; font-variant-numeric: tabular-nums; }
.mr-ex-use { color: var(--mr-soft); font-size: 0.88rem; line-height: 1.5; margin: 0.2rem 0 0.4rem; }
.mr-none {
  background: var(--mr-card-alt); border-radius: 10px; padding: 0.75rem 1rem; color: var(--mr-soft); font-size: 0.9rem; line-height: 1.5;
}
.mr-none strong { color: var(--mr-strong); display: block; margin-bottom: 0.3rem; }
.mr-none p { margin: 0.25rem 0 0; }
/* Explore search results: one left-aligned row per product, meta line muted. */
.st-key-ex-results { background: var(--mr-card); border: 1px solid var(--mr-line); border-radius: 10px; padding: 0.35rem; gap: 0; }
.st-key-ex-results [data-testid="stButton"] button { justify-content: flex-start; padding: 0.4rem 0.7rem; }
.st-key-ex-results [data-testid="stButton"] button:hover { background: var(--mr-card-raised); }
.st-key-ex-results [data-testid="stButton"] button > div { justify-content: flex-start; }
.st-key-ex-results [data-testid="stButton"] p { text-align: left; color: var(--mr-muted); font-size: 0.82rem; }
.st-key-ex-results [data-testid="stButton"] p strong { color: var(--mr-strong); font-size: 0.95rem; font-weight: 600; }
</style>
"""


def apply_style() -> None:
    c = colors()
    root = (f":root {{ --mr-card: {c['card']}; --mr-card-alt: {c['card_alt']}; --mr-card-raised: {c['raised']}; "
            f"--mr-line: {c['line']}; --mr-strong: {c['strong']}; --mr-ink: {c['text']}; --mr-soft: {c['soft']}; "
            f"--mr-lede-ink: {c['lede']}; --mr-muted: {c['muted']}; --mr-good: {c['good']}; --mr-accent: #4C8DF6; "
            f"--mr-tip-line: {c['tip_line']}; --mr-shadow: {c['shadow']}; }}")
    st.html(CSS.replace("<style>", "<style>\n" + root, 1))


@functools.lru_cache(maxsize=2)
def _theme_input(m: str) -> str:
    """Streamlit's theme message for mode m, as JSON: the config.toml theme, recolored for light."""
    from google.protobuf.json_format import MessageToDict
    from streamlit.proto.NewSession_pb2 import CustomThemeConfig
    from streamlit.runtime.app_session import _populate_theme_msg

    msg = CustomThemeConfig()
    _populate_theme_msg(msg)
    _populate_theme_msg(msg.sidebar, "theme.sidebar")
    c = THEMES[m]
    msg.base = msg.BaseTheme.LIGHT if m == "light" else msg.BaseTheme.DARK
    msg.background_color, msg.secondary_background_color = c["bg"], c["bg2"]
    msg.text_color, msg.border_color = c["text"], c["line"]
    msg.sidebar.background_color, msg.sidebar.secondary_background_color = c["sidebar"], c["sidebar2"]
    return json.dumps(MessageToDict(msg, use_integers_for_enums=True))


# Streamlit 1.50 can't change theme from Python, so this script, injected into the
# app's window like lib/tooltips.py, applies the mode the toggle asks for. It calls the
# same function Streamlit uses for an imported theme (found on the app's React tree),
# so the switch happens in place and widgets and tables follow. The choice is kept in
# localStorage and restored on the next visit by flipping the toggle.
_THEME_JS = r"""
(() => {
  const W = window, D = W.document;
  if (W.__mrTheme) return;
  W.__mrTheme = true;
  let setter = null, applied = null, restored = false;
  function findSetter() {
    const root = D.getElementById('root');
    const k = root && Object.keys(root).find(k => k.startsWith('__reactContainer'));
    const stack = k ? [root[k]] : [];
    while (stack.length) {
      const f = stack.pop(), props = f.memoizedProps || {};
      for (const key of Object.keys(props)) {
        const v = props[key];
        if (v && typeof v === 'object' && typeof v.setImportedTheme === 'function') return v.setImportedTheme;
      }
      if (f.child) stack.push(f.child);
      if (f.sibling) stack.push(f.sibling);
    }
    return null;
  }
  function tick() {
    const el = D.querySelector('.mr-theme-sync');
    if (!el) return;
    const want = el.dataset.mode;
    let saved = null;
    try { saved = W.localStorage.getItem('mr-theme'); } catch (e) {}
    if (!restored) {
      restored = true;
      if (saved && saved !== want) {
        const box = D.querySelector('.st-key-theme_dark input');
        if (box) { box.click(); return; }
      }
    }
    if (applied === null && want === 'light') { applied = 'light'; return; }  // server theme is already light
    if (want === applied) return;
    setter = setter || findSetter();
    if (!setter) return;
    setter(JSON.parse(el.dataset.input));
    applied = want;
    try { W.localStorage.setItem('mr-theme', want); } catch (e) {}
  }
  W.setInterval(tick, 250);
})();
"""


def sync_theme() -> None:
    """Hand the current mode to the theme script (call once per run, in Home.py)."""
    m = mode()
    with st.container(key="mr-theme-sync"):
        st.html(f'<div class="mr-theme-sync" data-mode="{m}" data-input="{escape(_theme_input(m))}"></div>')
        code = _THEME_JS.replace("</", "<\\/")
        components.html(
            "<script>(() => { const s = window.parent.document.createElement('script');"
            f" s.textContent = {json.dumps(code)}; window.parent.document.head.append(s); }})();</script>",
            height=0,
        )


def usd_words(v: float) -> str:
    """Hero-style amount: $9.1 billion, $640 million, $9.3 million."""
    if v >= 1e9:
        return f"${v / 1e9:,.1f} billion"
    if v >= 1e8:
        return f"${v / 1e6:,.0f} million"
    return f"${v / 1e6:,.1f} million"


def good() -> str:
    """Text color for savings and cheaper prices."""
    return colors()["good"]


def bad() -> str:
    """Text color for losses and dearer prices."""
    return colors()["bad"]


def gain_loss_style(v: str) -> str:
    """Styler color for a formatted figure: red when negative ("−$1.6M"), green otherwise."""
    return "" if v in ("", "—") else f"color: {bad()}" if v.startswith("−") else f"color: {good()}"


def usd_compact(v: float) -> str:
    sign = "−" if v < 0 else ""
    v = abs(v)
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= div:
            return f"{sign}${v / div:,.1f}{suffix}"
    return f"{sign}${v:,.0f}"


def page_header(title: str, lede: str, picker=None) -> None:
    """Every page's header: title and lede, with an optional control (the state
    picker) top-right. One layout so titles sit at the same height on every page."""
    if picker is None:
        st.title(title, anchor=False)
        st.markdown(f'<div class="mr-lede">{escape(lede)}</div>', unsafe_allow_html=True)
        return
    head, side = st.columns([5, 2], gap="large")
    with head:
        st.title(title, anchor=False)
        st.markdown(f'<div class="mr-lede">{escape(lede)}</div>', unsafe_allow_html=True)
    with side, st.container(key="page-picker"):
        picker()


def state_picker(key: str, geo: str, on_change) -> None:
    """The state selectbox in a page header (label hidden; the options name themselves)."""
    from lib import data
    # Set through Session State only (no index=), so it always follows the shared geo
    # without Streamlit's "default value and Session State" warning.
    st.session_state[key] = geo
    st.selectbox("State", data.states(), format_func=lambda c: data.STATE_NAMES.get(c, c),
                 key=key, on_change=on_change, label_visibility="collapsed")


def card_title(icon: str, text: str, color: str) -> None:
    """Card heading with a tinted Material Symbols icon (the font Streamlit already loads)."""
    st.markdown(
        f'<div class="mr-card-title"><span class="mr-icon" style="color:{color}">{icon}</span>'
        f"{escape(text)}</div>",
        unsafe_allow_html=True,
    )


def kpi(icon: str, color: str, label: str, value: str, sub: str) -> None:
    """A KPI tile's contents: icon + label, big value, muted sub-line."""
    card_title(icon, label, color)
    st.markdown(
        f'<div class="mr-kpi-value">{escape(value)}</div><div class="mr-kpi-sub">{escape(sub)}</div>',
        unsafe_allow_html=True,
    )
