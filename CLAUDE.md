# CLAUDE.md — SOTA summit RF-source mapper

## What this project does
Catalogs the fixed RF sources on/near SOTA summits by spatially joining the SOTA
summit list against three public FCC datasets, **merged**:
- **FCC ASR** (`r_tower.zip`) → registered antenna structures (towers/masts):
  location, owner, height, structure type.
- **FCC ULS Land Mobile + Microwave** (`l_LMcomm.zip`, `l_LMpriv.zip`,
  `l_micro.zip`) → licensed transmitters: location, owner, **frequency + power
  (ERP)**. Microwave shares the ULS record layout (drop-in) and adds fixed
  point-to-point paths (backhaul/STL) — freq + location but no power.
- **FCC CDBS media** (`facility.zip`, `fm_eng_data.zip`, `tv_eng_data.zip`,
  `am_eng_data.zip`, `am_ant_sys.zip`) → **broadcast FM/TV/AM** stations:
  location, callsign, **frequency/channel + ERP (the high-power offenders)**.

Goal: US first, association by association. Use case is knowing the RF
environment of a summit (receiver overload/desense risk when activating a peak
co-sited with broadcast/public-safety masts).

## How to run
```bash
pip install -r requirements.txt
python3 sota_rf_sources.py --association W6 --radius 1000   # proof-of-concept
python3 sota_rf_sources.py --us --radius 1000               # all US (eventual goal)
```
Outputs (prefixed by association or `US`): `*_rf_sources.csv` (one row per
summit×source), `*_summit_summary.csv` (per-summit counts + nearest),
`*_rf_sources.geojson` (for QGIS / geojson.io), and two **CalTopo** layers
(simplestyle GeoJSON, direct-import): `*_summits_caltopo.geojson` (one pin per
summit with sources, `marker-color` by overload risk, popup = per-band risk
analysis as plain text) and `*_sources_caltopo.geojson` (one pin per source,
`marker-color` by ERP heat). CalTopo popups are plain-text only (no HTML/img/
links), so the analysis is compressed into text fields + a `description` blob.

Self-test without downloading hundreds of MB (writes fixtures, then asserts the
loaders + broadcast kW→W / channel→freq / status filtering are correct):
```bash
python3 selftest_fixtures.py        # writes fixtures/ AND runs broadcast self-check asserts
python3 sota_rf_sources.py --association W6 --radius 2000 --out-dir testout \
  --summits-file fixtures/summitslist.csv \
  --asr-file fixtures/r_tower.zip --uls-file fixtures/l_LMcomm.zip \
  --broadcast-dir fixtures
```
Layer toggles: `--no-uls` (ASR only), `--no-broadcast` (skip FM/TV/AM).

## Architecture (all in `sota_rf_sources.py`)
- `load_summits()` — parse SOTA `summitslist.csv` (skips the version line, finds
  the `SummitCode` header), filter by association prefix.
- `load_asr()` — parse ASR CO/RA/EN records; keep active towers (constructed,
  not dismantled) that have surface coordinates.
- `load_uls()` — parse ULS HD/EN/LO/FR; keep active licenses; aggregate
  frequencies and max power per location. Generic over its list of zips, so
  land-mobile (`l_LMcomm`/`l_LMpriv`) and microwave (`l_micro`) all flow through
  it; microwave FR records carry a freq but **blank power** (→ NaN `max_power_w`,
  which is correct — a 6/11/18 GHz backhaul path is a freq+location fact), and
  its service codes (CF/MG/MW/TI/TS…) distinguish it in the `services` column.
- `load_broadcast()` — parse CDBS `facility` (identity/service/status) + the
  per-service engineering tables; keep current (`eng_record_type='C'`) records
  for licensed (`fac_status='LICEN'`) facilities, one (highest-ERP) per facility.
  Emits the same unified columns. **ERP is in kW in CDBS → stored as watts** so
  broadcast dwarfs land-mobile, which is the point. Gotchas baked in: FM ERP
  lives in `horiz_erp`/`vert_erp` (not `effective_erp`, which is blank); AM
  coords+power are in `am_ant_sys` keyed by `application_id`, joined to a
  facility via `am_eng_data`; TV center freq is derived from the RF channel
  (`tv_channel_center_mhz`, channels 2–36 only — legacy/display channels >36 get
  blank freq rather than a now-wrong one); `asrn` becomes `rf_link_reg` (ties a
  broadcast tx to its ASR tower, same as ULS).
- `merge_sources()` — unify ASR + ULS + broadcast into one RF table. **Merge
  key:** ULS `LO` records (and broadcast `asrn`) carry a tower registration
  number, surfaced as `rf_link_reg`; it matches an ASR registration number, so a
  transmitter is tied to the physical tower it sits on.
- `spatial_join()` — sklearn `BallTree` haversine per summit. Drops
  missing/out-of-range coords before indexing. **Field-strength inclusion:** a
  source is kept if it's within the base `--radius` (near-field, any power) OR
  its received-power proxy `ERP/d²` clears `FIELD_THRESHOLD_W_M2` (0.01 W/m² ≈ a
  10 kW tx at 1 km), out to a computed max radius. So a Sutro-class 1 MW mast is
  captured ~10 km out while distant low-power land-mobile is not — a flat radius
  can't do both. The popup tags any source beyond the base radius with its
  distance (`(1.9km)`), and near/far sources cluster separately so a far mast is
  never hidden inside a co-sited near line.

**FCC raw-record field indices are defined as dicts at the top of the script**
(`CO`, `RA`, `EN_ASR`, `HD`, `EN_ULS`, `LO`, `FR` for ASR/ULS; `FAC`, `FM_ENG`,
`TV_ENG`, `AM_ANT`, `AM_ENG` for broadcast). Edit there if a layout changes.
ULS/ASR files are pipe-delimited, no headers, cp1252, with stray CR/LF inside
records (handled by `fix_lines()`). CDBS files share that format; their live
column counts run a few wider than the published DDL (FCC appends new fields at
the end), but the indices above are verified against the real files.

## Current status
- Pipeline built and **verified against synthetic fixtures** (parsing, the
  ASR↔ULS merge, radius exclusion, dismantled/cancelled filtering all correct).
- **Run live against full FCC/SOTA data (W6, radius 1000 m) — works.** 4329 W6
  summits vs 772,009 RF sources (152,205 ASR + 619,804 ULS) → 6,977 summit×source
  hits; 490 summits have ≥1 source. Sanity-checked OK: the known RF-hot peaks all
  surfaced (Lukens 358, Santiago 246, Vaca 158, Diablo 114, San Bruno 70, Loma
  Prieta 58 w/ KNTV, Tam 45), frequencies/powers/owners are plausible (800 MHz
  public-safety, CHP low-band VHF, State of California, etc.), and 796 ULS hits
  linked to an ASR tower via `rf_link_reg`.
- **Live data exposed two coordinate bugs (now fixed):** (1) blank ULS
  minute/second fields poisoned the whole coord because `np.nan or 0` is `np.nan`
  (NaN is truthy) — fixed with a `nz()` helper; (2) a defensive coord-validity
  filter in `spatial_join()` now drops missing/out-of-range lat/lon (live ULS had
  a lat=93 garbage record) before `BallTree`, which otherwise crashes on NaN.
- Downloading: FCC `urllib` download hits its 120 s read timeout on the big zips;
  fetch them with `curl -C -` into `fccdata/` and run with `--no-download`.
- Env: conda env `sota-rf` (python 3.12, pandas/numpy/scikit-learn). Interpreter:
  `~/miniconda3/envs/sota-rf/bin/python` (the shell `conda activate` isn't wired
  up in non-interactive subshells — call that path directly).
- **Microwave layer added (roadmap #2) — drop-in, self-tested, run live.**
  `l_micro.zip` (226 MB) added to `URLS["uls"]`; same HD/EN/LO/FR layout so
  `load_uls()` was unchanged. W6 live: ULS locations 619,804 → **1,043,082**
  (+423k microwave paths), total sources 802,704 → **1,225,982**, W6 hits
  7,466 → **15,580**. Sanity-checked OK: microwave-band (>900 MHz) hits carry
  6/11/18/19 GHz backhaul freqs, service codes CF/MG/MW/TI/TS, real licensees
  (T-Mobile MW LLC backhaul, broadcast STLs), and power is blank 95.7% of the
  time (correct — microwave FR has no power field). Prominent relay peaks
  (Woodson, Box Springs, San Miguel, Verdugo) rose up the hot-summit list.
- **Broadcast FM/TV/AM layer added (roadmap #3) — built, self-tested, run live.**
  30,695 licensed CDBS facilities (FM 19,407 + TV 6,903 + AM 4,385) folded into
  the W6 join → 489 broadcast summit-hits. Sanity-checked OK: full-power TV
  (1,000,000 W) lands on the right major sites (San Bruno Mtn, Shasta Bally,
  Fremont Peak, Mount Allison), all FM freqs fall in 88–108 MHz, ERP spans
  1 W translators → 1 MW TV. Self-test asserts kW→W, TV channel→freq, AM
  kHz→MHz, and the LICEN/`'C'` filtering. Source choice: **CDBS** (scriptable,
  frozen ~2024-01) over **LMS** (current but Akamai-bot-blocked against scripted
  download); good for the long-lived high-ERP incumbents, stale for new low-power.
- **ULS power sanity-cap added (roadmap #6) — DONE.** Some ULS `power_erp`
  values are garbage (NBCUniversal 9.24 GHz STL at 5 MW, an 11.7 MW record at Oat
  Mtn). The W6 distribution is decisive: legit ULS ERP tops out at **3,500 W**,
  then a huge gap to the 4 bogus megawatt values; broadcast megawatts come from
  CDBS, never ULS. `load_uls()` now drops any ULS reading > `ULS_POWER_CAP_W`
  (1 MW) → NaN, so Mt Lukens' total falls from a garbage 5.24 MW to a real
  237 kW. Self-tested (garbage 10 MW dropped, legit 250 W kept).
- Note: the CalTopo **summit-risk** score was already robust to these —
  Oat Mtn's bogus 11.7 MW reads MODERATE, not HIGH, because the garbage STL's
  frequency doesn't fall in any ham-band window (the score is Σ ERP/d² within a
  ±octave of each ham band, so distance + band-relevance dilute the outlier).
- **CalTopo layers added — built, self-tested, verified on real W6 join.**
  `to_caltopo_summits()` / `to_caltopo_sources()` emit direct-import simplestyle
  GeoJSON. Summit layer: 576 W6 summits (317 CLEAR · 101 MODERATE · 83 HIGH · 75
  LOW); the HIGH shortlist is the known-hot roster (San Miguel, Lukens, Cahto,
  San Bruno, Fremont Pk, Shasta Bally, Mt Allison). Risk = estimated **field
  strength** `E = √(30·Σ ERP/d²)` V/m (incoherent free-space sum; counted once
  per source per band, never per-frequency). The summit's **overall** tier uses
  the total field over ALL sources (broadband front-end overload); each ham
  band's QRM level uses the in-±octave field. Tiers in V/m: HIGH≥10 / MOD≥3 /
  CLEAR (`FIELD_*_VM`) — **re-anchored to receiver desense** (not arbitrary):
  the field at which an activator loses a weak **S5 (≈ −117 dBm) FM signal**,
  graded by radio quality. **MODERATE 0.15 V/m** = blocks S5 on a cheap wideband
  HT (Quansheng/Baofeng, ~−20 dBm overload onset); **HIGH 1.5 V/m** = blocks S5
  even on a decent radio (~20 dB better); **LOW 0.05** = Quansheng degraded but
  S5 copyable; CLEAR = even a Quansheng is fine. Derivation in `_desense_field()`.
  These are the **physical** field, so the old 9× broadcast desense weight is
  retired (`BROADCAST_HAM_WEIGHT`=1) — a low physical threshold already makes
  broadcast dominate. Known-hot peaks land HIGH (San Bruno 48, Richardson 30,
  Occidental 10, Mt Davidson 8.6 V/m); blank-freq UHF-TV (ch>36) is scored in the
  70cm octave.
  **Broadcast desense weight (`BROADCAST_HAM_WEIGHT`=9):** FM/TV/AM ERP/d² is
  multiplied by 9 (≈3× field) in the score — a continuous megawatt carrier
  desenses cheap ham front-ends far worse per watt than land-mobile. Applied to
  BOTH the per-band and total field (so per-band stays ≤ overall). Fixes a
  false-quiet: Occidental's 418 kW FM 1.2 km off (on Mt Wilson) read LOW on
  2m/6m; now MODERATE, matching real front-end overload from a broadcast farm a
  km away — while co-sited FM summits (San Bruno) stay HIGH and the overall map
  barely shifts. Drives the **marker colour**.
  **Vertical antenna pattern (`_vpat_gain`, FM only):** broadcast ERP is the
  main-beam (horizon) value; a summit a few degrees off that beam sees less. FM
  `num_sections`/`spacing` (bays + bay-spacing in λ) come straight from CDBS
  `fm_eng_data`, giving an N-bay array-factor × cos-element pattern. The gain
  de-rates a source's ERP/d² by its relative power toward the summit's elevation
  angle `atan((rc_amsl−summit_alt)/d)`. Two guards keep it physical: a
  **near-field/co-location floor** (`max(base_radius, Rayleigh 2D²/λ)`) — a mast
  within the near zone blasts a co-located operator regardless of beam direction,
  so it keeps full ERP (this is why co-sited broadcast summits stay HIGH) — and a
  **null-fill floor** (`VPAT_NULL_FILL`=0.15) since real masts fill their nulls
  to serve the city. Verified against the Sutro/San Bruno→Mt Davidson field study
  (KOIT ERI SHP-6, KIOI Jampro). **Low-yield on the tier map** (national: 3/3547
  summits shift, all downward) because risk-dominating broadcast is either
  co-sited (near-field, full power) or far-but-near-horizon (in-beam), and TV
  (biggest ERP) has no bay data in CDBS → gain 1. Kept for correctness, a
  pattern-aware report card, and to plumb bays/spacing for the microwave
  beam-axis test. TV/AM/land-mobile → gain 1 (no data / omni).
  CalTopo constraints discovered the hard way: (1) popups are **plain-text only**
  (no HTML/img/clickable links, per CalTopo's help forum), so the rich
  report-card mockup can't live *in* CalTopo — it's a browser companion; (2)
  `marker-symbol` must be from CalTopo's honoured set — my first cut used
  `"triangle"` and it rendered as giant hollow shapes that weren't clickable
  (no popup). Fixed by matching `sota-wfs`'s verified styling (loaders.py):
  `marker-symbol: "point"` (small solid dot), `marker-color: "#hex"`, no
  `marker-size`. **Popup content** (per user feedback): terse — full name, total
  ERP, then the ERP broken down by **transmit band** (`88-108 MHz FM 425 kW`,
  biggest first), *not* by ham band; on-map label is the compact SOTA code.
  Bands are `SERVICE_BANDS` **split at every `HAM_ALLOC` edge** (`_build_rf_bins`)
  so a range never spans a ham band; a source inside a ham band is called out
  under the ham label (`144-148 MHz 2m`). Within each fixed segment the actual
  source freqs are **clustered** (`_cluster_freqs`, single-linkage at
  `CLUSTER_RATIO`=10%) so a tight land-mobile group shows at its real footprint
  (`151-159 MHz VHF 200 W`) rather than the whole segment; power is summed once
  per licence. Powered-but-unbinnable ERP (TV ch>36, blank freq) → `unknown
  freq` line.

## Data sources beyond FCC
- **NOAA Weather Radio (NWR) — DONE.** Federal (NWS/NTIA), so absent from all FCC
  data — a 162.400–162.550 MHz blind spot beside 2 m. `load_noaa()` parses the
  NWS's own `ccl-data.js` (callsign, decimal lat/lon, freq, power W; `source_db=NOAA`,
  `services=NWR`). ~1014 live tx; `--no-noaa`/`--noaa-file` toggle; fetched monthly.
  Verified: KWO37 sits on Mt Lukens (121 m) → drives its 2 m QRM to High.
- **Federal blind-spot survey (for future work).** Next best add = **FAA NAVAIDs**
  (VOR/VORTAC/TACAN/DME/ILS) via the FAA **NASR** 28-day nav file — public, has
  coords+freq+class, mountaintop-sited; DME/TACAN 960–1215 MHz sits by 23 cm.
  Optional: NEXRAD/ASR/ARSR radar (public, high-power, band-distant). **Irreducible
  gaps** (NTIA Government Master File is restricted): military/DoD and federal
  land-mobile (USFS/BLM/NPS/DHS) — document, never imply "nothing here."

## Roadmap / next tasks
1. ~~**Run W6 live** and sanity-check.~~ **DONE** — see Current status.
2. ~~**Add microwave**~~ **DONE** — `l_micro.zip` added to `URLS["uls"]`; it
   shares the ULS HD/EN/LO/FR layout so `load_uls()` handled it unchanged.
   Verified live on W6 (see Current status) and self-tested (blank-power → NaN).
3. ~~**Add broadcast FM/TV/AM**~~ **DONE** via `load_broadcast()` (CDBS media).
   Future: enrich with licensee/owner name (needs an `app_party`/parties join;
   currently `owner` = callsign + community), and optionally an LMS loader for
   current data once a non-bot-blocked download path is found.
4. ~~**Full `--us` run**~~ **DONE.** Generating the national map surfaced a
   coverage bug: `US_PREFIXES`/matching only caught single-association regions,
   dropping all lettered sub-associations (W7A, W0C, W4G, KLA…) — the whole
   mountain West, Colorado, Southeast, Alaska. Fixed (match the association part
   vs W/KH/KL/KP). National run: **50,950 US summits** (was a broken 6,688) vs
   1.23 M sources → 61,739 near + 855 far hits; **3,547 impacted** (415 HIGH ·
   552 MOD · 537 LOW · 2,043 CLEAR). Validated by the marquee Western broadcast
   peaks finally appearing — Mount Ord (Phoenix), Sandia Crest (Albuquerque),
   Farnsworth Pk (Salt Lake). Ran in minutes here, so the monthly deploy rebuild
   is light, not heavy. Sources layer is 26 MB / 59 k pts → WFS-only, not import.
5. Optional: collapse ULS-on-ASR matches (`rf_link_reg`) into single structure
   rows that list their frequencies inline, for a cleaner per-structure view.
6. ~~Sanity-cap absurd ULS `power_erp` outliers.~~ **DONE** — `load_uls()` drops
   ULS ERP > `ULS_POWER_CAP_W` (1 MW); see Current status.
7. ~~**CalTopo layers**~~ **DONE (first pass)** — direct-import simplestyle
   GeoJSON (`to_caltopo_summits` / `to_caltopo_sources`).
   - (a) ~~serve live via WFS + scheduled refresh~~ **DEPLOY PACKAGE BUILT** (not
     run here — targets the `jeffkowalski/sota-wfs` box). Mirrors that repo's
     fetch→loader→registry→systemd pattern: `fetch/fetch_rf_sources.py`
     (conditional GET on the ULS/ASR completes via `curl -z`/Last-Modified,
     CDBS fetched-once/frozen, rebuild, atomic swap into `data/rf_summits.geojson`),
     `systemd/fetch-rf-sources.{service,timer}` (monthly `oneshot`), and
     `DEPLOY_SOTA_WFS.md` (the `rf_geojson_loader` + `RF_Sources` `Layer` to add,
     deps, install). Download logic smoke-tested against live FCC.
   - (b) ~~standalone **RF report-card** web page~~ **DONE** — `write_report_card()`
     + `--report <SUMMIT>` emit a self-contained per-summit HTML (spectrum:
     band-power bars + per-emitter scatter + ham-band overload markers, plus a
     scrollable source table) from `report_template.html`, with a **data-provenance
     footer** — `data_provenance()` reads each FCC zip's internal generation date
     + the SOTA list's header date (also printed as a "data as of" line in the run
     log). The browser deep-dive CalTopo can't host. (c) tune risk thresholds /
     octave window with ground truth;
     (d) true per-record delta updates (FCC daily transaction files → a
     persistent datastore) if the monthly full pull is too heavy.
8. **Directional / propagation realism (from the Mt Davidson field study).** The
   field model was `ERP/d²`; the study showed two physical corrections matter.
   - (a) ~~**Vertical antenna pattern** (Phase A)~~ **DONE** — `_vpat_gain`,
     see the scoring notes above. FM bays/spacing from CDBS; near-field +
     null-fill guards; low-yield on tiers (3/3547) but grounds the report card
     and plumbs bays/spacing. Zero new data (rides the existing CDBS parse).
   - (b) ~~**Terrain LOS / diffraction** (Phase B)~~ **DONE.** `dem_terrain.py`
     (SRTM-30 m skadi tiles, no auth) + `apply_terrain()`: ITU-R P.526 knife-edge
     diffraction over the effective-Earth profile de-rates each *far* pair's
     ERP/d² (near/co-sited = LOS by definition). Result is fixed geometry →
     cached by `(summit, source)` in a JSON keyed cache; **built locally where the
     DEM lives (`--dem-dir --terrain-cache`), shipped by rsync to the droplet's
     `data/terrain_cache.json`**, which the pipeline reads **read-only** (no DEM
     on the box; uncached pairs = clear LOS, the safe default). National: 1052 of
     8004 far pairs shadowed >6 dB, **26 summits pruned downward** (0 up). Monthly
     droplet rebuild applies it from the cache at zero extra fetch; I refresh the
     cache locally when sources move. Validated on Diablo/Tam (shadowed) and the
     photo-confirmed Mt Davidson←Sutro (clear).
   - (c) **Microwave beam-axis test** (Phase C) — point-to-point paths are pencil
     beams; test whether the summit is near the beam axis (from the ULS path
     endpoints) to prune the ~423 k microwave hits, most of which are off-beam.
   - (d) Azimuth (directional) FM/TV patterns; optional exact LMS-filed elevation
     patterns (beam tilt + null-fill) for the high-value stations.

## Known gaps / gotchas
- ASR only requires registration above ~200 ft AGL (or near airports) — short
  structures may be absent.
- **Cellular** is geo-licensed, so exact site coords aren't in ULS; the physical
  towers still appear via ASR.
- Not every ASR record has surface coordinates (those are skipped).
- FCC "complete" snapshots refresh weekly — re-download for current data.
- Heights in ASR/FCC are metres.

## Conventions
- Keep all raw-record field indices in the dicts at the top of the script.
- Downloaded data and outputs are gitignored (`fccdata/`, `testout/`,
  `fixtures/`, `*.zip`, `*_rf_sources.*`, `*_summit_summary.csv`).
