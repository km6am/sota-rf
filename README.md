# SOTA summit RF-source mapper

Catalog the fixed RF sources sitting on or beside [SOTA](https://www.sota.org.uk/)
summits, and flag the ones likely to overload a QRP receiver — by spatially
joining the SOTA summit list against three public FCC datasets.

This is the RF environment that causes receiver overload / desense / intermod when
you activate a summit co-sited with broadcast or public-safety infrastructure. The
tool answers, per summit: *how much RF power is here, in which bands, and how bad is
it for the ham band I want to operate?*

## Data sources

Three FCC dataset families are merged with the SOTA summit list (exact files and
download URLs in [SOURCES.md](SOURCES.md)):

| Layer | Source | What it gives you |
|---|---|---|
| **Structures** | FCC ASR `r_tower.zip` | Registered towers/masts (> ~200 ft AGL or near airports): location, owner, height, structure type |
| **Land-mobile + microwave** | FCC ULS `l_LMcomm.zip`, `l_LMpriv.zip`, `l_micro.zip` | Licensed transmitters: location, owner, **frequency + power (ERP)**; microwave adds 6/11/18 GHz backhaul/STL paths (freq + location, no power) |
| **Broadcast** | FCC CDBS `facility.zip` + FM/TV/AM engineering tables | FM/TV/AM stations: callsign, **frequency/channel + ERP** — usually the megawatt-class offenders |
| **Summits** | SOTA `summitslist.csv` | Every summit's code, name, coordinates, elevation |

The layers are **merged**: ULS location records and broadcast facilities carry a
tower registration number (surfaced as `rf_link_reg`), so a transmitter is tied
back to the ASR structure it lives on. That lets you see, for one summit, both
"there's a 120 m tower here owned by X" and "and it's radiating 155 MHz at 250 W."

## Install

```
pip install pandas numpy scikit-learn
```

## Run

Proof-of-concept, one association (auto-downloads + caches the data if your network
can reach data.fcc.gov, transition.fcc.gov and sotadata.org.uk):

```
python3 sota_rf_sources.py --association W6 --radius 1000
```

All US associations at once (~51k summits; same code, one flag):

```
python3 sota_rf_sources.py --us --radius 1000
```

If your environment can't reach the FCC / SOTA hosts, download the files yourself
(see [SOURCES.md](SOURCES.md)) into `--data-dir` and run with `--no-download`.

Useful flags: `--radius` (base radius, metres), `--no-uls` (structures only),
`--no-broadcast` (skip FM/TV/AM), `--no-download` (use cached files in
`--data-dir`), `--broadcast-dir`, `--out-dir`,
`--report W6/CC-072` (write one summit's report card), `--reports-dir DIR`
(batch: a report card per impacted summit into `DIR/reports/` + a compact
`DIR/qrm_index.json` for a host page to merge in).

## Data & updates

The pipeline reads three families of public data — full list, exact files, and
download URLs in [SOURCES.md](SOURCES.md):

- **SOTA** `summitslist.csv` — the summits (refreshed daily upstream).
- **FCC ULS + ASR** — land-mobile, microwave, and registered towers. FCC rebuilds
  these "complete" files **weekly**.
- **FCC CDBS broadcast** (FM/TV/AM) — a **frozen** ~2024-01 snapshot (broadcast
  e-filing moved to LMS), so it never changes: fetched once, then skipped forever.

### One-off / manual

A normal run auto-downloads and caches into `--data-dir` (`fccdata/`). For an
offline or repeat run, use `--no-download` to reuse the cache. The big ULS zips
can exceed `urllib`'s read timeout, so for a manual pull fetch them with
`curl -C -` into the data dir first, then run with `--no-download` (see [Run](#run)).

### Automated monthly refresh — the same pattern as the Tesla Superchargers

The update path is a **drop-in analog of sota-wfs's supercharger fetch** — same
fetch-script + `systemd`-timer + atomic-swap shape:

| Superchargers (sota-wfs) | RF sources (this repo) |
|---|---|
| `fetch/fetch_superchargers.py` | `fetch/fetch_rf_sources.py` |
| `systemd/fetch-superchargers.timer` (weekly) | `systemd/fetch-rf-sources.timer` (monthly) |
| download → validate → atomic swap into `data/` | same, with a full rebuild in between |

`fetch/fetch_rf_sources.py` downloads (conditionally, below), reruns the pipeline,
and atomically swaps `data/rf_summits.geojson` into place; the WFS server's 15 s
mtime hot-reload serves it with no restart. Enable it exactly like the supercharger
timer:

```
systemctl --user enable --now fetch-rf-sources.timer
```

Full deploy steps (loader + registry wiring, deps, the CalTopo layer URL) are in
[DEPLOY_SOTA_WFS.md](DEPLOY_SOTA_WFS.md).

### What actually re-downloads each month

- **CDBS broadcast** — never (frozen; fetched once, then skipped).
- **ULS / ASR completes** — only when FCC's copy is newer than the local one
  (`curl -z` / HTTP `Last-Modified` conditional GET), resumably (`curl -C -`).

So a month with no FCC change pulls ~nothing; a month with a change re-fetches only
the changed complete files. (True per-record deltas from FCC's *daily transaction*
files would need a persistent datastore — a possible future upgrade, not needed at
monthly cadence.)

## Overload-risk model

Two ideas turn a list of nearby transmitters into a per-summit risk read:

- **Field-strength inclusion** — a source is kept if it's within the base
  `--radius` (near-field, any power) **or** its received-power proxy `ERP / d²`
  clears a floor (≈ a 10 kW transmitter at 1 km). So a 1 MW broadcast mast is
  captured ~10 km out while distant low-power land-mobile is not — a flat radius
  can't do both. (This is what surfaces Sutro Tower's ~7 MW on Mt Davidson, 1.9 km
  away, that a 1 km cutoff misses entirely.)
- **Per-ham-band risk** — for each amateur band an activator uses (40 m … 23 cm),
  score `Σ ERP / d²` over sources within a ±octave window; tier it
  **HIGH / MODERATE / LOW / CLEAR**. The summit's overall tier is its worst band,
  and drives the map marker colour (green → red).

## Output

Files prefixed by the association (or `US`):

- **`<ASSOC>_rf_sources.csv`** — one row per (summit, source) kept. Columns:
  `summit`, `summit_name`, `distance_m`, then `rf_source_db` (`ASR`/`ULS`/`FM`/
  `TV`/`AM`), `rf_ref`, `rf_owner`, `rf_freqs_mhz`, `rf_max_power_w`, `rf_services`,
  `rf_link_reg`, …
- **`<ASSOC>_summit_summary.csv`** — one row per summit: source counts, `nearest_m`,
  `nearest_owner`, `max_struct_height_m`, `max_power_w`. Sort by `rf_source_count`
  to rank the RF-hot summits.
- **`<ASSOC>_rf_sources.geojson`** — deduplicated source points for QGIS / geojson.io.
- **`<ASSOC>_summits_caltopo.geojson`** — **the summit-risk map layer.** One pin per
  impacted summit, `marker-color` by overload risk, popup = total ERP then the ERP
  broken down by transmit band (`88-108 MHz FM  425 kW`, biggest first; far sources
  tagged with distance). Direct-importable into [CalTopo](https://caltopo.com), or
  served live over WFS (see [DEPLOY_SOTA_WFS.md](DEPLOY_SOTA_WFS.md)).
- **`<ASSOC>_sources_caltopo.geojson`** — one pin per individual source, `marker-color`
  by ERP. Large nationally (~60k points) — best served over WFS/bbox, not imported.
- **`<CODE>_report.html`** *(only with `--report`)* — a standalone, self-contained
  **report card** for one summit: the spectrum (band-power bars + every individual
  emitter as a dot + per-ham-band overload markers) and a scrollable table of all
  sources. This is the browser deep-dive companion CalTopo can't host; open it in
  any browser. Rendered from `report_template.html`.

CalTopo popups are plain text (no HTML/images/links), so the analysis rides in the
feature's `title` (the SOTA code) + `description`, and severity in `marker-color`;
the marker symbol is `point` (CalTopo's small dot). See
[DEPLOY_SOTA_WFS.md](DEPLOY_SOTA_WFS.md) for the WFS layer + CalTopo URL.

## Choosing the radius

`--radius` sets the **base** (near-field) radius; the field-strength model then
extends it for high-ERP sources only. There's no single "on the summit" distance —
the SOTA activation zone is vertical (within 25 m of the summit elevation), which
maps to a couple hundred metres to ~1 km horizontally depending on the peak:

- `--radius 250` — installations essentially on the summit block
- `--radius 1000` — summit-top sites (default; good general choice)
- `--radius 2000` — also catch nearby high sites at any power

High-ERP broadcast is pulled in well beyond `--radius` regardless, by field strength.

## Coverage and known gaps

Captured well:
- All ASR-registered structures (the tall masts that crown summits).
- Land-mobile + microwave transmitters with real frequency + ERP (public-safety
  repeaters, business radio, 6/11/18 GHz backhaul).
- Broadcast FM/TV/AM — the megawatt-class offenders.

Still not included / caveats:
- **Cellular** — licensed by geographic area, so exact site coordinates aren't in
  ULS; the physical towers still show up via ASR.
- **Broadcast currency** — CDBS is a frozen ~2024-01 snapshot (good for long-lived
  high-ERP incumbents, stale for recent low-power additions); the live LMS successor
  is bot-blocked against scripted download.
- ASR only requires registration above ~200 ft AGL (or near airports), so short
  structures may be absent; records without surface coordinates are skipped.
- Some ULS `power_erp` values are data-entry garbage (multi-MW "STL" mislabels);
  these are capped at 1 MW so they can't dominate a summit's total.

## Field layouts

The raw FCC `.dat` files are pipe-delimited, no headers, cp1252, with split
degree/minute/second coordinate columns. Column indices follow the published FCC
public-access record layouts (ASR: CO/RA/EN; ULS: HD/EN/LO/FR; CDBS: facility +
per-service engineering tables) and are defined as dicts at the top of
`sota_rf_sources.py` so you can audit or extend them.

## Self-test

`selftest_fixtures.py` writes tiny synthetic files in the exact raw formats and
asserts the loaders, the kW→W / channel→freq broadcast conversions, the ULS
power-cap, and the CalTopo scoring — so you can verify behaviour without
downloading hundreds of MB:

```
python3 selftest_fixtures.py        # writes ./fixtures/ and runs the asserts
python3 sota_rf_sources.py --association W6 --radius 2000 --out-dir testout \
    --summits-file fixtures/summitslist.csv \
    --asr-file fixtures/r_tower.zip --uls-file fixtures/l_LMcomm.zip \
    --broadcast-dir fixtures
```

More detail on architecture and design decisions is in [CLAUDE.md](CLAUDE.md).
