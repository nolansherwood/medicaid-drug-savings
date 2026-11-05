"""Drug categories: how each NDC is classified as brand, generic, biologic and so
on (docs/decisions.md, classification hierarchy and D-001 to D-019). Text only,
plus national 2025 shares and the largest products in each category."""
import html

import streamlit as st

from lib import charts, data, ui

ui.page_header(
    "Drug Categories",
    "Every drug in the data is a package code (NDC). Each one is put in one of eight categories by how FDA "
    "approved it, never by its name. This page explains each category and how it is decided.",
)

# Category -> (color group, what it is, how it is decided). Colors match the Rankings mix card.
BRAND, REF, GEN, BIOSIM, OTHER = charts.MIX_COLORS
CATEGORIES = {
    "Small molecules": (
        "Chemical drugs, usually pills, inhalers and injections made by chemical synthesis. FDA approves the "
        "original under a New Drug Application (NDA) and copies under an Abbreviated NDA (ANDA).",
        [
            ("Brand, single source", BRAND,
             "A brand-name drug with no generic of the same strength and form dispensed to Medicaid in 2025.",
             "FDA approval is an NDA, and no A-rated generic or authorized generic with the same ingredient, "
             "strength and form had Medicaid prescriptions that year."),
            ("Brand, generic competition", BRAND,
             "A brand-name drug where a generic of the same strength and form was dispensed to Medicaid in 2025.",
             "FDA approval is an NDA, and an A-rated generic or authorized generic with the same ingredient, "
             "strength and form had Medicaid prescriptions that year. This is based on what was dispensed, not "
             "on the category manufacturers report to CMS, which is often out of date."),
            ("Authorized generic", GEN,
             "The brand manufacturer's own drug, sold under a generic label, often by a partner company.",
             'FDA lists the package as "NDA authorized generic": it is sold under the brand\'s NDA, not an ANDA.'),
            ("Generic", GEN,
             "A copy of a brand drug that FDA approved as having the same active ingredient, strength, form and "
             "effect.",
             "FDA approval is an ANDA. Generics sold under a brand-like name (branded generics) stay in this "
             "category and are flagged."),
        ],
    ),
    "Biologics": (
        "Drugs made from living cells, such as antibodies, insulins and vaccines. FDA licenses them under a "
        "Biologics License Application (BLA). Copies are never identical, so they are called biosimilars, not "
        "generics.",
        [
            ("Reference biologic", REF,
             "An original biologic, the one biosimilars are compared with.",
             "A BLA licensed under section 351(a). Insulins, somatropin and some other proteins were approved as "
             "drugs but moved to biologic status in March 2020, so they count here even if older records call "
             "them NDAs."),
            ("Biosimilar", BIOSIM,
             "A copy of a reference biologic with no meaningful difference in safety or effect.",
             "A BLA licensed under section 351(k) that FDA has not designated interchangeable. Switching to it "
             "needs a new prescription."),
            ("Interchangeable biosimilar", BIOSIM,
             "A biosimilar that a pharmacy may substitute for the reference biologic without a new prescription, "
             "where state law allows.",
             "A 351(k) BLA that the FDA Purple Book lists as interchangeable."),
        ],
    ),
    "Other": (
        "",
        [
            ("OTC / unapproved / unclassified", OTHER,
             "Over-the-counter products, drugs marketed without FDA approval, and products no source could place.",
             "FDA lists the package as an OTC monograph product or as unapproved, or neither FDA nor CMS has a "
             "record of it."),
        ],
    ),
}

def pct(v: float) -> str:
    return "<0.1%" if 0 < v < 0.0005 else f"{v:.1%}"


cs = data.category_summary()
cs = cs[cs.state == data.NATIONAL].set_index("product_type")
prods = data.products(data.NATIONAL).sort_values("medicaid_amount", ascending=False)
top = prods.groupby("product_type")["product"].apply(lambda s: list(dict.fromkeys(s))[:3])

st.markdown(
    '<div class="mr-note" style="margin-top:0">Shares are national, 2025, before rebates. '
    '"Largest" lists the products with the highest Medicaid spending in each category.</div>',
    unsafe_allow_html=True,
)
for family, (intro, cats) in CATEGORIES.items():
    st.subheader(family, anchor=False)
    if intro:
        st.markdown(f'<div class="mr-lede">{html.escape(intro)}</div>', unsafe_allow_html=True)
    for i, (name, color, what, how) in enumerate(cats):
        if i % 2 == 0:   # one row of columns per pair, so the two cards in a row are the same height
            cols = st.container(key=f"cat-row-{family[:3]}-{i}").columns(2, gap="medium")
        row = cs.loc[name] if name in cs.index else None
        share = (f"{pct(row.share_prescriptions)} of prescriptions · {pct(row.share_spending)} of spending"
                 if row is not None else "")
        examples = ", ".join(top.get(name, []))
        with cols[i % 2], st.container(border=True, key=f"card-cat-{family[:3]}-{i}"):
            st.markdown(
                f'<div class="mr-section"><span class="mr-swatch" style="background:{color}"></span>'
                f"{html.escape(name)}</div>"
                f'<div class="mr-cat-what">{html.escape(what)}</div>'
                f'<div class="mr-cat-how"><b>How we know.</b> {html.escape(how)}</div>'
                + (f'<div class="mr-note">{share}<br>Largest: {html.escape(examples)}</div>' if share else ""),
                unsafe_allow_html=True,
            )

# ---- order of sources -------------------------------------------------------------
with st.container(border=True, key="card-cat-order"):
    st.markdown('<div class="mr-section">How each package is checked</div>', unsafe_allow_html=True)
    st.caption("Sources are checked in this order, and the first one that decides wins. Where sources disagree, the "
               "disagreement is logged and the FDA record is used.")
    st.markdown(
        "1. **Manual fixes.** A short, documented list of packages the sources get wrong.\n"
        "2. **Transitioned biologics.** Insulins, somatropin and other proteins moved to biologic status in 2020 "
        "are always biologics.\n"
        "3. **FDA Purple Book**, for any biologic: 351(a) is a reference biologic; 351(k) is a biosimilar, or an "
        "interchangeable biosimilar if FDA designated it.\n"
        "4. **FDA NDC Directory** marketing category: NDA, NDA authorized generic, ANDA, BLA, OTC monograph or "
        "unapproved.\n"
        "5. **What Medicaid dispensed** splits NDA brands: generic competition if a matching generic or authorized "
        "generic was dispensed nationally in 2025, single source if not.\n"
        "6. **CMS Medicaid Drug Rebate Program** records fill in when FDA has no record (for example, discontinued "
        "packages) or the brand's strength and form are unknown.\n"
        "7. Nothing matched: OTC / unapproved / unclassified."
    )

# ---- cases worth knowing -----------------------------------------------------------
with st.container(border=True, key="card-cat-cases"):
    st.markdown('<div class="mr-section">Cases worth knowing</div>', unsafe_allow_html=True)
    st.markdown(
        "- **One brand can be in both brand categories.** Categories are decided per strength and form, so "
        "Xarelto 2.5 mg tablets and the oral suspension (generics dispensed) are generic competition, while the 10, "
        "15 and 20 mg tablets are single source.\n"
        "- **Approved is not available.** Eliquis has FDA-approved generics, but patent settlements keep them off "
        "the market, so no generic was dispensed and Eliquis is single source.\n"
        "- **The approval pathway decides, not the molecule.** Basaglar (insulin glargine) is a reference "
        "biologic, not a biosimilar of Lantus, because it was approved as its own drug before 2020. Generic "
        "liraglutide and glucagon were approved by ANDA, so they are generics even though they are peptides.\n"
        "- **Unbranded biologics keep their category.** Sandoz's \"Adalimumab\" is the unbranded version of "
        "Hyrimoz, sold under the same license, so it is an interchangeable biosimilar like Hyrimoz.\n"
        "- **Manufacturer-reported categories are often out of date.** CMS's rebate records still listed Vyvanse "
        "and Symbicort as having no generics in 2025, although generics launched in 2023. This site uses what was "
        "dispensed instead. The rebate estimates still use the reported category, because that is what the "
        "manufacturer pays on.\n"
        "- **State views use the national category.** Whether a generic was dispensed is decided from national "
        "data, so a brand is generic competition in every state if a generic was dispensed anywhere."
    )

# ---- sources -----------------------------------------------------------------------
with st.container(border=True, key="card-cat-sources"):
    st.markdown('<div class="mr-section">Sources</div>', unsafe_allow_html=True)
    st.markdown(
        "- [FDA National Drug Code Directory](https://www.fda.gov/drugs/drug-approvals-and-databases/"
        "national-drug-code-directory): marketing category and application number for each package.\n"
        "- [FDA Purple Book](https://purplebooksearch.fda.gov/): licensed biologics, biosimilars and "
        "interchangeability.\n"
        "- [FDA Orange Book](https://www.fda.gov/drugs/drug-approvals-and-databases/approved-drug-products-"
        "therapeutic-equivalence-evaluations-orange-book): therapeutic-equivalence (A) ratings, patents and "
        "exclusivity.\n"
        "- [CMS Medicaid Drug Rebate Program data](https://www.medicaid.gov/medicaid/prescription-drugs/"
        "medicaid-drug-rebate-program/medicaid-drug-rebate-program-data): rebate category and equivalence codes.\n"
        "- [CMS State Drug Utilization Data](https://www.medicaid.gov/medicaid/prescription-drugs/state-drug-"
        "utilization-data): prescriptions and spending.\n"
        "- [NLM RxNorm](https://www.nlm.nih.gov/research/umls/rxnorm/): ingredient, strength and form, used to "
        "match brands with generics."
    )
