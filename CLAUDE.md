# Medicaid Drug Savings Dashboard

## Purpose
A standalone dashboard on cost-saving alternatives for prescription drugs
(generics, biosimilars), built on public Medicaid data. The long-term plan is a
tab per drug category. **Current scope is only the first piece: the top drugs
within each drug category, ranked by prescription volume and by spending.**
Do not build beyond this scope unless asked.

**"Drug category" in this project means product type**: small-molecule brand,
generic, biologic, biosimilar, etc. (see "Product type classification").
Therapeutic class (ATC) is a secondary dimension for drill-down only.

## Current scope (v1)
1. Ingest Medicaid State Drug Utilization Data (SDUD).
2. Map every NDC to an active ingredient, a product type (primary category),
   and an ATC therapeutic class (secondary).
3. Produce, for each product type category, the top drugs nationally:
   - by volume (number of prescriptions)
   - by spending (Medicaid amount reimbursed)
4. Validate the results.

## Phase 2 scope: switching opportunities (in progress)
Decisions D-011 to D-017 in docs/decisions.md.
1. Match brands to generics and reference biologics to biosimilars:
   - Primary match: same RxNorm SCD (ingredient + strength + dose form) and
     an FDA "A" therapeutic equivalence code on the generic (MDRP file).
   - Biosimilars: Purple Book reference product + same concentration,
     volume and device. Interchangeable and non-interchangeable are separate
     tiers.
   - A looser match (same ingredient + dose form, any strength) is computed as
     an unvetted proof of concept in outputs/savings_{year}.csv only; it is
     not shown on or exported to the public site (D-011 rev.).
2. Price benchmark: volume-weighted average Medicaid paid per unit of the
   matched generics/biosimilars, same group, state and year. Lowest-cost
   alternative shown as a best case.
3. Savings shown three ways: gross (pre-rebate), after statutory minimum
   rebates, and after average rebates calibrated to MACPAC totals.
4. Flag brands on state "brand preferred over generic" lists.
5. A separate dashboard page for savings ("Savings Opportunity Overview").
6. An "Explore Alternatives" page: pick a product, see its equivalents and
   prices, and, if none was dispensed, the FDA-record reason (D-021).
   Built by `python -m src.explore --year YYYY` after src.export_dashboard.
7. The dashboard is a Streamlit app with Plotly charts (app/), hosted on
   Streamlit Community Cloud (D-023; see docs/deploy.md). It reads the
   files in outputs/dashboard/ and does no aggregation of its own.

## Out of scope for now
Do not build these yet; they come in later phases:
- Price benchmarks from outside SDUD (NADAC, Cost Plus Drugs, TrumpRx)
- Dispensing-fee adjustments
- Enrollment normalization

Design choices below should not block these later additions.

## Data source: SDUD
- Publisher: CMS, via data.medicaid.gov. One dataset per year, quarterly rows.
- Start with the most recent complete year. Confirm which year is complete
  before downloading.
- Fields used (verify exact column names on ingest): state, year, quarter,
  utilization_type, ndc, product_name, suppression_used, units_reimbursed,
  number_of_prescriptions, total_amount_reimbursed,
  medicaid_amount_reimbursed, non_medicaid_amount_reimbursed.

### Definitions
- **utilization_type**: FFSU = fee-for-service (state paid the pharmacy);
  MCOU = managed care (the plan paid the pharmacy). Always SUM both for
  totals. Keep the field in the fact table.
- **number_of_prescriptions**: count of paid claims. Each fill and refill
  counts once; no days-supply adjustment (a 90-day fill = 1 prescription).
- **units_reimbursed**: quantity in the NDC's billing unit (tablet, mL, gram,
  each). Units are NOT comparable across dosage forms or drugs. Never sum
  units across different drugs or forms.
- **medicaid_amount_reimbursed**: amount Medicaid paid pharmacies, including
  dispensing fees, before manufacturer rebates. This is the spending measure
  for rankings.
- **total_amount_reimbursed**: Medicaid plus non-Medicaid (e.g. other
  insurance) payments. Keep it; it will be used for price comparisons later.

### Rules
- **NDCs are strings.** Normalize to 11 digits with leading zeros preserved.
  Never read NDC columns as numbers.
- **Fact table grain:** state x year x quarter x NDC x utilization_type.
  Store as Parquet in data/processed/.
- **National rows:** SDUD includes rows with state = "XX" (national totals).
  Store them in a SEPARATE table. Never sum them together with state rows.
- **State dimension:** keep state in the fact table from the start so a single
  state (e.g. California) is just a filter later. Rankings should be functions
  that take year and state parameters (state=None means national).
- **Suppression:** rows with fewer than 11 prescriptions are suppressed
  (blank measures, suppression_used = true). Keep these rows, do not drop
  them. Compute a suppression residual per NDC and period:
  national ("XX") total minus the sum of states.

## Product type classification (primary category)
Classify every NDC by FDA approval pathway, NOT by product name. Names are
unreliable (branded generics, brands sold under generic labels).

### Taxonomy
Two levels: modality, then product type.

| Modality | Product type | Rule |
|---|---|---|
| Small molecule | Brand, single source | NDA; no A-rated generic or authorized generic of the same strength and form dispensed in Medicaid that year (D-019) |
| Small molecule | Brand, generic competition | NDA; an A-rated generic or authorized generic of the same strength and form (RxNorm SCD + unit) was dispensed in Medicaid that year (D-019). MDRP S/I is only the fallback when the strength and form are unknown |
| Small molecule | Authorized generic | Marketing category "NDA AUTHORIZED GENERIC" |
| Small molecule | Generic | ANDA (add a branded_generic flag when the ANDA product has a trade name) |
| Biologic | Reference biologic | BLA, Purple Book type 351(a) |
| Biologic | Biosimilar | BLA, Purple Book type 351(k), not interchangeable |
| Biologic | Interchangeable biosimilar | BLA, Purple Book 351(k) interchangeable |
| Other | OTC / unapproved / unclassified | OTC monograph, unapproved drug, or no match |

Additional flag: **unbranded_biologic** = a biologic whose proprietary name
matches its nonproprietary name (e.g. unbranded adalimumab products). These
share a BLA with a branded product, so detect them by name, not application.
It is a flag on top of the product type, not a product type of its own
(see D-003).

**Caveat on manufacturer-reported categories (D-019):** the MDRP S/I/N category is
self-reported by manufacturers and often does not match the market: it can be
stale (Vyvanse reported S after generics launched), wrong (Invega Sustenna
reported I with no generic ever approved), or true on paper only (Eliquis has
approved generics that patent settlements keep off the market). Classification
therefore uses Medicaid dispensing evidence, and every disagreement with MDRP is
logged. Consequences: "generic competition" means a generic was actually
dispensed, not merely approved; a brand can sit in both categories when only some
strengths have generics (e.g. Xarelto 2.5 mg); and the evidence is national, so a
state view uses the national classification.

Additional flag: **nda_non_innovator** = NDA product that MDRP classifies as
N (non-innovator multiple source), e.g. 505(b)(2) or old NDAs held by
non-originator firms (see D-002).

**Every classification decision, with its reasoning, is recorded in
docs/decisions.md. Add an entry there whenever a rule is added or changed.**

### Sources
- **FDA NDC Directory** (product and package files, plus the excluded /
  discontinued files for older NDCs): marketing category and application
  number for each NDC. Note this is labeler-reported via SPL listings.
- **FDA Purple Book**: splits BLAs into 351(a) reference products vs 351(k)
  biosimilars, and flags interchangeability. Join on BLA number.
- **FDA Orange Book / Drugs@FDA**: validate NDA and ANDA application numbers.
  Flag NDCs whose application number does not match an approval record.
- **Medicaid Drug Rebate Program (MDRP) product file** (data.medicaid.gov):
  drug category S (single source), I (innovator multiple source),
  N (non-innovator multiple source). Also covers terminated NDCs; use it as a
  fallback when the FDA directory has no record.

### Rules and edge cases
- **NDC formats:** FDA uses 10-digit NDCs (4-4-2, 5-3-2, 5-4-1); pad the short
  segment with a leading zero to match SDUD's 11 digits. Test this carefully.
- **Transitioned biologics:** insulins, somatropin, and some other products
  were approved as NDAs but deemed BLAs in March 2020. Classify them as
  biologics regardless of what the listing says (keep an ingredient list in
  the overrides file).
- **Repackagers/relabelers** list under the original application number;
  that is fine for classification.
- **Precedence when sources disagree:** Purple Book (for biologics) > FDA NDC
  Directory > MDRP category. Log every disagreement.
  For the single-source vs generic-competition split, Medicaid dispensing
  evidence > MDRP category (D-019).

## Drug mapping (crosswalk)
Map NDC -> RxNorm ingredient -> ATC therapeutic class, and attach the product
type fields above.

- Use the NLM RxNav REST API (free, no license). Cache every response in
  data/cache/ so reruns don't re-query. Respect the rate limit (about 20
  requests per second); add retries.
- NDC -> RxCUI: use the NDC status/history lookup so discontinued NDCs in older
  data still map.
- RxCUI -> ingredient: use the ingredient (IN) concept; multi-ingredient
  products map to the multiple-ingredient (MIN) concept.
- Product -> ATC: use RxClass with ATCPROD (ATC assigned to the specific
  product) as the main source; fall back to ingredient-level ATC (source
  ATC) only when no product of that ingredient has an ATCPROD class.
  Ingredient-level ATC never returns combination codes (see D-001).
- Verify endpoint paths against current RxNav documentation before relying
  on them.

### Classification rules
- **Primary category:** product type (above). ATC is secondary: store levels
  1-4 in the crosswalk, default to level 2 when used for drill-down.
- **Ranking unit:** within each product type category, rank by ingredient
  (summing all NDCs of that ingredient and product type). Example: brand
  Lipitor and generic atorvastatin appear in different categories.
- **Ingredient overview:** also produce an ingredient-level view summing all
  product types, showing the split of prescriptions and spending across
  product types (e.g. adalimumab: reference vs biosimilar vs unbranded).
- **Drugs with multiple ATC codes:** assign one primary class. Document the
  rule used in the code and keep a list of affected drugs.
- **Combination products:** use the ATC combination code rather than
  splitting across ingredients.
- **Unmapped NDCs:** put manual fixes in a version-controlled overrides file
  (e.g. src/crosswalk_overrides.csv) rather than special-casing logic.

## Outputs
1. **Category summary:** prescriptions and Medicaid spending by modality and
   product type, with shares of the total.
2. **Top drugs per product type category** (top N, default N = 10), two
   tables each:
   - By volume: ingredient, ATC level-2 class, prescriptions, Medicaid amount
     reimbursed, cost per prescription, share of category prescriptions.
   - By spending: same columns, sorted by Medicaid amount reimbursed, with
     share of category spending.
3. **Overall top 25** ingredients by each measure, with the product type split.
Save as CSV/Parquet in outputs/.

## Validation (required before anything is considered done)
- Row counts per year match the source download.
- NDC formatting: every NDC is an 11-character string.
- Crosswalk coverage report: share of prescriptions and of spending mapped
  to an ingredient, a product type, and an ATC class. Target well above 95%
  of spending; report the size of the "unclassified" bucket.
- Product type test cases in tests/ (hand-checked):
  Lipitor (brand, generic competition) vs atorvastatin (generic);
  an authorized generic; Humira (reference biologic) vs Hyrimoz (biosimilar)
  vs unbranded adalimumab (unbranded_biologic flag); Lantus (reference
  biologic, transitioned) vs Semglee / insulin glargine-yfgn (interchangeable
  biosimilar); a branded generic.
- Sum of states plus suppression residual reconciles to national totals.
- National top 25 by spending is compared against CMS's "Medicaid Spending
  by Drug" dataset; large divergences must be investigated.
- Sanity check: common generics (e.g. atorvastatin, amlodipine) should be near
  the top by volume; high-cost brands (e.g. GLP-1s, HIV and immunology drugs)
  should be near the top by spending.
- Tests live in tests/ and run with pytest. tests/test_app.py checks the
  app: every page renders, links between pages work, and the 2025 figures
  match the old dashboard.

## Caveats to carry into any output
- Spending is before manufacturer rebates, so it overstates Medicaid's net
  cost, especially for brand drugs.
- Reimbursement includes pharmacy dispensing fees.
- Prescription counts do not adjust for days supply.
- Small-volume drugs are undercounted at the state level due to suppression.

## Project layout
- data/raw/        downloads (not in git)
- data/processed/  Parquet tables (not in git)
- data/cache/      cached API responses (not in git)
- src/             pipeline code
- tests/           pytest tests
- notebooks/       exploration only; pipeline logic belongs in src/
- outputs/         ranked tables (not in git); outputs/dashboard/ holds the
                   files the app reads
- app/             Streamlit app: Home.py (entry point, navigation),
                   views/ (one file per page), lib/ (data loaders, charts,
                   tooltips, shared styling); theme in .streamlit/config.toml
- archive/         retired static site (not maintained, see its README)

## Tooling and conventions
- Python, DuckDB over Parquet for aggregations (SQL is fine), pandas where
  convenient. Dependencies in requirements.txt.
- Pipeline steps are separate, rerunnable scripts: ingest -> crosswalk ->
  rankings.
- Propose a plan before writing significant code.
- Every table's fields and join conditions are documented in
  docs/data_lineage.md. Update it whenever a field or join changes.
- Prefer clear, readable code over clever code; comment non-obvious data
  decisions.
