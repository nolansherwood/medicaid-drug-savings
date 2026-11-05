"""The static site must ship every dataset the dashboard page asks for."""
import re

from src import build_site, config

PAGE = config.OUTPUTS / "dashboard" / "index.html"


def test_every_dataset_the_page_reads_is_built():
    page = PAGE.read_text()
    asked = set(re.findall(r"dash\.data\('([a-z0-9_]+)'\)", page))
    asked |= set(re.findall(r"""data-source="([a-z0-9_]+)\"""", page))
    calcs = set(re.findall(r"dash\.calc\('([a-z0-9_]+)'", page))
    # explore_states_N is chosen at runtime: one file per group of 13 states.
    files = {f"explore_states_{n}" for n in range(1, 5)} if "explore_states_" in page else set()
    missing = (asked | files) - calcs - set(build_site.DATASETS)
    assert not missing, f"page reads datasets the site build does not ship: {missing}"


def test_state_file_count_matches_page():
    page = PAGE.read_text()
    m = re.search(r"const STATE_FILES = (\d+);", page)
    from src import explore
    assert m and int(m.group(1)) == explore.STATE_FILES
