/*
 * Minimal stand-in for the claude.ai dashboard runtime, so the dashboard page
 * (outputs/dashboard/index.html) runs as a plain static site.
 *
 * Provides the subset of the `dash` API the page uses:
 *   dash.data(id)        -> {status, data, message}; first call starts loading the CSV
 *   dash.onData(fn)      -> fn runs now and whenever data, filters or the page width change
 *   dash.calc(id, spec)  -> derived dataset from other datasets
 *   dash.params(), dash.setParams(obj), dash.resetParams()   (kept in the URL query)
 *   dash.setLink(name)   -> sets the URL #hash (tabs)
 *   dash.colors          -> series colors from the CSS tokens (--series-1 ...)
 *
 * Datasets and default filters come from window.DASH_MANIFEST (manifest.js,
 * written by src/build_site.py). CSV cells arrive as text, as on the dashboard.
 */
(function () {
  "use strict";
  const manifest = window.DASH_MANIFEST || { datasets: {}, params: {} };
  const store = {};
  const calcs = {};
  const listeners = [];
  let params = Object.assign({}, manifest.params, readUrl());

  function readUrl() {
    const out = {};
    new URLSearchParams(location.search).forEach((v, k) => { out[k] = v; });
    return out;
  }
  function writeUrl() {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v != null && v !== "" && v !== manifest.params[k]) q.set(k, v);
    }
    const qs = q.toString();
    history.replaceState(null, "", (qs ? "?" + qs : location.pathname) + location.hash);
  }

  let pending = false;
  function schedule() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      for (const fn of listeners) {
        try { fn(); } catch (e) { console.error("dashboard draw failed:", e); }
      }
    });
  }

  function load(id) {
    if (store[id]) return;
    const ds = manifest.datasets[id];
    if (!ds) { store[id] = { status: "missing", data: [], message: `No dataset "${id}"` }; return; }
    store[id] = { status: "loading", data: [] };
    fetch(ds.file)
      .then((r) => { if (!r.ok) throw new Error(`${r.status} ${r.statusText} for ${ds.file}`); return r.text(); })
      .then((text) => { store[id] = { status: "ok", data: d3.csvParse(text) }; schedule(); })
      .catch((e) => { store[id] = { status: "error", data: [], message: String(e) }; console.error(e); schedule(); });
  }

  function data(id) {
    if (calcs[id]) return runCalc(id);
    load(id);
    const s = store[id];
    return { status: s.status, data: s.data, rows: s.data, message: s.message, refreshing: false, meta: [] };
  }

  function runCalc(id) {
    const c = calcs[id];
    const inputs = c.inputs.map((i) => data(i));
    const bad = inputs.find((d) => d.status !== "ok");
    if (bad) return { status: bad.status === "loading" ? "loading" : "error", data: [], message: bad.message };
    const key = inputs.map((d) => d.data);
    if (!c.memo || c.memo.key.some((k, i) => k !== key[i])) {
      try { c.memo = { key, value: c.fn(...key) }; }
      catch (e) { console.error(`calc ${id} failed:`, e); return { status: "error", data: [], message: String(e) }; }
    }
    return { status: "ok", data: c.memo.value, rows: c.memo.value, meta: [] };
  }

  let lastWidth = window.innerWidth, resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (window.innerWidth !== lastWidth) { lastWidth = window.innerWidth; schedule(); }
    }, 120);
  });
  // Redraw when the viewer switches between light and dark (chart colors).
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", schedule);
  // ...and when the site's light/dark toggle is used (see shell.html).
  window.addEventListener("themechange", schedule);

  const dash = {
    data,
    onData(fn) {
      listeners.push(fn);
      try { fn(); } catch (e) { console.error("dashboard draw failed:", e); }
    },
    calc(id, spec) { calcs[id] = { inputs: spec.inputs, fn: spec.fn, memo: null }; },
    loader() { /* not used by static files */ },
    params() { return Object.assign({}, params); },
    setParams(obj) {
      for (const [k, v] of Object.entries(obj || {})) params[k] = v;
      writeUrl();
      schedule();
    },
    resetParams() { params = Object.assign({}, manifest.params); writeUrl(); schedule(); },
    setLink(name) { history.replaceState(null, "", location.pathname + location.search + (name ? "#" + name : "")); },
    refresh() { /* static data */ },
  };
  Object.defineProperty(dash, "colors", {
    get() {
      const css = getComputedStyle(document.documentElement);
      return [1, 2, 3, 4, 5, 6, 7, 8].map((i) => css.getPropertyValue(`--series-${i}`).trim() || "#888");
    },
  });
  window.dash = dash;
})();
