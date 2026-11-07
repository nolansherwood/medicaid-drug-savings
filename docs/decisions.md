# Classification decisions log

Every rule that decides how an NDC is classified (product type, ingredient, or
ATC class) is recorded here with the reasoning, the alternatives considered,
and where it lives in code. Add a new entry whenever a rule is added or changed;
do not edit old entries except to mark them superseded.

## Acronyms

| Term | Meaning |
|---|---|
| NDC | National Drug Code: the 11-digit (SDUD) / 10-digit (FDA) package code |
| NDA | New Drug Application: FDA approval for a new drug, normally the original brand |
| ANDA | Abbreviated New Drug Application: FDA approval for a generic copy of an NDA drug |
| 505(b)(2) | An NDA type for a new version of an existing drug (new dose, form, or combination) that relies partly on the original's studies |
| BLA | Biologics License Application: FDA approval for a biologic |
| 351(a) / 351(k) | Sections of the Public Health Service Act: 351(a) = original (reference) biologic, 351(k) = biosimilar |
| MDRP | Medicaid Drug Rebate Program (CMS). Labels each NDC S, I, or N |
| S / I / N | MDRP drug category: Single source (brand, no generics); Innovator multiple source (brand with generics); Non-innovator multiple source (generic) |
| CMS | Centers for Medicare & Medicaid Services |
| SDUD | State Drug Utilization Data (CMS) |
| RxNorm / RxCUI | NLM's drug vocabulary / its concept identifier |
| IN / MIN | RxNorm ingredient / multiple-ingredient concept |
| ATC | Anatomical Therapeutic Chemical classification (WHO) |
| ATCPROD | RxClass relationship assigning ATC classes to specific products rather than ingredients |
| NLM | National Library of Medicine |

## Classification hierarchy (current)

**Primary category = product type**, decided per NDC in this order:

1. **Overrides file** (src/crosswalk_overrides.csv) — manual, documented fixes.
2. **Transitioned biologics** (insulins, somatropin, etc.; ingredient list in
   the overrides file) — always Biologic, regardless of listing.
3. **Purple Book** (for any BLA) — 351(a) → Reference biologic; 351(k) →
   Biosimilar or Interchangeable biosimilar.
4. **FDA NDC Directory marketing category** — NDA, NDA AUTHORIZED GENERIC,
   ANDA, BLA, OTC monograph, unapproved.
5. **MDRP** — S/I/N splits NDA brands into single source vs generic
   competition; also the fallback when the FDA directory has no record
   (e.g. terminated NDCs).
6. Nothing matched → Other / unclassified.

Sources disagreeing at any step are logged to
outputs/product_type_disagreements.csv.

**Flags** (on top of product type, never a separate category):
branded_generic, unbranded_biologic, nda_non_innovator.

**Secondary dimension = ATC therapeutic class** (drill-down only), see D-001.

---

## D-001 — ATC class comes from the product (ATCPROD), not the ingredient
- **Date:** 2026-10-21. **Status:** accepted.
- **Decision:** Assign ATC classes from RxClass ATCPROD on the product RxCUI.
  Fall back to ingredient-level ATC (source ATC) only when no product of an
  ingredient has an ATCPROD class.
- **Why:** Ingredient-level ATC never returns combination codes. For
  losartan / hydrochlorothiazide it returns C09CA (ARB) and C03AA (thiazide),
  never C09DA (ARBs and diuretics), but the project rules require combination
  codes. ATCPROD returns C09DA. It also separates routes (prednisone tablets →
  H02AB glucocorticoids; prednisone enemas → A07EA local corticosteroids).
- **Alternatives considered:** ingredient-level ATC only (rejected: no
  combination codes).
- **Code:** src/crosswalk.py (module docstring, `ingredient_level_classes`).

## D-002 — NDA products labeled N by MDRP are "Brand, generic competition" + flag
- **Date:** 2026-10-21. **Status:** superseded by D-019 for classification; the `nda_non_innovator` flag is kept.
- **Decision:** An NDA product whose MDRP category is N (non-innovator multiple
  source) is classified **Small molecule / Brand, generic competition** and
  gets `nda_non_innovator = true`.
- **Why:** The precedence rule ranks the FDA approval pathway above MDRP, and
  the FDA says NDA. These are typically 505(b)(2) products or old pre-1984
  NDAs held by non-originator firms; they often price like generics. The flag
  keeps them separable so they can be moved later without reclassifying.
- **Alternatives considered:** classify as Generic, following CMS (rejected:
  contradicts FDA > MDRP precedence; can be revisited once the size of the
  group is known).
- **Follow-up:** report prescriptions and spending in this group after the
  first classification run.
- **Result (2025):** 717 NDCs, 6.7M prescriptions, $268M (0.24% of spending).
- **Code:** src/product_type.py (`classify_row`, NDA branch).

## D-003 — unbranded_biologic is a flag, not a product type
- **Date:** 2026-10-21. **Status:** proposed (recommended; awaiting confirmation). Implemented as recommended so it can be reviewed in the output.
- **Decision:** `unbranded_biologic` is a yes/no flag on top of the product
  type. E.g. adalimumab-adaz (unbranded Hyrimoz) = Biosimilar + flag;
  unbranded insulin lispro (Lilly) = Reference biologic + flag.
- **Why:** Unbranded biologics are sold under the same BLA as their branded
  twin, so they are the same product legally. A separate category would split
  an ingredient's biosimilars across two ranking tables. The ingredient
  overview still shows the unbranded share by splitting on product type ×
  flag.
- **Alternatives considered:** separate "Unbranded biologic" product type
  (rejected: breaks within-category rankings).
- **Detection:** unbranded if every word of the proprietary name (ignoring salts,
  dosage forms, release types) appears in the nonproprietary name. Needed because
  e.g. Sandoz lists unbranded Hyrimoz as proprietary "Adalimumab", nonproprietary
  "adalimumab-adaz". Same rule drives `branded_generic` for ANDAs.
- **Result (2025):** 54 NDCs, $573M. Examples: Sandoz "Adalimumab",
  Biocon "Insulin Glargine" (unbranded Semglee).
- **Code:** src/product_type.py (`is_unbranded`, `NON_BRAND_WORDS`).

## D-004 — BLAs that are only partly interchangeable count as interchangeable + flag
- **Date:** 2026-10-21. **Status:** proposed (default; review).
- **Decision:** If any Purple Book product under a 351(k) BLA is interchangeable,
  the BLA's NDCs are **Interchangeable biosimilar** with
  `interchangeable_partial = true` when not all of them are.
- **Why:** 5 BLAs (Wezlana, Yesintek, Avtozma, Eticovo, Yesafili) list some
  strengths/presentations as interchangeable and others not. The Purple Book has
  no NDCs, so an NDC cannot be tied to a specific Purple Book product.
- **Alternatives:** classify as plain Biosimilar (more conservative).
- **Result (2025):** 4 NDCs, $8M.
- **Code:** src/reference_data.py (`build_purple_book`), src/product_type.py.

## D-005 — How MDRP is read
- **Date:** 2026-10-21. **Status:** accepted.
- **COD status codes** are not documented in the file. Mapping was inferred by
  cross-tabulating against the FDA marketing category for the same NDCs:
  01 = ANDA, 02 = BLA, 03 = NDA, 04 = NDA authorized generic, 09/10 = OTC
  monograph, all others = unapproved. Codes appear both as "3" and "03"; both
  are normalized to two digits.
- **As-of record:** MDRP has one row per NDC per quarter, and S/I/N changes over
  time (a brand moves S → I when generics launch). The classification uses the
  latest record at or before Q4 of the analysis year (else the earliest later one).
- **Application numbers** are 7-digit with a leading zero; after normalizing to
  6 digits they match the FDA directory for 98.7% of NDCs. Remaining mismatches
  are real source differences (e.g. Suboxone typo "0224100" for NDA 022410;
  Xifaxan's two NDAs). FDA number wins; all logged.
- **Code:** src/reference_data.py (`MDRP_COD_PATHWAY`, `build_mdrp`),
  src/product_type.py (`load_inputs`).

## D-006 — A BLA not found in the Purple Book is a Reference biologic
- **Date:** 2026-10-21. **Status:** accepted.
- **Why:** every 351(k) biosimilar is listed in the Purple Book, so an unlisted
  BLA cannot be a biosimilar. Logged as `bla_not_in_purple_book`.
- **Result (2025):** 1 NDC, $0.

## D-007 — NDA with no MDRP category is "Brand, single source" + flag
- **Date:** 2026-10-21. **Status:** narrowed by D-019: applies only when market evidence is also unavailable.
- **Why:** without MDRP there is no S/I signal. Flagged `mdrp_category_missing`.
- **Result (2025):** 85 NDCs, $31.5K (negligible).

## D-008 — Classification follows FDA records even when surprising
- **Date:** 2026-10-21. **Status:** accepted (noted for dashboard copy).
- **Basaglar** (Lilly insulin glargine) is a **Reference biologic**, not a
  biosimilar: it was approved by 505(b)(2) NDA before 2020 and became a 351(a)
  BLA at the transition. Same for other pre-2020 "follow-on" insulins.
- **Hyrimoz** is an **Interchangeable biosimilar** (Purple Book, Sept 2026),
  not a plain biosimilar as the original test list assumed.
- **Peptides approved by ANDA** (e.g. generic liraglutide, glucagon) are
  **Small molecule / Generic** because classification follows the approval
  pathway.

## D-009 — Override files
- **Date:** 2026-10-21. **Status:** accepted.
- `src/crosswalk_overrides.csv`: ingredient / ATC fixes (RxNav step).
- `src/product_type_overrides.csv`: per-NDC product type fixes.
- `src/transitioned_biologics.csv`: ingredient name patterns for products
  deemed BLAs on 2020-03-23 (FDA transition list). Kept as its own file
  because it is a rule list, not per-NDC fixes.

## Source disagreements found (2025 run)
See outputs/product_type_disagreements_2025.csv. Largest groups:
- FDA "NDA AUTHORIZED GENERIC" vs MDRP NDA: 201 NDCs, $97M → FDA wins.
- FDA NDA vs MDRP authorized generic: 169 NDCs, $42M → FDA wins.
- FDA BLA vs MDRP NDA: 11 NDCs, $17M (Pertzye, a transitioned pancrelipase) → biologic.
- Application number mismatches: 585 NDCs, $1.4B (dominated by Suboxone) → FDA number.

## D-010 — Ambiguous ATC fallbacks are left for manual override, not guessed
- **Date:** 2026-10-21. **Status:** accepted.
- **Decision:** When an ingredient has no product-level (ATCPROD) class and the
  ingredient-level fallback returns more than one class, leave its ATC blank
  (`atc_source = ambiguous_needs_override`) and fix it in
  src/crosswalk_overrides.csv. A single fallback class is still used.
- **Why:** The first run picked the alphabetically first candidate. For
  combinations the candidates are the components' single-agent classes, so
  Trelegy (fluticasone / umeclidinium / vilanterol, $542M) landed in D07AC
  "skin corticosteroids" and Dymista in the same class. RxClass has no ATCPROD
  for any version of these products, so the combination code cannot be
  recovered automatically.
- **Overrides added (WHO ATC):** Trelegy → R03AL, Dymista → R01AD,
  Altuviiio (efanesoctocog alfa) → B02BD.
- **Result (2025):** spending mapped to ATC 99.34% → 99.57%. Remaining
  unclassified ATC (~$0.3B) is mostly 2024–25 approvals with no ATC code in
  RxClass yet (Crenessity, Filsuvez, Miebo, Tryngolza, Modeyso, Journavx).
  Ingredient and product type are still mapped for these, so they still rank;
  only the ATC drill-down label is missing.
- **Code:** src/crosswalk.py (`build`, step 4).

---

# Phase 2: switching opportunities (brand → generic, reference → biosimilar)

## D-011 — Match on exact strength and form; show a looser match only as a flagged proof of concept
- **Date:** 2026-10-22. **Status:** accepted (user).
- **Primary (defensible):** a brand and a generic match only when they share
  ingredient(s), strength and dose form, i.e. the same RxNorm SCD (semantic
  clinical drug), and the generic carries an FDA "A" therapeutic equivalence
  code (AB, AA, AN, AO, AP, AT, A1–An) in the MDRP product file. B-rated and
  unrated generics are not switch targets.
- **Proof of concept (needs vetting):** same ingredient and dose form across
  strengths. Shown separately, labelled as unvetted, never added to the
  primary total.
- **Why:** ingredient-only matching counts $19.1B of 2025 brand spending as
  "has a generic", but the largest items are different products (Invega
  Sustenna injection vs paliperidone tablets; Abilify Maintena vs
  aripiprazole tablets; Sublocade vs buprenorphine tablets).

## D-012 — Biosimilars matched via Purple Book reference product + presentation
- **Date:** 2026-10-22. **Status:** accepted (approach).
- RxNorm gives each biosimilar its own SCD (suffix, e.g. adalimumab-adaz),
  so SCD matching cannot link biologics. Link 351(k) → reference product with
  the Purple Book, then match concentration, volume and device (pen,
  prefilled syringe, vial) parsed from RxNorm.

## D-013 — Interchangeable and non-interchangeable biosimilars are separate tiers
- **Date:** 2026-10-22. **Status:** accepted (user).
- Interchangeable: pharmacy-level substitution where state law allows.
  Non-interchangeable: needs a new prescription. Reported separately.

## D-014 — Follow-on biologics follow FDA designation
- **Date:** 2026-10-22. **Status:** accepted (user).
- 351(a) follow-ons such as Basaglar are reference biologics, not
  alternatives to Lantus (consistent with D-008).

## D-015 — Generic / biosimilar price benchmark: weighted average Medicaid paid per unit
- **Date:** 2026-10-22. **Status:** accepted (user).
- **Decision:** benchmark = total Medicaid paid ÷ total units for the matched
  alternatives in the same equivalence group, same geography (national or
  state), same year. Lowest-cost alternative NDC holding ≥1% of the group's
  alternative units is shown as a best case.
- **Why:** same data, same pre-rebate basis and same dispensing fees as the
  brand cost; reflects the generics pharmacies actually dispense; follows the
  state filter. The single cheapest NDC may not be able to supply the market.
- **Safeguard:** a state needs ≥1,000 alternative units or ≥2 alternative
  NDCs in the group; otherwise the national benchmark is used and flagged.
- **Code:** src/savings.py (`compute`).

## D-016 — Savings shown three ways: gross, minimum rebates, average rebates
- **Date:** 2026-10-22. **Status:** accepted (user).
- **Gross:** brand paid − brand units × benchmark. Upper bound.
- **Minimum rebates:** each NDC net of its statutory floor by MDRP category:
  S/I 23.1% (17.1% if MDRP flags clotting factor or pediatric), N 13%.
- **Average rebates:** brand/reference side net of the national calibrated
  brand rate; generics 13%; authorized generics and biosimilars 23.1%.
  - Calibration: MACPAC MACStats Feb 2026, FY 2024 Exhibits 26 and 28:
    gross $106.4B, rebates $58.4B (55%), brand 85.8% of gross. Assuming
    generics get 13%, brands average **61.9%**.
  - **National rate for every state.** State-level implied rates are
    distorted by rebate collection timing and prior-period adjustments:
    7 states fall outside [23.1%, 100%] (OR −11%, MS 129%, WY 134%). State
    rates are kept in data/processed/reference/rebate_rates_fy2024.csv for
    reference only.
  - Authorized generics and biosimilars are held at the 23.1% floor rather
    than the brand average, so their net cost is not understated.
- **Known approximation:** statutory rates are % of AMP, applied here to
  Medicaid reimbursement (which includes dispensing fees and sits above AMP).
  Confidential state supplemental rebates are not modelled.
- **Code:** src/rebates.py, src/savings.py.

## D-017 — State brand-preferred-over-generic lists
- **Date:** 2026-10-22. **Status:** accepted (user); coverage partial.
- **Compiled (src/brand_preferred_lists.csv, 269 entries):** SC (Oct 1 2026),
  IA (Oct 1 2026), MD (Aug 6 2026), NH (Jul 1 2026), OH (Oct 2026, "BvG"
  entries in the unified PDL), NY (Oct 22 2026, Brand Less Than Generic
  program). Each row carries its source URL and effective date.
- **Known programs not yet compiled:** Massachusetts (MassHealth "BP" flag,
  searchable drug list only), Louisiana (returns July 1 2026; source PDF
  refused automated download). ISPOR 2024 counts ~15 states with such
  lists or designations; the rest still need to be identified.
- **Matching:** a brand group is flagged for a state when one of its brand
  names equals or starts with a listed brand name. Qualifiers (strength,
  form) are kept for display but not used to narrow the match, so a flag
  can be broader than the list entry.
- **Meaning:** a flag says the state has already judged the brand cheaper
  after rebates; such rows should not be read as savings opportunities.

## D-018 — Match the FDA NDC Directory at product level when the package is missing
- **Date:** 2026-10-22. **Status:** accepted (fix prompted by user review of the semaglutide popup).
- **Problem:** 7,113 SDUD NDCs ($604M) had no FDA package match. Many are
  "inner" unit NDCs (e.g. 00169-4130-01, the Ozempic pen inside carton
  00169-4130-13) that the FDA package file never lists. They fell back to the
  MDRP name ("OZEMPIC 1MG 1X3ML INNER"), had no labeler, and showed as separate
  products.
- **Decision:** when the full NDC has no FDA package match, match its first 9
  digits (labeler + product) to the FDA product listing (`fda_product.product9`).
  `fda_match_level` records which match was used. If neither matches, the
  labeler falls back to the MDRP labeler name.
- **Result (2025):** 5,646 NDCs ($559M) recovered at product level. NDCs with
  no FDA or MDRP record fell from 1,412 to 1,168. "Unknown labeler" rows in the
  national ingredient popup fell from 972 to 27.
- **Code:** src/reference_data.py (`build_fda_ndc`), src/product_type.py (`load_inputs`).

## D-019 — Generic competition is decided by what Medicaid dispensed, not the manufacturer's MDRP category
- **Date:** 2026-10-22. **Status:** accepted (user chose option C; supersedes the
  MDRP S/I rule in D-002 and most of D-007).
- **Problem:** the MDRP drug category (S single source, I innovator multiple
  source, N non-innovator) is reported by the manufacturer and often disagrees
  with the market:
  - Invega Sustenna is reported I, but no generic paliperidone palmitate has
    ever been approved (Orange Book: only Janssen NDAs and Luye's Erzofri NDA).
    I-reported NDAs with no approved generic: 318 NDCs, $2.2B.
  - Vyvanse (generic 2023), Symbicort (2023) and Ventolin are reported S
    although generics were on the market before 2025.
  - Eliquis is S with generics FDA-approved since 2020 but kept off the market
    by patent settlements, so "approved" is not "available" either.
- **Decision:** an NDA brand NDC is **Brand, generic competition** when an
  A-rated generic (ANDA with an MDRP TE code starting "A") or an authorized
  generic with the same RxNorm SCD/GPCK and MDRP unit type had prescriptions in
  that year's national SDUD; otherwise **Brand, single source**. MDRP S/I/N is
  used only when the brand's SCD is unknown. `competition_source` records which
  was used; every disagreement is logged as `mdrp_category_vs_market`.
- **Why:** reflects real substitution options in the year analysed, and uses the
  same matching as the savings page, so the two pages cannot contradict each other.
- **Caveats (also in CLAUDE.md):**
  - "Generic competition" means a generic was dispensed to Medicaid, not merely approved.
  - Classification is per strength and form, so a brand can appear in both
    categories (Xarelto 2.5 mg has generics; other strengths do not).
  - The evidence is national; state views use the national classification.
  - A generic dispensed in tiny volume still counts.
- **Rebates:** the rebate floor still follows the MDRP category as reported
  (S and I both 23.1%, N 13%), because that is what the manufacturer pays.
- **Result (2025):** 1,475 NDCs where MDRP disagrees with the market. Brand,
  generic competition: $9.6B (was $8.0B). Brand, single source: $55.2B (was $56.7B).
  MDRP fallback used for 305 NDCs ($0.1B).
- **Code:** src/product_type.py (`add_market_evidence`, NDA branch of `classify_row`);
  src/rxnav.py (`clinical_concept`, shared with src/equivalence.py).

## D-020 — FDA-approved use shown for each product, from FDA labels
- **Date:** 2026-10-22. **Status:** accepted (user request: tell apart products
  that share an ingredient, e.g. Ozempic vs Wegovy).
- **Source:** the "Indications and Usage" section of each product's FDA label
  (SPL), from the openFDA drug label bulk download (14 files, ~270k labels).
  OTC labels without that section use their "Purpose" section.
- **Join, in order of preference:**
  1. Label document id. The FDA NDC Directory `PRODUCTID` ends with the id
     of the label the labeler filed; it equals the openFDA label `id`.
  2. `openfda.product_ndc` on the 9-digit labeler + product code.
  3. NDCs printed on the label's package panel. Needed because openFDA keeps
     only each label's newest version (so an older version id from the NDC
     Directory finds nothing), and openFDA's harmonized `openfda` block is
     empty for many current labels (Ozempic, Wegovy, Eliquis, Biktarvy).
- **Text cleanup:** section heading and cross-references removed; sentences
  whose words nearly all appeared earlier (restated summaries) dropped;
  trimmed to ~600 characters at a sentence end.
- **Result:** labels for 31,826 of 33,797 SDUD product codes; 99.1% of
  national popup spending has an approved use. The gaps are mostly
  plasma-derived products (clotting factors, immune globulin), which are
  largely missing from the openFDA label set.
- **Code:** src/indications.py, src/export_dashboard.py (`export_indications`).

## D-021 — Explore Alternatives: equivalents per product, and why none exist
- **Date:** 2026-10-22. **Status:** accepted (user request).
- **Alternatives:** the same equivalence groups as the savings page (D-011, D-012).
- **State filter (added 2026-10-22):**
  - Prescriptions, spending and cost per unit/Rx follow the selected state.
  - Where a product was not dispensed in the state (or only in suppressed cells), its national price is shown, labelled "national".
  - Savings use the state's estimate when one exists, else the national one, labelled.
  - Reasons stay national (FDA records).
  - Data: four files of 13 states each, keyed by row ids into the national tables. The page loads only the file holding the selected state.
- **When none was dispensed, the reason comes from FDA records,** checked in this order:
  1. Only non-A-rated generics were dispensed.
  2. Equivalents exist only for other strengths or forms (loose match).
  3. An Orange Book A-rated ANDA exists for the same ingredient + dosage form + route, but none was dispensed ("approved, not marketed"), with the first approval date.
  4. No ANDA ("no generic approved").
  5. Biologics: biosimilars licensed against the reference product (Purple Book "Ref. Product Proprietary Name"), or none licensed.
- **Patents and exclusivities shown:**
  - Unexpired Orange Book patents (latest expiry, count) and exclusivities (decoded, e.g. ODE = orphan drug), per NDA.
  - For biologics, Purple Book reference-product and orphan exclusivity dates.
  - The page says "listed patents expire as late as …" because listed patents can be challenged, settled or invalidated.
- **Known limits:**
  - Patents and exclusivities are per NDA, not per strength.
  - "Approved, not marketed" uses ingredient + dosage form + route, not strength.
- **Code:** src/explore.py; Explore tab script in outputs/dashboard/index.html.

## D-017 (revised 2026-10-23): brand-preferred matching is exact on the FDA full product name
- **Problem found in the pre-publish audit:** prefix matching flagged products the states did not list. NH's "Cleocin" (vaginal cream) flagged Cleocin T, Cleocin Phosphate and Cleocin Hydrochloride. SC's "Restasis" (single-use vials) flagged Restasis MultiDose. Ohio's Spiriva entry flagged Spiriva Respimat. "Lotemax" flagged Lotemax SM, "Toujeo" flagged Toujeo Max, and "Jentadueto" flagged Jentadueto XR.
- **New rule:** a brand is flagged only when its FDA full product name (`PROPRIETARYNAME` + `PROPRIETARYNAMESUFFIX`, e.g. "Spiriva HandiHaler", "Advair Diskus", "Xigduo XR") equals the listed name. Comparison ignores case, punctuation and device words (SoloStar, KwikPen, FlexTouch, pen, vial, syringe, …), so "Lantus SoloStar" still matches "Lantus".
- **List corrections:** Ohio's entry renamed to "Spiriva Handihaler" (the inhalation-capsule product it lists); NH and NY "Depakote Sprinkle" renamed to the FDA name "Depakote Sprinkles".
- **Left unflagged on purpose (5 entries):**
  - Protonix suspension/packet (SC, IA, OH, NY): the FDA uses the same name ("Protonix Delayed-Release") for the packets and the tablets, so a name match would wrongly flag the tablets.
  - Procentra (OH): classified as a generic (ANDA), and the flag applies to brands only.
- **Result (2025):** 211 national opportunity rows carry at least one state flag.

## D-022: exclude savings where brand and alternatives report units on different bases
- **Date:** 2026-10-23. **Status:** accepted (user approved after the pre-publish audit).
- **Problem:** SDUD units are reported by states, and a few NDCs are reported per package rather than per tablet or mL. Example: Hikma methadone 5 mg (00054457025) shows exactly 1 unit per prescription, making the brand look like $177 per tablet against $0.26 for generics. Cost per unit, and so savings, are then not comparable.
- **Decision:** drop an opportunity when the national units per prescription of the brand side and the alternative side (per tier) differ by more than 10×.
  - Excluded rows go to `outputs/savings_excluded_unit_mismatch_{year}.csv`.
  - On the Explore page, those presentations show a note that per-unit prices are not comparable, and no savings are estimated.
- **Result (2025):** 32 groups across tiers and match levels; $12.2M of $9.30B national gross savings (0.1%). Largest: Dolophine/methadone 5 mg, Avastin 4 mL, Suprep. 13 Explore presentations carry the note.
- **Code:** src/savings.py (`units_per_rx`, `drop_unit_mismatches`), src/explore.py.

## Wording fix (2026-10-23)
"After average rebates" card: "Brand rebates at the 61.9% national average" changed to "an estimated 61.9% (calibrated to MACPAC FY 2024)", since the 61.9% is this project's calibration, not a published figure.

## D-011 (revised 2026-10-23): proof-of-concept match removed from the public site
- **Decision (user):** the cross-strength "proof of concept" match is no longer shown on the public dashboard, and its rows are not exported to the dashboard data files (which anyone can download from the site). It stays in the pipeline output `outputs/savings_{year}.csv` (`match_level = proof_of_concept`) for the user's own analysis.
- **Why:** it compares cost per unit across strengths, so its figures need vetting. On a public portfolio site, an unvetted "~$10B" estimate could be quoted out of context.
- **Code:** src/export_dashboard.py (`PUBLIC_MATCH_LEVELS`); the card was removed from outputs/dashboard/index.html.

## D-023: the dashboard moves to Streamlit and Plotly; the static site is retired
- **Date:** 2026-11-04. **Status:** accepted (user request).
- **Decision:** the dashboard is a Streamlit app (`app/`) with Plotly charts, hosted on Streamlit Community Cloud. The claude.ai dashboard and the static site at pharmasavings.nolansherwood.com are retired. Their files are in `archive/static_site/`. This replaces the earlier note in CLAUDE.md that listed Streamlit as superseded.
- **What stays the same:** the app reads the same `outputs/dashboard/*_2025.csv` files and does no aggregation. Rankings, savings and Explore figures match the old dashboard (pinned in tests/test_app.py). Methodology text is carried over word for word.
- **What changed for users:**
  - A landing page with three cards: the savings hero (follows the selected state), a region picker and a product search. Below them is a state map with eight metrics.
  - The map's generic share counts Generic (ANDA) prescriptions only, the same definition as the old Rankings summary sentence. Authorized generics are not included.
  - Product search matches names and ingredients exactly as typed, highest national spending first, as on the old page. Streamlit's own dropdown search matched letters loosely (for example, "Eliquis" found Yervoy), so it is not used.
  - Explore shows each strength and form collapsed to one line with a short marker (alternatives found, or none dispensed) and opens on demand.
  - Animated topology background (2026-11-05): faint blue Vanta.js contour lines behind the cards, on a white page in light mode; desktop only (off on phones and for reduced motion). Cards, tables and charts stay solid.
  - Light and dark modes (2026-11-05). Light is the default (config.toml); a Dark mode toggle in the side menu switches to the original dark look. Same layout and chart colors; text, cards, tables and the green/red figures have a variant per mode (`ui.THEMES`). The choice is remembered in the browser.
  - A Drug Categories page (side menu, 2026-11-05) explains the eight product types in plain language: what each is, how it is decided (the classification hierarchy above and D-019), national 2025 shares and the largest products, edge cases (D-008, D-019) and source links. Text only; it adds no rules.
  - Explore's comparison table has two price columns (2026-11-05): per unit before rebates (what Medicaid paid) and per unit after estimated average rebates (brands and reference biologics 61.9%, generics 13%, authorized generics and biosimilars 23.1%, the same rates as the savings estimates). The second shows where a brand becomes cheaper than its generic after rebates (e.g. Farxiga).
  - The Savings page ranks brands, not single strengths. Every strength and form of a brand with a matched alternative is added up (Humira: 5 strengths, $2.65B gross nationally). Before, one brand could take several of the 25 rows (Humira 4, Vyvanse 5). Selecting a brand shows each strength. The totals on the page did not change. New files: `savings_brands`, `savings_brand_groups` (data_lineage C3b, C3c).
  - Rankings: the generic and brand share tiles are replaced by a "Prescriptions vs. spending" card. It shows two 100% bars in five groups that cover every product type once (`data.MIX_GROUPS`). Generics there include authorized generics.
  - A Rebates page (side menu) explains the rebate estimates: how MACPAC's FY 2024 totals give the 61.9% average brand rate, each top brand's break-even rebate against it, a brand-vs-alternative price per unit before rebates, after minimum rebates and after average rebates, with a caveat that MACPAC counts rebates in the year received, not the year the prescriptions were filled. A state-by-state table was tried and removed (2026-11-04): single-state years are distorted by that timing (Wyoming and Mississippi collected more than they spent). The Savings table briefly dropped its "After avg." column, then restored it (2026-11-05) so the table shows all three estimates side by side; the KPI tile links to the Rebates page for how the rate is estimated. The savings output gained four effective-rate columns for this (data_lineage B13), and the totals are unchanged.
  - Rankings tooltips are HTML, laid out like the old ones (`app/lib/tooltips.py`), and appear when hovering anywhere on a row.
- **Why:** the user asked for Streamlit and Plotly. Community Cloud is free and deploys from GitHub. The app runs Python 3.9 with Streamlit 1.50, the last Streamlit release for Python 3.9.
- **Code:** `app/` and `.streamlit/config.toml`; tests in `tests/test_app.py`; hosting in docs/deploy.md.

## D-024: state product lists keep every product
- **Date:** 2026-11-05. **Status:** accepted (user request).
- **Decision:** `products_states` keeps every product for each state and ingredient, as the national file already did. Before, each state list kept the 12 products with the highest spending and grouped the rest into one "N other products" row.
- **Why:** sorting by spending hid cheap, high-volume generics sold by many manufacturers (for example, 30 methylphenidate products in Texas). The top 12 covered 99.7% of state spending but only 96.5% of state prescriptions, and the Rankings page also ranks drugs by prescriptions. A prescription threshold (for example, 50) was considered; it would keep 99.9% of prescriptions but still needs a grouped row and a second rule to explain. Every row already has at least 11 prescriptions because CMS suppresses smaller counts.
- **Cost:** the file grows from 7.2 MB to 9.8 MB (77,585 rows); `outputs/dashboard/` is about 49 MB.
- **Code:** src/export_dashboard.py `export_products`; the products popup on the Rankings page (now lists Prescriptions, spending and cost per Rx right after the product name).

## D-025: biosimilars matched to the reference product the Purple Book names
- **Date:** 2026-11-06. **Status:** accepted (user). Implements D-012 as written; the code had keyed biologics on RxNorm ingredient text instead.
- **Problem:** biologic groups used the RxNorm clinical drug name with the biosimilar suffix removed. RxNorm spells some ingredients differently for the reference and its biosimilars: NovoLog is "insulin aspart, human", Kirsty is "insulin aspart-xjhz" ("insulin aspart" once the suffix is removed). So Kirsty (interchangeable) and Merilog were never matched to NovoLog, and NovoLog showed "no biosimilar dispensed" although Kirsty had 793 prescriptions. The same text key also paired Semglee, Rezvoglar and unbranded insulin glargine with Basaglar, which is its own 351(a) reference product (D-014), not their reference (Lantus).
- **Decision:** a biologic's group is its reference product plus its presentation. The reference is the product's own BLA for a 351(a) product and, for a 351(k) biosimilar, the BLA of the product its Purple Book row names as "Ref. Product Proprietary Name" (per product, so a BLA covering two products with different references, like Bomyntra / Conexxence, is handled). The presentation is the RxNorm clinical drug name without the ingredient words, so concentration, volume, device and billing unit must still match.
- **Result (2025):** Kirsty and Merilog now match NovoLog and Novo's unbranded Insulin Aspart (+$67M gross); the Basaglar pairs are gone (−$39M gross). National gross savings $9.099B → $9.127B (still $9.1B); interchangeable tier $3.838B → $3.866B; savings after average rebates unchanged. Every other biosimilar–reference pair is unchanged.
- **Code:** src/equivalence.py (`purple_book_references`, `presentation`, `biologic_keys`). Checked by src/audit_matches.py (check A).

## Open items

### OI-1: multipacks of identical units are matched only to the same pack (raised 2026-11-04)
- **Status:** open, not yet decided. Deferred until the Streamlit migration is finished.
- **Problem:** the primary match (D-011, D-012) uses the RxNorm clinical drug, and a pack has its own pack code (GPCK). So a pack only matches an identical pack. That is right for combination and regimen packs, but too strict for multipacks of one unit. Example: Humira `{3 (0.8 ML adalimumab 100 MG/ML Auto-Injector) } Pack` shows "No biosimilar dispensed" although the single 0.8 mL pen has 5 interchangeable biosimilars. A pharmacy can fill the 3-pen prescription with three biosimilar pens, and FDA equivalence ratings do not depend on package size.
- **Size (2025, brand and reference packs):** $4.27B of $97.4B brand spending, 154 groups.
  - Multipacks of one unit: 77 groups, $1.85B, mostly drugs with no generic approved ($1.30B). Clearest misses: the Humira 3-pen pack ($113.1M) and Imvexxy estradiol insert packs (about $0.7M).
  - Combination and regimen packs (Trikafta, Paxlovid, Cabenuva, contraceptives with placebo or iron): 77 groups, $2.42B. Splitting them into separate generics would be a new prescription, not a substitution, so keep matching them pack to pack.
- **Proposed rule:** match a single-unit multipack (`{N (X) } Pack`) to clinical drug X, priced per unit.
- **Before deciding:**
  - Check units: states may report multipack units per package, not per pen or mL (see D-022).
  - Expected effect: national gross savings rise by up to about $114M.
  - **Code:** src/equivalence.py (group keys) and src/rxnav.py (GPCK lookup).
