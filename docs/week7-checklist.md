# Week 7: Power BI report checklist

> Design and decisions: [ADR 0007](adr/0007-power-bi-report.md).

## A. Connection
- [x] Dedicated Power BI token (scope `sql`, 30 days), separate from the dbt token
- [x] Databricks connector, Import mode; host and HTTP path as Power Query parameters
- [x] Minimal slice: fixed speed by income group, 2026 Q3, equal to SQL (329.92 / 199.54 / 77.93 / 44.50)

## B. PBIP and export
- [x] Saved as a Power BI project (TMDL + PBIR), working copy outside the repository
- [x] Auto date/time and relationship autodetection off
- [x] `tools/export_pbip.py`: allowlist, placeholders, scan, atomic replace; 15 unit tests (71 in total)
- [x] `.gitleaks.toml`: rule for Databricks workspace hosts; `.gitignore`: `reporting/powerbi/**/.pbi/`
- [x] First export committed and pushed; gitleaks: no leaks; CI green

## C. Semantic model
- [x] 12 tables (9 Gold + `dim_year`, `dim_connection`, `dim_group` in Power Query), 14 many-to-one relationships
- [x] 37 measures: sum ÷ sum speeds, coverage rule (≥ 90% of population), low-sample flags, "latest" values found by the model
- [x] `avg_*`, `sum_*` and key columns hidden; English display names
- [x] World, ratio, Dutch and internet-use values equal to SQL

## D. Pages
- [x] Overview: six KPI cards, internet use by region
- [x] Global: internet use by region over time, GDP × speed scatter (log scale), fastest and slowest 10
- [x] Income groups: classification and connection slicers, internet use and speed by income group, ratio card; 2005 high-income 61.0 / 52.7 checked
- [x] Methods: sources and licences, calculation rules, limitations, pipeline diagram
- [x] Netherlands: `tools/build_maps.py nl` and a Shape map of municipality fixed speed (clockwise rings for d3), colours around the Dutch average, province slicer, fastest and slowest 15, fixed and mobile by quarter; Ameland 116.8, Sluis 192.0, Lelystad 368.3 and both tables checked
- [x] Theme fonts (text 12, titles 14, cards 28), KPI cards not cross-filtered
- [x] Income groups chart subtitles explain the method; scatter plot leaves out countries without an FY27 income group

## E. Checks waiting for the warehouse (done 2026-11-02, results in ADR 0007)
- [x] Fastest and slowest 10 countries (all 20 equal) and the number of scatter points (182 by SQL)
- [x] Mobile speed by income group, 2026 Q3 (43.2 / 102.0 / 127.6 / 192.7)
- [x] Countries that changed income group in FY27 (drop in lower-middle-income speed in 2026 Q3)
- [x] West Bank and Gaza: fixed speed far above what its GDP suggests; check which tests fall inside its boundary
- [x] The blank income group in the scatter plot: 21 units outside WDI (e.g. Taiwan), with speed but no GDP or income group

## F. Documentation and repo
- [x] ADR 0007; ADR 0001 points to it
- [x] README: how to open the report, repository layout
- [x] Page screenshots in the README (rendered from a PDF export, so no Desktop window or account is shown)
- [x] PDF export in the repository (`reporting/powerbi/ddlake-report.pdf`, five pages); five page screenshots rendered from it
- [x] Data and world map for a gender page (`mart_country_year` female and male internet use, `tools/build_maps.py world`); the page itself is left for the next version
- [ ] Export, scan, commit, local gitleaks scan, push, CI
