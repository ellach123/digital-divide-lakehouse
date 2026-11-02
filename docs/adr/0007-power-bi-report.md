# ADR 0007: Power BI report

- **Status:** Accepted (pages 1, 2, 3 and 5 validated 2026-11-01, the Netherlands page 2026-11-02)
- **Date:** 2026-11-01

## Context
Week 7 turns the Gold layer (ADR 0005) into a report. ADR 0001 planned Power BI Desktop, published as "a `.pbix` file plus screenshots", so that no Pro licence is needed. Constraints stay the same: under €1 in total, a public repository with no environment identifiers, and Databricks Free Edition, whose SQL warehouse is shared and not always available.

## Decision

### Storage mode and connection
- **Import mode.** The Gold tables are small (the largest has about 61,000 rows), the data change once a week, and an imported model keeps working when the warehouse is down. DirectQuery would need the warehouse for every click.
- The workspace host and the warehouse HTTP path are **Power Query parameters** (`DatabricksHost`, `HttpPath`). Every table query uses them, so these two values appear in one file only.
- A dedicated personal access token for Power BI (scope `sql`, 30 days), separate from the dbt token. It is entered in the Power BI credentials dialog only and stored by Power BI outside the project folder.

### PBIP instead of `.pbix` (changes ADR 0001)
The report is committed as a **Power BI project (PBIP)**: the semantic model as TMDL and the report as PBIR, both plain text.

- A `.pbix` is a compressed binary that also contains the imported data. It cannot be reviewed in a diff or scanned for a host name, and every change rewrites the whole file.
- PBIP files can be diffed, reviewed and scanned like code, and the data cache (`.pbi/cache.abf`) is a separate file that is never committed.

Desktop works in a folder **outside the repository**, which holds the real parameter values, the data cache and per-user settings. `tools/export_pbip.py` writes the publishable copy to `reporting/powerbi/ddlake-report/`:
1. copy an **allowlist** of PBIP files only (no `.pbi/` folder; files Desktop may add in future are skipped by default);
2. replace the two parameter values with placeholders;
3. scan every file for e-mail addresses, workspace hosts, warehouse paths, Databricks tokens, SAS signatures, storage endpoints, the parameter values themselves, and GUIDs outside the lines where Power BI writes its own random ids;
4. replace the target folder only if the scan found nothing.

It has 15 unit tests. `.gitleaks.toml` adds a rule for Databricks workspace hosts, so the CI scan would also catch a host that slipped past the export.

### Semantic model
12 tables, 14 relationships, 37 measures:
- **9 Gold tables** (4 dimensions, 2 speed facts, 2 group aggregates, the country-year mart) and **3 small Power Query tables**: `dim_year`, `dim_connection` (fixed / mobile) and `dim_group`, built from the two aggregate tables. `dim_group` is built in Power Query, not as a DAX table, to avoid a circular dependency.
- `fct_indicator_country_year` is not loaded; country indicators come from the wide `mart_country_year`.
- All relationships are **many-to-one, single direction**, written explicitly in TMDL; automatic relationship detection and auto date/time are off.
- **Averages are never averaged.** The `avg_*` and `sum_*` columns are hidden; every speed measure divides a sum by a sum (`sum of tile average × tests` ÷ `tests`). Speed measures return blank when fixed and mobile are mixed, and group measures return blank unless exactly one group type is selected.
- **Coverage rule:** a group value (internet use, GDP) is shown only when reporting countries cover at least 90% of the group's population; a second measure shows the value including low coverage, for tooltips.
- **Low sample:** a country quarter below 1,000 tests and a municipality quarter below 100 tests are flagged; last-four-quarter country speeds need at least 4,000 tests.
- **"Latest" values are found by the measures** (latest quarter, latest year with a reliable value), so a refresh with new data moves every page forward. Typed by hand, and to be updated with new data: the GDP year of the scatter plot (2024, a constant in the model), and on the Netherlands map the quarter in the subtitle (2026 Q3) and the colour midpoint (324.9, the Dutch average).

### Income classification
Income groups default to the **classification of the year** (`income_as_was`, from the SCD2 history in ADR 0005); a slicer switches to today's FY27 list. The two can differ a lot: in 2005, 61.0% of people in countries that were high-income at the time were online, but 52.7% in today's high-income countries. Country-level pages use the FY27 list only and say so; the scatter plot leaves out countries without an FY27 income group.

### Pages
| Page | Question | Main visuals |
|---|---|---|
| Overview | Where does the world stand? | Six KPI cards (latest reliable internet use, world and Dutch fixed speed, high- vs low-income ratio, data cut-off), internet use by region |
| Global | How do countries compare? | Internet use by region over time, GDP per capita (log scale) × fixed speed scatter, fastest and slowest 10 countries |
| Income groups | Is the income gap closing? | Internet use by income group (2000–2024), fixed or mobile speed by income group (8 quarters), ratio card |
| Netherlands | Which municipalities have the fastest and the slowest broadband? | Shape map of municipality fixed speed, fastest and slowest 15, province slicer, fixed and mobile by quarter |
| Methods | How are the numbers made? | Sources, calculation rules, limitations, pipeline diagram |

The report is in English. Ookla's CC BY-NC-SA licence requires attribution, so the Methods page names every source and licence.

### Netherlands map
A Shape map of fixed download speed per municipality in the latest quarter. `tools/build_maps.py nl` downloads the boundaries from the same PDOK source as Notebook 32, simplifies them and writes `reporting/powerbi/maps/nl_municipalities.geojson` (342 municipalities, ~0.6 MB). The Shape map in Desktop 2.159 accepts GeoJSON (no TopoJSON step, no preview switch) and matches shapes by municipality name; all 342 names equal `dim_municipality`. The file has clockwise outer rings: the Shape map draws with d3, which fills the whole visual when a ring is counter-clockwise, the order PDOK uses (RFC 7946). Colours diverge around the Dutch average (324.9 Mbps, the Overview card): blue is faster, orange slower, orange and blue so that colour-blind readers can tell them apart; 205 of the 342 municipalities are below the average, because the large cities with many tests pull it up (the median municipality is at 315.4). Mobile speed is shown in the trend chart, not on the map: mobile has about 5% of the fixed tests.

### Gender page (next version)
The data and the map for a page on internet use by sex are in place; the page itself is left for the next version. `mart_country_year` now carries female and male internet use (WDI, ITU estimates), and `tools/build_maps.py world` writes `reporting/powerbi/maps/world_countries.geojson` from the same Natural Earth file as Notebook 34 (249 shapes, one per `dim_country` row, ~1.0 MB). The page will show each country's latest year from 2018 on (years differ: 115 of 217 economies, 80% of the population) and no regional averages: weighting them needs female and male population, which is not in the data (ADR 0005). Coverage is thinnest where gaps are likely largest: 13 of 48 economies in Sub-Saharan Africa.

## Consequences
- **Opening the repository copy shows no data until it is refreshed:** the cache is not committed. The reader sets the two parameters, enters their own token and refreshes (README, "How to open the report").
- The working copy outside the repository must be exported after every change; the export overwrites the repository copy completely, so the repository is never edited directly.
- **Free Edition availability is a real risk.** The warehouse could not start on 2026-10-21 and 10-22 (recovered by itself on 10-23) and again on 11-01, with no change on our side (the last query before the first failure was on 10-16). Import mode let report work continue on the cached data; only refreshes and SQL cross-checks had to wait. A paid warehouse would remove the risk but break the €1 cap (ADR 0001), so it was not considered.
- About 4,500 of the committed lines are Desktop's base theme JSON, which Desktop writes into every project, and about 17,000 more are Q&A synonyms that Desktop generates in `cultures/en-GB.tmdl` (table and column names only, no data values).
- Microsoft's documentation still describes the Shape map as a preview feature that has to be switched on; in Desktop 2.159 it is not.
- No publishing to the Power BI service: that needs a Pro licence (ADR 0001). Readers see screenshots and a PDF in the repository.

## Results (2026-10-24 to 2026-11-02)
Every value was checked against SQL on the Gold tables; all matched to the shown precision.

| Check | Power BI | SQL |
|---|---|---|
| Fixed download, 2026 Q3, by income group (high / upper middle / lower middle / low) | 329.9 / 199.5 / 77.9 / 44.5 Mbps | 329.92 / 199.54 / 77.93 / 44.50 |
| World fixed download, 2026 Q3 | 242.5 Mbps | 242.49 |
| High- vs low-income fixed speed ratio, 2026 Q3 | 7.4 × | 7.41 |
| Netherlands fixed download, 2026 Q3 | 324.9 Mbps | 324.94 |
| World internet use 2022 / 2023 / 2024 | 66.8 / 71.3 / 72.7 % | 66.82 / 71.30 / 72.67 |
| World internet use 2025 | hidden (coverage 36.7%) | 81.06, not reliable |
| High-income internet use 2005, classification of the year / FY27 | 61.0 / 52.7 % | 61.0 / 52.7 |
| Fastest and slowest 10 countries, fixed, last 4 quarters (at least 4,000 tests) | Singapore 611.8 … United States 385.2; Syria 15.8 … Congo, Rep. 28.3 | all 20 equal; 205 countries ranked |
| Mobile download, 2026 Q3, by income group (low / lower middle / upper middle / high) | 43.2 / 102.0 / 127.6 / 192.7 Mbps | 43.17 / 101.96 / 127.58 / 192.73 |
| Netherlands, fixed, 2026 Q3: Ameland / Sluis / Lelystad | 116.8 / 192.0 / 368.3 Mbps | 116.8 / 192.0 / 368.3 |
| Fastest and slowest 15 Dutch municipalities, fixed, 2026 Q3 | Enschede 398.3 … Terneuzen 363.6; Ameland 116.8 … Dantumadiel 233.7 | all 30 equal |

The SQL checks also explained three things seen in the report (2026-11-02):
- **Lower-middle-income speed drops from 109.8 to 77.9 Mbps between 2026 Q2 and Q3** with the classification of the time. FY27 took effect on 1 July and moved Jordan, the Philippines, Sri Lanka, Viet Nam and Micronesia up to upper middle income (Togo moved up into the group), so fast networks left the group. With today's list the series is smooth.
- **The blank income group in the scatter plot** is 21 units outside WDI (e.g. Taiwan, Somaliland) that have speed tests but no GDP or income group. They are filtered out of the plot; it shows 182 countries by SQL.
- **West Bank and Gaza: 251.8 Mbps fixed, far above what its GDP suggests.** Gaza has 12 tests in the last four quarters, so the value is the West Bank's. 38% of its tests come from tiles averaging at least 300 Mbps (398 Mbps on average); the other 62% average 162 Mbps. Country values follow Natural Earth's de facto boundaries, which the Methods page states.
