# Running and publishing the dashboard

The dashboard is a Streamlit app (`app/`) with Plotly charts, hosted on
Streamlit Community Cloud (D-023). It reads the CSV files in
`outputs/dashboard/` that the pipeline writes, and does no aggregation of its
own. The old static site is retired; see `archive/static_site/README.md`.

## Run it locally

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app/Home.py          # http://localhost:8501
```

The theme (light by default, serif headings) is in `.streamlit/config.toml`.
The rest of the styling is CSS in `app/lib/ui.py`, which reads its colors
from `ui.THEMES`. The side-menu "Dark mode" toggle switches to the dark colors
there: Streamlit 1.50 can't change theme from Python, so a small script in
`ui.sync_theme()` applies it in the browser and remembers the choice
(localStorage). It uses Streamlit internals (the function behind "import
theme"), so check the toggle after any Streamlit upgrade.

App layout:

| File | What it is |
|---|---|
| `app/Home.py` | Entry point: page setup, side menu, logo |
| `app/views/home.py` | Overview: savings hero, region picker, product search, state map |
| `app/views/rankings.py` | KPI tiles, category table, top 25 bar charts, products popup |
| `app/views/savings.py` | Savings KPI tiles by switch type, top 25 brands (strengths added up) |
| `app/views/explore.py` | Product search, strengths and forms (collapsed), alternatives and FDA reasons |
| `app/views/rebates.py` | MACPAC rebate calibration and timing caveat, break-even rebate for top brands, price before and after rebates |
| `app/views/categories.py` | Drug Categories: what each of the eight product types means, the order of FDA and CMS sources, edge cases |
| `app/lib/data.py` | Cached loaders for `outputs/dashboard/*.csv` |
| `app/lib/charts.py` | Plotly figures (state map, ranked bars) and map metrics |
| `app/lib/tooltips.py` | HTML tooltips for the bar charts, styled like the original dashboard's |
| `app/lib/ui.py` | CSS, number formats, card and header helpers |

The selected state is shared by every page (`st.session_state.geo`), so
picking Texas on one page shows Texas on the others.

## Tests

```bash
.venv/bin/python -m pytest -q
```

`tests/test_app.py` runs each page headlessly (Streamlit AppTest), nationally
and for a state. It checks that pages render, that the home search and the
region picker work, and that Explore shows the expected strengths for Humira
and Eliquis. It also pins the 2025 figures shown on the old dashboard
($9.1B gross savings, $111.9B spending, 80.2% generic share and others).
After a data refresh those pinned figures will change: check the new values,
then update them in the test.

The tests skip when `outputs/dashboard/` has not been built. Hover tooltips
are JavaScript and are not covered; check them in a browser.

## Publish on Streamlit Community Cloud

Community Cloud deploys from a GitHub repository and redeploys on every push.

1. **GitHub.** The project is public at
   `github.com/nolansherwood/medicaid-drug-savings`, branch `main`.
2. **The app's data is committed.** `outputs/` is in `.gitignore` except
   `outputs/dashboard/` (`!outputs/dashboard/`): the app needs only those CSV
   files (about 49 MB, 19 files; the largest is 9.8 MB, under GitHub's 100 MB
   limit). `data/` stays out of git; the app never reads it.
3. **The app** at share.streamlit.io → Create app → from GitHub:
   - Repository and branch: `nolansherwood/medicaid-drug-savings`, `main`.
   - Main file path: `app/Home.py`.
   - Advanced settings → Python version: 3.12. Community Cloud installs
     `app/requirements.txt` (next to the entry point), which pins Streamlit
     1.50.0, Plotly and pandas to the versions tested locally on Python 3.9.
     Change those pins only after testing the app and the light/dark toggle
     on the new versions.
4. **Custom address.** Community Cloud gives an address like
   `<name>.streamlit.app`, and it cannot be served on your own domain. To
   keep `pharmasavings.nolansherwood.com` working, add a Cloudflare redirect
   rule (Rules → Redirect rules) from that host to the `streamlit.app`
   address, and link to it from the portfolio.

A Community Cloud app goes to sleep after several days without visitors. The
next visitor sees a "wake up" button and waits about a minute.

## Refreshing the data (new quarter or year)

Run the pipeline in this order:

```bash
Y=2025
.venv/bin/python -m src.ingest_sdud --year $Y
.venv/bin/python -m src.reference_data          # FDA, Purple Book, MDRP, Orange Book
.venv/bin/python -m src.crosswalk               # RxNav (cached; new NDCs only)
.venv/bin/python -m src.product_type --year $Y
.venv/bin/python -m src.indications             # needs the openFDA label download (see src/indications.py)
.venv/bin/python -m src.rebates
.venv/bin/python -m src.equivalence --year $Y
.venv/bin/python -m src.savings --year $Y
.venv/bin/python -m src.export_dashboard --year $Y
.venv/bin/python -m src.explore --year $Y
.venv/bin/python -m pytest -q
```

For a new year, also change `YEAR` in `app/lib/data.py`. Then commit the
updated `outputs/dashboard/` files and push; Community Cloud redeploys, and
the app's data cache starts fresh.

Disclaimer shown in the side menu: built from CMS State Drug Utilization Data
and FDA records; spending is before manufacturer rebates.
