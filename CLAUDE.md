# CLAUDE.md — SOTA summit RF-source mapper

## What this project does
Catalogs the fixed RF sources on/near SOTA summits by spatially joining the SOTA
summit list against two public FCC datasets, **merged**:
- **FCC ASR** (`r_tower.zip`) → registered antenna structures (towers/masts):
  location, owner, height, structure type.
- **FCC ULS Land Mobile** (`l_LMcomm.zip`, `l_LMpriv.zip`) → licensed
  transmitters: location, owner, **frequency + power (ERP)**.

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

Self-test without downloading hundreds of MB:
```bash
python3 selftest_fixtures.py
python3 sota_rf_sources.py --association W6 --radius 1000 --out-dir testout \
  --summits-file fixtures/summitslist.csv \
  --asr-file fixtures/r_tower.zip --uls-file fixtures/l_LMcomm.zip
```

## Architecture (all in `sota_rf_sources.py`)
- `load_summits()` — parse SOTA `summitslist.csv` (skips the version line, finds
  the `SummitCode` header), filter by association prefix.
- `load_asr()` — parse ASR CO/RA/EN records; keep active towers (constructed,
  not dismantled) that have surface coordinates.
- `load_uls()` — parse ULS HD/EN/LO/FR; keep active licenses; aggregate
  frequencies and max power per location.
- `merge_sources()` — unify into one RF table. **Merge key:** ULS `LO` records
  carry `tower_registration_number`, surfaced as `rf_link_reg`; it matches an
  ASR registration number, so a transmitter is tied to the physical tower it
  sits on.
- `spatial_join()` — sklearn `BallTree` haversine, `query_radius` per summit.

**FCC raw-record field indices are defined as dicts at the top of the script**
(`CO`, `RA`, `EN_ASR`, `HD`, `EN_ULS`, `LO`, `FR`). Edit there if a layout
changes. Files are pipe-delimited, no headers, cp1252, with stray CR/LF inside
records (handled by `fix_lines()`).

## Current status
- Pipeline built and **verified against synthetic fixtures** (parsing, the
  ASR↔ULS merge, radius exclusion, dismantled/cancelled filtering all correct).
- **NOT yet run against live FCC/SOTA data** — it was prototyped in a sandbox
  whose network couldn't reach data.fcc.gov / sotadata.org.uk. **This machine
  can**, so the first real run is the immediate next step.

## Roadmap / next tasks
1. **Run W6 live** and sanity-check results (expect Diablo, Tam, Loma Prieta,
   San Bruno/Sutro-area peaks to be RF-hot). Tune `--radius`.
2. **Add microwave** — `l_micro.zip` uses the *same* HD/EN/LO/FR layout, so it's
   a drop-in: add its URL to `URLS["uls"]`.
3. **Add broadcast FM/TV/AM** — the high-ERP offenders. These live in the FCC
   **LMS/media** dataset (different schema, not ULS). This is the main remaining
   build: a new loader producing the same unified RF-source columns.
4. **Full `--us` run** once layers are in.
5. Optional: collapse ULS-on-ASR matches (`rf_link_reg`) into single structure
   rows that list their frequencies inline, for a cleaner per-structure view.

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
