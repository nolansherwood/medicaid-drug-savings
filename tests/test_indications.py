"""Tests for FDA label indication extraction (src.indications)."""
import pytest

from src import indications as ind


@pytest.mark.parametrize("raw,expected", [
    ("1 INDICATIONS AND USAGE OZEMPIC is indicated ( 1.1 ) for adults.", "OZEMPIC is indicated for adults."),
    ("INDICATIONS & USAGE  Treats pain.", "Treats pain."),
    ("Uses temporarily relieves minor aches", "temporarily relieves minor aches"),
])
def test_clean(raw, expected):
    assert ind.clean(raw) == expected


def test_dedupe_drops_restated_sentences_and_dangling_header():
    text = ("INVEGA SUSTENNA is indicated for the treatment of schizophrenia in adults. "
            "INVEGA SUSTENNA is indicated for treatment of schizophrenia in adults. "
            "INVEGA SUSTENNA is an atypical antipsychotic indicated:")
    out = ind.dedupe_restatements(text)
    assert out == "INVEGA SUSTENNA is indicated for the treatment of schizophrenia in adults."


def test_dedupe_keeps_sentences_with_new_information():
    # Adds the drug class, so it is not a pure restatement.
    text = ("INVEGA SUSTENNA is indicated for the treatment of schizophrenia in adults. "
            "INVEGA SUSTENNA is an atypical antipsychotic indicated for treatment of schizophrenia in adults.")
    assert "atypical antipsychotic" in ind.dedupe_restatements(text)


def test_dedupe_keeps_distinct_indications():
    text = "X is indicated: • for type 2 diabetes. • to reduce cardiovascular risk in adults."
    assert "cardiovascular" in ind.dedupe_restatements(text)


@pytest.mark.parametrize("panel,expected", [
    ("PRINCIPAL DISPLAY PANEL NDC 0169-4130-13 Ozempic", {"001694130"}),
    ("Pen NDC 0169- 4525 -14 List 452514", {"001694525"}),
    ("NDC: 12345-678-90 and NDC 00002-1433-80", {"123450678", "000021433"}),
    ("Lot 12345-6789", set()),                         # not preceded by NDC
])
def test_printed_ndcs(panel, expected):
    assert ind.printed_product9s({"package_label_principal_display_panel": [panel]}) == expected


def test_product9():
    assert ind.product9("0169-4130") == "001694130"
    assert ind.product9("12345-678") == "123450678"
    assert ind.product9("bad") is None
