# Retired: static-site dashboard

The dashboard was first published as a claude.ai dashboard and as a static
site (pharmasavings.nolansherwood.com, Cloudflare Pages). Both were replaced
by the Streamlit app in `app/` (decision D-023 in docs/decisions.md). The
files are kept here for reference only. They are not maintained, and their
tests are not run.

- `index.html`: the dashboard page (was `outputs/dashboard/index.html`). The
  Streamlit pages took their logic and wording from it.
- `site_src/`: the site shell, CSS and the small runtime that loaded the CSVs.
- `build_site.py`, `test_build_site.py`: the build script (was
  `src/build_site.py`) and its tests. Paths in them point to the old
  locations, so they will not run from here without changes.
- `site/`: the last built site (not in git).
