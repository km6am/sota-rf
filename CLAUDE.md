# CLAUDE.md — SOTA summit RF-source mapper

## What this project does
Catalogs the fixed RF sources on/near SOTA summits by spatially joining the SOTA
summit list against three public FCC datasets, **merged**:
- **FCC ASR** (`r_tower.zip`) → registered antenna structures (towers/masts):
  location, owner, height, structure type.
- **FCC ULS Land Mobile** (`l_LMcomm.zip`, `l_LMpriv.zip`) → licensed
  transmitters: location, owner, **frequency + power (ERP)**.
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
summit×source), `*_summit_summary.csv` (per-summit counts + nearest), and
`*_rf_sources.geojson` (for QGIS / geojson.io).

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
  frequencies and max power per location.
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
- `spatial_join()` — sklearn `BallTree` haversine, `query_radius` per summit.
  Drops sources with missing/out-of-range coords before indexing.

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
- Env: conda env `sota-rf` (python 3.12, pandas/numpy/scikit-learn).
- **Broadcast FM/TV/AM layer added (roadmap #3) — built, self-tested, run live.**
  30,695 licensed CDBS facilities (FM 19,407 + TV 6,903 + AM 4,385) folded into
  the W6 join → 489 broadcast summit-hits. Sanity-checked OK: full-power TV
  (1,000,000 W) lands on the right major sites (San Bruno Mtn, Shasta Bally,
  Fremont Peak, Mount Allison), all FM freqs fall in 88–108 MHz, ERP spans
  1 W translators → 1 MW TV. Self-test asserts kW→W, TV channel→freq, AM
  kHz→MHz, and the LICEN/`'C'` filtering. Source choice: **CDBS** (scriptable,
  frozen ~2024-01) over **LMS** (current but Akamai-bot-blocked against scripted
  download); good for the long-lived high-ERP incumbents, stale for new low-power.
- Pre-existing **ULS data-quality outlier** (not broadcast): some ULS `power_erp`
  values are garbage (e.g. NBCUniversal 9.24 GHz STL listed at 5,000,000 W at
  Mt Lukens). A future sanity-cap on ULS power would clean the `max_power_w`
  summary column.

## Roadmap / next tasks
1. ~~**Run W6 live** and sanity-check.~~ **DONE** — see Current status.
2. **Add microwave** — `l_micro.zip` uses the *same* HD/EN/LO/FR layout, so it's
   a drop-in: add its URL to `URLS["uls"]`.
3. ~~**Add broadcast FM/TV/AM**~~ **DONE** via `load_broadcast()` (CDBS media).
   Future: enrich with licensee/owner name (needs an `app_party`/parties join;
   currently `owner` = callsign + community), and optionally an LMS loader for
   current data once a non-bot-blocked download path is found.
4. **Full `--us` run** once layers are in.
5. Optional: collapse ULS-on-ASR matches (`rf_link_reg`) into single structure
   rows that list their frequencies inline, for a cleaner per-structure view.
6. Optional: sanity-cap absurd ULS `power_erp` outliers (see Current status).

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
