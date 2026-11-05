"""Build the standalone static website in site/ from the dashboard (Cloudflare Pages ready).

The dashboard page (outputs/dashboard/index.html, the same file published to
the claude.ai dashboard) is wrapped in a site shell (site_src/shell.html) and
runs on a small stand-in for the dashboard runtime (site_src/dash.js). All
paths are relative, so the folder works at a domain root, a subdomain, or a
sub-path of an existing site.

  site/
    index.html            shell + dashboard page
    assets/               site.css, dash.js, manifest.js, d3 (no external requests)
    data/                 the dashboard CSVs (loaded on demand by the page)
    _headers              Cloudflare Pages cache headers

Run after the pipeline and src.explore:
    python -m src.build_site --year 2025
Preview locally:
    python -m http.server --directory site 8000
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
import sys
from datetime import datetime, timezone

from src import config

SRC = config.ROOT / "site_src"
OUT = config.ROOT / "site"
DASH_DIR = config.OUTPUTS / "dashboard"
MAX_FILE_BYTES = 25 * 1024 * 1024        # Cloudflare Pages per-file limit

# Dataset id (as the page asks for it) -> CSV written by the pipeline.
DATASETS = {
    "summary": "category_summary_{year}.csv",
    "rankings": "rankings_{year}.csv",
    "products_us": "products_us_{year}.csv",
    "products_states": "products_states_{year}.csv",
    "indications": "indications_{year}.csv",
    "product_ndcs": "product_ndcs_{year}.csv",
    "savings_top": "savings_top_{year}.csv",
    "savings_summary": "savings_summary_{year}.csv",
    "savings_ndcs": "savings_ndcs_{year}.csv",
    "explore_index": "explore_index_{year}.csv",
    "explore_groups": "explore_groups_{year}.csv",
    "explore_status": "explore_status_{year}.csv",
    "explore_states_1": "explore_states_1_{year}.csv",
    "explore_states_2": "explore_states_2_{year}.csv",
    "explore_states_3": "explore_states_3_{year}.csv",
    "explore_states_4": "explore_states_4_{year}.csv",
}
DEFAULT_PARAMS = {"category": "All product types", "state": "US", "savtier": "all"}

HEADERS = """/data/*
  Cache-Control: public, max-age=3600
  Access-Control-Allow-Origin: *
/assets/*
  Cache-Control: public, max-age=31536000, immutable
/*
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
"""


def build(year: int) -> None:
    cfg = json.loads((SRC / "site_config.json").read_text())
    page = (DASH_DIR / "index.html").read_text()
    now = datetime.now(timezone.utc)
    build_id = now.strftime("%Y%m%d%H%M")

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "assets").mkdir(parents=True)
    (OUT / "data").mkdir()

    datasets = {}
    for ds_id, pattern in DATASETS.items():
        src = DASH_DIR / pattern.format(year=year)
        if not src.exists():
            raise SystemExit(f"Missing {src.name}; run the pipeline (export_dashboard, explore) first.")
        if src.stat().st_size > MAX_FILE_BYTES:
            raise SystemExit(f"{src.name} is over Cloudflare's 25 MiB per-file limit.")
        shutil.copy2(src, OUT / "data" / src.name)
        datasets[ds_id] = {"file": f"data/{src.name}"}

    manifest = {"datasets": datasets, "params": DEFAULT_PARAMS, "built": now.isoformat(), "year": year}
    (OUT / "assets" / "manifest.js").write_text("window.DASH_MANIFEST = " + json.dumps(manifest, indent=1) + ";\n")
    for f in ("site.css", "dash.js"):
        shutil.copy2(SRC / f, OUT / "assets" / f)
    shutil.copy2(SRC / "vendor" / "d3.v7.9.0.min.js", OUT / "assets" / "d3.v7.9.0.min.js")

    esc = lambda v: html.escape(str(v), quote=True)  # noqa: E731
    shell = (SRC / "shell.html").read_text()
    out = (shell.replace("{{SITE_TITLE}}", esc(cfg["site_title"]))
                .replace("{{DESCRIPTION}}", esc(cfg["description"]))
                .replace("{{SITE_URL}}", esc(cfg.get("site_url", "").rstrip("/")))
                .replace("{{HOME_URL}}", esc(cfg["home_url"]))
                .replace("{{HOME_LABEL}}", esc(cfg["home_label"]))
                .replace("{{AUTHOR_LINE}}", esc(cfg.get("author_line", "")))
                .replace("{{YEAR}}", str(year))
                .replace("{{BUILD_DATE}}", now.strftime("%B %-d, %Y"))
                .replace("{{BUILD_ID}}", build_id)
                .replace("{{PAGE}}", page))
    (OUT / "index.html").write_text(out)
    (OUT / "_headers").write_text(HEADERS)

    total = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    n = sum(1 for p in OUT.rglob("*") if p.is_file())
    print(f"  site/ built: {n} files, {total / 1e6:.1f} MB (largest file "
          f"{max(p.stat().st_size for p in OUT.rglob('*') if p.is_file()) / 1e6:.1f} MB)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args(argv)
    build(args.year)
    return 0


if __name__ == "__main__":
    sys.exit(main())
