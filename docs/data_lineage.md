# Data lineage

How every table behind the dashboard is built: the fields each dataset
contributes, and exactly which columns are matched when tables are joined.
Row counts are for SDUD 2025. Classification rules and their reasons are in
docs/decisions.md; this file covers only *what joins to what*.

Notation: `A.col = B.col` is a join condition. `LEFT JOIN` keeps every row
of the left table; `INNER JOIN` keeps only rows found in both. A
transformation applied to a key before matching is shown in the "Key
preparation" line.

---

## Pipeline order

```
1  src/ingest_sdud.py       SDUD CSV ─────────────────────────► sdud_state, sdud_national, sdud_suppression_residual
2  src/reference_data.py    FDA NDC Directory, Purple Book,
                            Orange Book, Drugs@FDA, MDRP ─────► fda_ndc, purple_book, fda_applications, mdrp
3  src/crosswalk.py         sdud_national + RxNav API ────────► crosswalk_ndc, crosswalk_ingredient
4  src/product_type.py      sdud_national + fda_ndc + mdrp
                            + purple_book + fda_applications ─► product_type_ndc_2025
5  src/rankings.py          sdud + crosswalk_ndc
                            + product_type_ndc ───────────────► outputs/category_summary, top_by_product_type, top_overall
6  src/rebates.py           MACPAC exhibits 26 + 28 ──────────► rebate_rates_fy2024
7  src/equivalence.py       product_type_ndc + crosswalk_ndc
                            + RxNav API ──────────────────────► equivalence_ndc_2025
8  src/savings.py           sdud + equivalence_ndc + rebate_rates
                            + brand_preferred_lists ──────────► outputs/savings_2025
9  src/export_dashboard.py  steps 5 and 8 ────────────────────► the four dashboard datasets
```

The join key that ties everything to SDUD is the **11-digit NDC** (`ndc`).

---

## Part A. Source datasets: fields pulled

### A1. SDUD (CMS State Drug Utilization Data 2025)
File: `data/raw/sdud_2025.csv`, 5,308,547 rows. One row per state × quarter × NDC × utilization type.

| Source column | Stored as | Notes |
|---|---|---|
| State | `state` | Two-letter code; `XX` = national total |
| Year | `year` | |
| Quarter | `quarter` | |
| Utilization Type | `utilization_type` | FFSU = fee-for-service, MCOU = managed care |
| NDC | `ndc` | Non-digits removed, left-padded to 11 digits |
| Labeler Code, Product Code, Package Size | `labeler_code`, `product_code`, `package_size` | Only used to check `ndc` = labeler + product + package |
| Product Name | `product_name` | Truncated to 10 characters by CMS |
| Suppression Used | `suppression_used` | True when the row has < 11 prescriptions and blank measures |
| Units Reimbursed | `units_reimbursed` | In the NDC's billing unit |
| Number of Prescriptions | `number_of_prescriptions` | |
| Total Amount Reimbursed | `total_amount_reimbursed` | Kept, not used yet |
| Medicaid Amount Reimbursed | `medicaid_amount_reimbursed` | **The spending measure** |
| Non Medicaid Amount Reimbursed | `non_medicaid_amount_reimbursed` | Kept, not used yet |

### A2. FDA NDC Directory
Files: `ndctext.zip` (current) and `ndc_excluded.zip` (excluded/discontinued), each with a product file and a package file.

| File | Columns pulled |
|---|---|
| package.txt / Packages_excluded.txt | `PRODUCTID`, `NDCPACKAGECODE`, `STARTMARKETINGDATE`, `ENDMARKETINGDATE` |
| product.txt / Products_excluded.txt | `PRODUCTID`, `PRODUCTNDC`, `PRODUCTTYPENAME`, `PROPRIETARYNAME`, `PROPRIETARYNAMESUFFIX`, `NONPROPRIETARYNAME`, `SUBSTANCENAME`, `DOSAGEFORMNAME`, `ROUTENAME`, `MARKETINGCATEGORYNAME`, `APPLICATIONNUMBER`, `LABELERNAME`, `NDC_EXCLUDE_FLAG`, `STARTMARKETINGDATE`, `ENDMARKETINGDATE` |

### A3. CMS Medicaid Drug Rebate Program (MDRP) product file
File: `mdrp_products.csv`, 2,002,508 rows, one per NDC per quarter (2014 Q3 – 2026 Q2).

Columns pulled: `NDC`, `Year`, `Quarter`, `Drug Category`, `COD Status`,
`FDA Application Number`, `FDA Product Name`, `Labeler Name`,
`Termination Date`, `Unit Type`, `FDA Therapeutic Equivalence Code`,
`Clotting Factor Indicator`, `Pediatric Indicator`.

### A4. FDA Purple Book (September 2026)
File: `purplebook.csv`, full product list (second section of the file), 2,251 products.

Columns pulled: `BLA Number`, `License Type`, `Proprietary Name`, `Proper Name`,
`Marketing Status`, `Ref. Product Proper Name`, `Ref. Product Proprietary Name`, `Applicant`.

### A5. FDA Orange Book and Drugs@FDA
| File | Columns pulled |
|---|---|
| Orange Book `products.txt` | `Appl_Type` (N or A), `Appl_No` |
| Drugs@FDA `Applications.txt` | `ApplType` (NDA, ANDA, BLA), `ApplNo` |

### A6. NLM RxNav / RxClass API (cached in `data/cache/rxnav_cache.sqlite`)
| Endpoint | Input | Fields read |
|---|---|---|
| `/ndcstatus.json?ndc=` | NDC | `rxcui`, `status`, `conceptName`, `ndcHistory[].activeRxcui`, `ndcHistory[].originalRxcui`, `ndcHistory[].endDate` |
| `/rxcui/{id}/related.json?tty=IN+MIN` | product RxCUI | ingredient concepts: `rxcui`, `name`, `tty` |
| `/rxcui/{id}/historystatus.json` | retired RxCUI | `derivedConcepts.remappedConcept[].remappedRxCui`, `.remappedActive` |
| `/rxclass/class/byRxcui.json?relaSource=ATCPROD` | product RxCUI | `rxclassMinConceptItem.classId`, `minConcept.rxcui` |
| `/rxclass/class/byRxcui.json?relaSource=ATC` | ingredient RxCUI | same (fallback only) |
| `/rxclass/allClasses.json?classTypes=ATC1-4` | — | `classId`, `className` |
| `/rxcui/{id}/properties.json` | product RxCUI | `rxcui`, `name`, `tty` |
| `/rxcui/{id}/related.json?tty=SCD+GPCK` | branded RxCUI | generic concept `rxcui`, `name`, `tty` |

### A6b. openFDA drug labels (bulk download, Oct 2026)
Files: `data/raw/reference/openfda_label/drug-label-00NN-of-0014.json.zip`, about 270k labels.

| Field | Used for |
|---|---|
| `id` (SPL document id) | Join 1, to `fda_ndc.spl_id` |
| `set_id`, `effective_time` | Label identity and date |
| `openfda.product_ndc`, `openfda.brand_name`, `openfda.generic_name` | Join 2, and display |
| `package_label_principal_display_panel` | Join 3: printed NDCs |
| `indications_and_usage` (else `purpose`) | The approved-use text |
| `spl_product_data_elements` | Brand name when `openfda` is empty |

### A7. MACPAC MACStats (Feb 2026), FY 2024
| Exhibit | Columns pulled (per state + national) |
|---|---|
| 28 | gross spending total / FFS / managed care; rebates total / FFS / managed care |
| 26 | gross spending total; brand %, generic %, unknown % |

### A8. State brand-preferred-over-generic lists (compiled by hand)
File: `src/brand_preferred_lists.csv`, 269 rows, from the SC, IA, MD, NH, OH and NY 2026 lists.
Columns: `state`, `brand_name`, `qualifier`, `note`, `effective_date`, `source_url`.

### A9. Project rule files (version-controlled)
| File | Columns |
|---|---|
| `src/crosswalk_overrides.csv` | `ndc`, `ingredient_rxcui`, `ingredient_name`, `atc4`, `note` |
| `src/product_type_overrides.csv` | `ndc`, `modality`, `product_type`, `note` |
| `src/transitioned_biologics.csv` | `pattern`, `note` |

---

## Part B. Built tables and their joins

### B1. `sdud_state_2025`, `sdud_national_2025` (step 1)
- **Rows:** 4,987,938 state rows, 320,609 national rows.
- **Fields:** the 15 SDUD fields in A1.
- **Built by:** a split, not a join: `state <> 'XX'` → state table; `state = 'XX'` → national table.

### B2. `sdud_suppression_residual_2025` (step 1)
- **Rows:** 320,609.
- **Grain:** year × quarter × NDC × utilization type.
- **Join:** the national rows `FULL JOIN` the state rows (summed across states) on

  | national | | state sum |
  |---|---|---|
  | `year` | = | `year` |
  | `quarter` | = | `quarter` |
  | `ndc` | = | `ndc` |
  | `utilization_type` | = | `utilization_type` |

- **Fields:** for each measure, `national_*`, `states_*`, and `residual_* = national − states`. Also `national_suppressed`, `n_state_rows`, `n_state_rows_suppressed`.

### B3. `reference/fda_ndc` (step 2)
- **Rows:** 599,852, one per 11-digit package NDC.
- **Join:** package file `LEFT JOIN` product file on `package.PRODUCTID = product.PRODUCTID`, done separately for the current and excluded files, which are then stacked.
- **Key preparation:** `ndc` = `NDCPACKAGECODE` converted from 10 to 11 digits: each segment is padded to 5-4-2 digits, which adds the leading zero to whichever segment is short.
  - 4-4-2 `0002-1433-80` → `00002143380`
  - 5-3-2 `12345-678-90` → `12345067890`
  - 5-4-1 `12345-6789-0` → `12345678900`

  100 excluded codes containing letters cannot be converted and are dropped.
- **De-duplication:** one row per `ndc`. A current listing beats an excluded one; then the latest start date wins.
- **Derived:** `application_prefix` (NDA/ANDA/BLA) and `application_no` = the digits of `APPLICATIONNUMBER`, leading zeros removed, then padded to 6.

### B3a. `reference/fda_product` (step 2)
- **Rows:** one per FDA product, the 9-digit labeler + product code.
- **Key preparation:** `product9` = `PRODUCTNDC` segments padded to 5 + 4 digits (`0169-4130` → `001694130`).
- **Fields:** the same as `fda_ndc`, minus package fields. A current listing beats an excluded one, then the latest start date wins.
- **Used for:** the fallback join for NDCs the package file does not list, such as inner unit NDCs (D-018).

### B4. `reference/purple_book` (step 2)
- **Rows:** 864, one per BLA.
- **Key preparation:** `bla_no` = `BLA Number` padded to 6 digits.
- **Derived (no join):**
  - `bla_type`: 351(a) or 351(k).
  - `any_interchangeable` / `all_interchangeable`: from `License Type`.
  - Names concatenated across the BLA's products.

### B5. `reference/fda_applications` (step 2)
- **Rows:** 29,409.
- **Built by:** stacking (UNION) the two files below, then keeping distinct (`appl_type`, `appl_no`):

  | | Drugs@FDA | Orange Book |
  |---|---|---|
  | `appl_type` | `ApplType` | `Appl_Type`, mapped `N`→NDA, `A`→ANDA |
  | `appl_no` | `ApplNo`, padded to 6 digits | `Appl_No`, padded to 6 digits |

### B6. `reference/mdrp` (step 2)
- **Rows:** 2,002,508.
- **Key preparation:**
  - `ndc` = non-digits removed, padded to 11.
  - `mdrp_cod_status` padded to 2 digits ('3' → '03').
  - `mdrp_application_no` = digits only, leading zeros removed, padded to 6.

### B7. `crosswalk_ndc` (step 3)
- **Rows:** 50,925, one per NDC in `sdud_national_2025`.
- **Starting list:** `SELECT DISTINCT ndc FROM sdud_national`, with `sum(number_of_prescriptions)` and `sum(medicaid_amount_reimbursed)` kept as weights.
- **API lookups (one per key, cached):**

  | Input field | Endpoint | Output fields |
  |---|---|---|
  | `ndc` | ndcstatus | `product_rxcui` (= `rxcui`, else latest `ndcHistory.activeRxcui`, else `originalRxcui`), `ndc_status`, `rxnorm_product_name`, `product_rxcui_source` |
  | `product_rxcui` | related IN+MIN | `ingredient_rxcui`, `ingredient_name`, `ingredient_tty`, `component_in_rxcuis` (MIN if there is one, else the single IN, else the INs joined with "+" as MULTI_IN) |
  | `product_rxcui` with no ingredient | historystatus → related IN+MIN on `remappedRxCui` | the same ingredient fields; `ingredient_source = history_remap` |
  | `product_rxcui` (or its remap) | RxClass ATCPROD | `product_atc4_codes` |
  | `ingredient_rxcui` (fallback) | RxClass ATC, filtered to `minConcept.rxcui = ingredient_rxcui` | candidate ATC codes |
  | `atc4` and its prefixes | allClasses | `atc1_name` … `atc4_name` via `classId = atc1/atc2/atc3/atc4` |

- **ATC levels from the code:** `atc1` = first character of `atc4`, `atc2` = first 3, `atc3` = first 4.
- **Primary class per ingredient:** chosen from the `product_atc4_codes` of all its NDCs, weighted by prescriptions (rule D-001 / D-010).
- **Overrides:**
  - NDC-level: `crosswalk_overrides.ndc = crosswalk_ndc.ndc`.
  - Ingredient-level (blank `ndc`): `crosswalk_overrides.ingredient_rxcui = crosswalk_ndc.ingredient_rxcui`.

### B8. `crosswalk_ingredient` (step 3)
- **Rows:** 2,120.
- **Built by:** one row per `ingredient_rxcui` from B7, plus `n_atc_candidates`.

### B9. `product_type_ndc_2025` (step 4)
- **Rows:** 50,925, one per NDC.
- **SQL joins:**

  | Left | Join | Right | Condition |
  |---|---|---|---|
  | SDUD national, summed by `ndc` | LEFT JOIN | `fda_ndc` | `sdud.ndc = fda_ndc.ndc` |
  | (result) | LEFT JOIN | `fda_product`, only when the line above found nothing | `left(sdud.ndc, 9) = fda_product.product9` |
  | (result) | LEFT JOIN | `mdrp`, one record per NDC | `sdud.ndc = mdrp.ndc` |

  Every FDA field is `coalesce(package value, product value)`. `fda_match_level` = `package`, `product`, or empty.

- **Market evidence for generic competition (D-019):**

  | Left | Join | Right | Condition |
  |---|---|---|---|
  | (result) | LEFT JOIN | `crosswalk_ndc` | `ndc = crosswalk.ndc` (to get `product_rxcui`) |
  | `product_rxcui` | API | RxNav properties / related SCD+GPCK | → `clinical_rxcui` |
  | brand NDC | lookup | set of (`clinical_rxcui`, unit) of A-rated ANDA or authorized-generic NDCs with prescriptions > 0. A-rated: `mdrp_te_code` starts "A", or, when it is blank or "NR", every Orange Book `products.txt` row of the NDC's ANDA (`Appl_No`) has a `TE_Code` starting "A" (D-026). Unit: `mdrp_unit_type`; blank → the most common unit of the other NDCs with the same `clinical_rxcui` (D-026); then put on one basis per clinical drug (D-027, see `unit_type`) | `(brand.clinical_rxcui, brand unit)` in the set → `generic_on_market` |


  The MDRP record kept is the latest one with (`year`, `quarter`) ≤ (2025, Q4); if there is none, the earliest later one.
- **Lookups (in code, row by row):**

  | Field on this table | | Field on reference | Purpose |
  |---|---|---|---|
  | `application_no` (= `fda_application_no`; else `mdrp_application_no`) | = | `purple_book.bla_no` | Reference vs biosimilar vs interchangeable |
  | (pathway as NDA/ANDA/BLA, `application_no`) | = | (`fda_applications.appl_type`, `appl_no`) | Validation flag `application_validated` |
  | `substance_name`, `nonproprietary_name`, `proprietary_name`, `mdrp_product_name`, `sdud_product_name` | word match | `transitioned_biologics.pattern` | `transitioned_biologic` |
  | `ndc` | = | `product_type_overrides.ndc` | Manual override |

- **Fields carried from each source:**
  - **From SDUD:** `sdud_product_name`, `prescriptions`, `medicaid_amount`.
  - **From `fda_ndc`:**
    - `fda_listing_source`, `fda_marketing_category`, `fda_application_prefix`, `fda_application_no`
    - `proprietary_name`, `proprietary_name_suffix`, `nonproprietary_name`, `substance_name`
    - `labeler_name`, `dosage_form`, `route`
  - **From `mdrp`:**
    - `mdrp_record_year`, `mdrp_record_quarter`
    - `mdrp_drug_category`, `mdrp_cod_status`, `mdrp_application_no`, `mdrp_product_name`
    - `mdrp_unit_type`, `mdrp_te_code`, `mdrp_clotting_factor`, `mdrp_pediatric`
  - **From `purple_book`:** `purple_book_match`, `purple_book_bla_type`, `interchangeable_partial`.
  - **Derived:**
    - `pathway`, `pathway_source`, `application_no`
    - `modality`, `product_type`
    - `transitioned_biologic`, `nda_non_innovator`, `mdrp_category_missing`
    - `branded_generic`, `unbranded_biologic`, `application_validated`
    - `other_reason`, `classification_source`
    - `product_rxcui`, `clinical_rxcui`, `generic_on_market`, `competition_source` (D-019)

### B10. Ranking tables (step 5, `src/rankings.py`)
Base view `drug`, one row per NDC for the chosen geography:

| Left | Join | Right | Condition |
|---|---|---|---|
| SDUD, summed by `ndc` (national table; or state table `WHERE state = X`) | LEFT JOIN | `crosswalk_ndc` | `sdud.ndc = crosswalk_ndc.ndc` |
| (result) | LEFT JOIN | `product_type_ndc_2025` | `sdud.ndc = product_type.ndc` |

Fields used from `drug`:

| Field | Source |
|---|---|
| `prescriptions` | sum of SDUD `number_of_prescriptions` |
| `medicaid_amount` | sum of SDUD `medicaid_amount_reimbursed` |
| `ingredient_rxcui`, `ingredient_name` | crosswalk |
| `atc2`, `atc2_name` | crosswalk |
| `modality`, `product_type`, `unbranded_biologic` | product type |

Outputs are `GROUP BY` the following, with no further joins except the category totals:

| Output | Grouped by | Extra join |
|---|---|---|
| `category_summary` | `modality`, `product_type` | — |
| `top_by_product_type` | `product_type`, `ingredient_rxcui` | category totals on `product_type` = `product_type`, for the shares |
| `top_overall` | `ingredient_rxcui` | — |
| `ingredient_product_type_split` | `ingredient_rxcui`, `product_type`, `unbranded_biologic` | — |

### B11. `reference/rebate_rates_fy2024` (step 6)
- **Rows:** 51 (50 jurisdictions + US; Virginia and Puerto Rico were not parsed).
- **Join:** Exhibit 28 rows matched to Exhibit 26 rows on state name, which is then mapped to its two-letter code.
- **Derived:** `brand_rate_implied = (rebates − 0.13 × generic gross) ÷ brand gross`. Only the `US` row's `brand_rate_used` (0.619) is used downstream.

### B12. `equivalence_ndc_2025` (step 7)
- **Rows:** 50,925.
- **SQL join:**

  | Left | Join | Right | Condition |
  |---|---|---|---|
  | `product_type_ndc_2025` | LEFT JOIN | `crosswalk_ndc` | `product_type.ndc = crosswalk.ndc` |

- **API lookups:**

  | Input | Endpoint | Output |
  |---|---|---|
  | `product_rxcui` | properties (then historystatus if retired) | `product_tty` |
  | `product_rxcui` when the product is SBD/BPCK | related SCD+GPCK | `clinical_rxcui`, `clinical_name`, `clinical_tty` (itself when already SCD/GPCK) |

- **Derived keys, which are the "join columns" for the savings step:**

  | Field | Small molecules | Biologics |
  |---|---|---|
  | `unit_type`, `unit_factor` | `mdrp_unit_type` (blank → most common unit of the same `clinical_rxcui`, D-026), then on one basis per clinical drug (D-027): EA with a leading volume in `clinical_name` ("0.4 ML ...", not packs) → ML with `unit_factor` = that volume; EA for a `clinical_rxcui` whose other NDCs are TAB or CAP → that unit, factor 1. SDUD `units_reimbursed` × `unit_factor` = units in `unit_type` | same |
  | `primary_key` | `'scd:' + clinical_rxcui + '|' + unit_type` | `'bio:' + reference BLA + '|' + presentation(clinical_name) + '|' + unit_type` (D-025). Reference BLA: the NDC's own `application_no` for 351(a); for 351(k), the 351(a) BLA of the product named in the raw Purple Book's `Ref. Product Proprietary Name` (matched on the biosimilar's BLA + proprietary name; an unbranded biosimilar takes its BLA's only reference; of several reference BLAs with that name, the one with the same presentation). `presentation` = clinical_name without lowercase ingredient words. Fallback when no reference is found: `'bio:' + clinical_name without biosimilar suffix + '|' + unit_type` |
  | `loose_key` | `'form:' + clinical_name with numbers/units removed + '|' + unit_type` | same |

- **Role** (from `product_type`, `mdrp_te_code` and the Orange Book): brand, generic (A-rated: MDRP TE code "A…", or Orange Book A for every product of its ANDA when MDRP is blank or "NR", D-026), generic_not_a_rated, authorized_generic, reference, biosimilar, interchangeable, none.
- **Savings units:** the savings step multiplies SDUD `units_reimbursed` by `unit_factor`; the minimum-volume test for a benchmark (`MIN_ALT_UNITS`) uses the units as reported.
- **`rebate_min_rate`:** from `mdrp_drug_category`, `mdrp_clotting_factor`, `mdrp_pediatric`.

### B13. `outputs/savings_2025` (step 8)
- **Rows:** 33,916 (geography × match level × tier × group).
- **Step 1: measures by NDC.** National rows with `geo = 'US'`, plus state rows with `geo = state`, each summed by `ndc`: `units` (SDUD `units_reimbursed`), `rx`, `amt` (SDUD `medicaid_amount_reimbursed`).
- **Joins:**

  | Left | Join | Right | Condition |
  |---|---|---|---|
  | measures | INNER JOIN | `equivalence_ndc_2025` | `measures.ndc = equivalence.ndc` |
  | brand side, summed per (`geo`, key) | LEFT JOIN | alternative side, summed per (`geo`, key) | `brand.geo = alt.geo AND brand.key = alt.key` |
  | (result) | LEFT JOIN | alternative side where `geo = 'US'` (national fallback price) | `brand.key = us_alt.key` |
  | (result) | LEFT JOIN | group names (from `equivalence_ndc`, grouped by key) | `savings.group_key = equivalence.primary_key` or `loose_key` |
  | (result) | name match | `brand_preferred_lists` | `normalize(proprietary_name + ' ' + proprietary_name_suffix)` = `normalize(brand_name)`; exact, device words ignored (D-017 rev.) |
  | (no join) | — | `rebate_rates_fy2024` | value where `state = 'US'` |

  Here `key` is `primary_key` (match_level = primary) or `loose_key` (proof_of_concept). The brand side is role brand/reference; the alternative side is the roles of the tier.
- **`normalize`:** lowercase, with every non-letter/digit run replaced by one space, and device words (SoloStar, pen, vial, syringe, …) removed.
- **Unit-mismatch exclusion (D-022):** groups whose national brand and alternative units per prescription differ by more than 10× are dropped and listed in `outputs/savings_excluded_unit_mismatch_{year}.csv`.

---

## Part C. The four dashboard datasets (step 9)

### C1. `rankings` → `outputs/dashboard/rankings_2025.csv` (20,162 rows)
Built by running B10 once nationally (`state = 'US'`) and once per state, then stacking the results.

| Field | Comes from |
|---|---|
| `state` | `US` for the national run, otherwise the state code |
| `category` | `product_type` (B9), or "All product types" for the overall ranking |
| `measure` | `volume` or `spending` (which column was ranked) |
| `rank` | position after sorting by the measure |
| `ingredient` | `crosswalk_ndc.ingredient_name` (B7) |
| `therapeutic_class` | `crosswalk_ndc.atc2_name` (B7) |
| `prescriptions` | sum of SDUD `number_of_prescriptions` |
| `medicaid_amount` | sum of SDUD `medicaid_amount_reimbursed` |
| `cost_per_prescription` | `medicaid_amount ÷ prescriptions` |
| `share_of_category_prescriptions`, `share_of_category_spending` | ingredient ÷ category total (same geography) |
| `product_type_split_prescriptions`, `product_type_split_spending` | overall ranking only: the ingredient's shares by `product_type` |

### C2. `summary` → `outputs/dashboard/category_summary_2025.csv` (424 rows = 53 geographies × 8 product types)
| Field | Comes from |
|---|---|
| `state` | as above |
| `modality`, `product_type` | B9 |
| `ingredients` | count of distinct `ingredient_rxcui` |
| `prescriptions`, `medicaid_amount` | SDUD sums |
| `share_prescriptions`, `share_spending` | category ÷ geography total |
| `cost_per_prescription` | `medicaid_amount ÷ prescriptions` |

### C3. `savings_top` → `outputs/dashboard/savings_top_2025.csv` (2,467 rows)
The rows of B13 with `match_level = primary` and `gross_savings > 0`, top 25 per geography × tier. The app no longer reads this file (the Savings page uses C3b); it is kept for analysis. Proof-of-concept rows stay in `outputs/savings_{year}.csv` and are not exported (D-011 rev.).

| Field | Comes from |
|---|---|
| `geo`, `match_level`, `tier`, `group_key` | B13 |
| `group_name` | `equivalence.clinical_name` (biosimilar suffix removed), or the strength-free form name for proof of concept |
| `ingredient` | `crosswalk_ndc.ingredient_name` |
| `brand_names` | `fda_ndc.proprietary_name` of brand/reference NDCs in the group (up to 4) |
| `alternative_names` | `fda_ndc.proprietary_name` of the alternatives (up to 4) |
| `therapeutic_class` | `crosswalk_ndc.atc2_name` |
| `brand_prescriptions`, `brand_spending` | SDUD sums over brand NDCs |
| `alt_share_of_units` | alternative `units_reimbursed` ÷ (brand + alternative units) |
| `brand_cost_per_unit` | brand `medicaid_amount_reimbursed` ÷ brand `units_reimbursed` |
| `alt_cost_per_unit` | alternative `medicaid_amount_reimbursed` ÷ alternative `units_reimbursed` (same geography, else national) |
| `benchmark_source` | which price was used |
| `gross_savings` | `brand_spending − brand units × alt_cost_per_unit` |
| `gross_savings_best_case` | same, using the cheapest alternative NDC with ≥ 1% of its units |
| `min_rebate_savings` | brand × (1 − `rebate_min_rate`) − switched cost × (1 − alternative `rebate_min_rate`) |
| `avg_rebate_savings` | brand × (1 − 0.619) − switched cost × (1 − 0.13 for generics or 0.231 for authorized generics/biosimilars) |
| `brand_preferred_here`, `brand_preferred_states` | name match to `brand_preferred_lists` |
| `savings_reverse_after_rebates` | `gross_savings > 0 AND avg_rebate_savings < 0` |
| `brand_min_rebate_rate`, `brand_avg_rebate_rate` | effective brand-side rebate in each scenario: 1 − net ÷ `brand_spending`, over the group's brand NDCs (B13 only, and C3b/C3c) |
| `alt_min_rebate_rate`, `alt_avg_rebate_rate` | the same for the alternatives (13% generics, 23.1% authorized generics and biosimilars, spending-weighted) |
| `key` | `geo|match_level|tier|group_key` (row identifier for the page) |

### C3b. `savings_brands` → `outputs/dashboard/savings_brands_2025.csv` (3,329 rows)
The Savings page's top 25 table: one row per brand, adding up all of the brand's strengths and forms (`src.export_dashboard.brand_rollup`). Built from the same rows as C3 (B13, `primary`, `gross_savings > 0`), but from every row, not only the top 25 per tier.

| Field | Comes from |
|---|---|
| `geo`, `match_level` | B13 |
| `tier_view` | a tier, or `all`. `all` first keeps each group's best tier (as C4 does), so a biologic's strength is not counted in both biosimilar tiers |
| `brand_key` | first name in `brand_names` (lower case) + `|` + `ingredient`. One key per brand even when the listed names differ by strength |
| `brand`, `ingredient` | first name in `brand_names` of the highest-savings strength; `ingredient` |
| `tiers` | the tiers among the brand's rows |
| `groups` | number of strength/form groups added up |
| `brand_prescriptions`, `brand_spending`, `gross_savings`, `min_rebate_savings`, `avg_rebate_savings` | sums over the groups (after-rebate sums can be negative) |
| `alt_share_of_units` | the groups' `alt_share_of_units`, weighted by `brand_prescriptions` |
| `groups_reverse_after_rebates`, `groups_brand_preferred_here` | counts of groups with the flag |
| `brand_preferred_states` | union of the groups' `brand_preferred_states` |
| `rank` | 1–25 by `gross_savings` within `geo` × `match_level` × `tier_view` |

### C3c. `savings_brand_groups` → `outputs/dashboard/savings_brand_groups_2025.csv` (9,395 rows)
The rows of B13 behind each brand in C3b, with C3's fields plus `tier_view` and `brand_key` (join to C3b on `geo`, `match_level`, `tier_view`, `brand_key`). Shown as the strength-by-strength detail when a brand is selected. C4b's NDC lists cover these groups too.

### C3d. `rebate_rates` → `outputs/dashboard/rebate_rates_2025.csv` (51 rows)
A copy of `data/processed/reference/rebate_rates_fy2024.csv` (src.rebates, D-016) for the Rebates page (which uses only the `US` row): MACPAC FY 2024 `gross_millions`, `rebates_millions`, `rebate_share_of_gross`, `brand_share_of_gross`, `generic_share_of_gross`, `brand_rate_implied`, `brand_rate_used`, `generic_rate_used`, `note`, per `state` plus `US`. Puerto Rico and Virginia have no row.

### C4. `savings_summary` → `outputs/dashboard/savings_summary_2025.csv` (212 rows)
B13 (`primary` only) summed per `geo` × `match_level` × `tier`, plus `tier = 'all'`, which keeps each group's best tier (so a reference biologic is not counted twice).

| Field | Comes from |
|---|---|
| `groups` | count of groups with `gross_savings > 0` |
| `brand_spending` | sum of `brand_spending` for those groups |
| `gross_savings`, `min_rebate_savings`, `avg_rebate_savings` | sums of the positive values only |
| `groups_reverse_after_rebates` | count of `savings_reverse_after_rebates` |
| `groups_brand_preferred_here`, `gross_savings_brand_preferred_here` | from `brand_preferred_here` |

### C4b. `savings_ndcs` → `outputs/dashboard/savings_ndcs_{year}.csv` (461 rows)
The Brand NDCs and Alternative NDCs columns on the Savings Opportunity Overview.

| Left | Join | Right | Condition |
|---|---|---|---|
| distinct (`match_level`, `tier`, `group_key`) of `savings_top` and `savings_brand_groups` | lookup | `equivalence_ndc_2025` ⨝ SDUD national (on `ndc`, Medicaid spending > 0) | `group_key = equivalence.primary_key` (primary) or `= equivalence.loose_key` (proof of concept) |

| Field | Comes from |
|---|---|
| `brand_ndcs`, `n_brand_ndcs` | NDCs with role `brand` (generic tier) or `reference` (biosimilar tiers), 11 plain digits, highest national Medicaid spending first |
| `alt_ndcs`, `n_alt_ndcs` | NDCs with the tier's alternative roles: generic tier = A-rated generics and authorized generics; interchangeable tier = interchangeable biosimilars; biosimilar tier = biosimilars |

Stored once per group, not per state, so the lists are national.

### C5. `products_us` and `products_states` → `outputs/dashboard/products_us_2025.csv` (3,276 rows), `products_states_2025.csv` (77,585 rows)
Feeds the ingredient popup on the Rankings tab.

**Joins:**

| Left | Join | Right | Condition |
|---|---|---|---|
| SDUD summed by `ndc` (national as `geo = 'US'`, plus each state) | INNER JOIN | `crosswalk_ndc` | `sdud.ndc = crosswalk.ndc` |
| (result) | INNER JOIN | `product_type_ndc_2025` | `sdud.ndc = product_type.ndc` |
| (result, grouped) | INNER JOIN | `rankings_2025.csv` (distinct `state`, `ingredient`) | `geo = rankings.state AND ingredient = rankings.ingredient` (only ranked ingredients are kept) |

**Grain and cap:**
- One row per `geo` × `ingredient` × `product` × `labeler` × `product_type`. A product is one name from one manufacturer, all strengths and packages combined.
- Every product is kept, nationally and for each state (D-024; until 2026-11-05 state rows kept the top 12 by spending and grouped the rest).

| Field | Comes from |
|---|---|
| `geo` | `US` or the state code |
| `ingredient` | `crosswalk_ndc.ingredient_name` |
| `product` | `fda_ndc.proprietary_name`, else `mdrp.mdrp_product_name`, else SDUD `product_name`, else "Unnamed product, NDC " + the labeler-product code (e.g. `00536-1353`) |
| `labeler` | `fda_ndc.labeler_name` (package or product match), else `mdrp.mdrp_labeler_name`, else "Unknown labeler" |
| `product_type` | `product_type_ndc_2025.product_type` |
| `forms` | distinct `fda_ndc.dosage_form` values |
| `unit_type` | distinct `mdrp.mdrp_unit_type` values |
| `ndcs` | count of NDCs in the product |
| `prescriptions`, `medicaid_amount` | SDUD sums |
| `cost_per_prescription` | `medicaid_amount ÷ prescriptions` |
| `cost_per_unit` | `medicaid_amount ÷ SDUD units_reimbursed`, only when the product has a single unit type |

### C5b. `product_ndcs` → `outputs/dashboard/product_ndcs_{year}.csv` (6,193 rows)
The NDCs column in the ingredient popup.

| Left | Join | Right | Condition |
|---|---|---|---|
| SDUD national, summed by `ndc` | INNER JOIN | `product_type_ndc_2025` | `sdud.ndc = product_type.ndc` |
| (result, grouped) | INNER JOIN | `products_us` ∪ `products_states` (distinct product, labeler, product_type) | all three columns equal |

| Field | Comes from |
|---|---|
| `product`, `labeler`, `product_type` | Same naming rules as C5 (the popup row key) |
| `n_ndcs` | Count of NDCs in the product |
| `ndcs` | 11-digit NDCs as plain digits like SDUD (`00169413013`), space-separated, highest national Medicaid spending first. National rows, so a state view shows the product's NDCs nationally. |

### C6. `indications` → `outputs/dashboard/indications_2025.csv` (5,785 rows)
Hover text on product names in the ingredient popup.

**Step 1 (`src/indications.py` → `reference/label_indications`):** one row per 9-digit product code. The first join that finds a label wins:

| Order | Left | | Right |
|---|---|---|---|
| 1 | `fda_ndc.spl_id` (for codes `left(fda_ndc.ndc, 9)`) | = | openFDA label `id` |
| 2 | `left(sdud.ndc, 9)` | = | `openfda.product_ndc`, padded to 9 digits |
| 3 | `left(sdud.ndc, 9)` | = | NDCs printed in `package_label_principal_display_panel` |

Within a step, the newest `effective_time` wins.

**Step 2 (`export_indications`):** each popup product (`product`, `labeler`) takes the label of its 9-digit code with the most Medicaid spending:
- Join: `left(sdud.ndc, 9) = label_indications.product9`.
- Kept: only products that appear in `products_us` or `products_states`.

| Field | Comes from |
|---|---|
| `product`, `labeler` | Same expressions as C5 (shared constants) |
| `indication` | Label text, cleaned and trimmed to ~600 characters |
| `label_brand_name` | `openfda.brand_name`, else `spl_product_data_elements` |
| `set_id`, `effective_time` | The label |

### C7. Explore Alternatives: `explore_index`, `explore_groups`, `explore_status` (`src/explore.py`)

**`explore_groups` (22,827 rows):** one row per `group_key` × product × labeler × product_type × role, national 2025.

| Left | Join | Right | Condition |
|---|---|---|---|
| SDUD national, summed by `ndc` | INNER JOIN | `product_type_ndc_2025` | `sdud.ndc = product_type.ndc` |
| (result) | INNER JOIN | `equivalence_ndc_2025` | `sdud.ndc = equivalence.ndc` |

Fields:
- `group_key` = `equivalence.primary_key`; `presentation` = `equivalence.group_name`.
- `product`, `labeler`: same naming rules as C5.
- `role`, `unit_type`: from the equivalence table; `units` = SDUD units × `unit_factor` (D-027).
- `units`, `prescriptions`, `medicaid_amount`: SDUD sums.
- `ndcs`: the row's NDCs as 11 plain digits, space-separated, highest national Medicaid spending first.
- `cost_per_unit`, `cost_per_prescription`: calculated from those sums.

**`explore_index` (13,217 rows):** the same base, grouped by product × labeler. Includes `ingredient`, `product_type`, `modality`, national totals and the number of presentations.

**`explore_states_{1..4}` (about 700k rows in total, 13 states per file):** state figures keyed by row ids into the national tables. `pid` points to `explore_index`, `gid` to `explore_groups`, `sid` to `explore_status`.

| kind | Source | Join | Fields |
|---|---|---|---|
| `p` | SDUD state rows ⨝ `product_type` (on `ndc`), grouped by state × product × labeler | `(product, labeler)` = `explore_index` → `pid` | `rx`, `amt` |
| `g` | Same rows ⨝ `equivalence` (on `ndc`), grouped by state × group × product × labeler × type × role | those five columns = `explore_groups` → `gid` | `rx`, `amt`, `units` |
| `s` | `outputs/savings_2025` (state geos, primary, best tier per group) | `group_key` = `explore_status.group_key` → `sid` | `gross`, `min_rebate`, `avg_rebate`, `reverse`, `preferred_here` |

File number: the state codes sorted alphabetically, split into 4 groups of 13. The page uses the same rule.

**`explore_status` (3,717 rows):** one row per group that contains a brand or reference product.

| Left | Join | Right | Condition |
|---|---|---|---|
| group's top-spending brand `application_no` | lookup | Orange Book `products.txt` (NDA rows) | `application_no = lpad(Appl_No, 6)` → NDA's (`Ingredient`, `DF;Route`) |
| NDA (`Ingredient`, `DF;Route`) | lookup | Orange Book ANDA rows with `TE_Code` starting "A" | same `Ingredient` + `DF;Route` → `first_generic_approval` |
| `application_no` | lookup | Orange Book `patent.txt` (not delisted, unexpired) | `Appl_No` → `latest_patent_expiry`, `n_patents` |
| build date | constant | `date.today()` when src.explore runs | `as_of`: the date "unexpired" is measured against (patents and exclusivities); shown on the page |
| `application_no` | lookup | Orange Book `exclusivity.txt` (unexpired) | `Appl_No` → `exclusivities` |
| `application_no` (BLA) | lookup | Purple Book 351(a) rows | `BLA Number` → `ref_exclusivity`, `orphan_exclusivity` |
| reference BLA names | name match | Purple Book 351(k) rows | `Ref. Product Proprietary Name` in the BLA's proprietary names → `biosimilars_licensed` |
| `group_key` | LEFT JOIN | `outputs/savings_2025` (US, primary, best tier) | `group_key = savings.group_key` → savings figures, brand-preferred states |

---

## Part D. Join coverage and weak points

| Join | Matched | Not matched |
|---|---|---|
| SDUD `ndc` → `fda_ndc.ndc` (package) | 99.46% of spending | Inner unit NDCs and old NDCs |
| …then `left(ndc, 9)` → `fda_product.product9` | +$559M (5,646 NDCs) | 1,467 NDCs, $45M |
| SDUD `ndc` → `mdrp.ndc` | 99.96% of spending | — |
| Either FDA or MDRP | 99.99% of spending | 1,355 NDCs, classified "unclassified" |
| `application_no` → `purple_book.bla_no` | All BLAs but 1 | 1 BLA, $0 |
| SDUD `ndc` → RxNav ingredient | 99.83% of spending | Vitamins, IV fluids, supplies |
| Ingredient → ATC | 99.57% of spending | 2024–25 approvals not yet in RxClass |
| Popup product → FDA label | 99.1% of national popup spending | Plasma-derived products (clotting factors, immune globulin) |

The only joins that match on text instead of an ID:
- the state brand-preferred lists (`proprietary_name` vs `brand_name`);
- the transitioned-biologic patterns;
- the biosimilar presentation match, through RxNorm names with the suffix removed.
