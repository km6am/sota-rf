# SOTA summit RF-source mapper

Identify the fixed RF sources sitting on or beside SOTA summits, by spatially
joining the SOTA summit list against two public FCC datasets:

| Layer | Source file | What it gives you |
|-------|-------------|-------------------|
| **Structures** | FCC ASR `r_tower.zip` | Registered antenna structures (towers/masts > ~200 ft AGL or near airports): location, owner, height, structure type |
| **Transmitters** | FCC ULS Land Mobile `l_LMcomm.zip`, `l_LMpriv.zip` | Licensed land-mobile stations: location, owner, **frequencies and power (ERP)** |
| **Summits** | SOTA `summitslist.csv` | Every summit's reference, name, coordinates, elevation |

The two FCC layers are **merged**: ULS location records carry a
`tower_registration_number`, so a transmitter license can be tied back to the
exact ASR structure it lives on (surfaced in the `rf_link_reg` column). That
lets you see, for one summit, both "there's a 120 m tower here owned by X" and
"and it's radiating 155 MHz at 250 W."

This is the same RF environment that causes receiver overload / desense when you
activate a summit co-sited with broadcast or public-safety infrastructure.

## Install

```
pip install pandas numpy scikit-learn
```

## Run

Proof-of-concept, one association (downloads + caches the data automatically if
your network can reach data.fcc.gov and sotadata.org.uk):

```
python3 sota_rf_sources.py --association W6 --radius 1000
```

All US associations at once (this is the eventual goal; same code, one flag):

```
python3 sota_rf_sources.py --us --radius 1000
```

If your environment can't reach the FCC / SOTA hosts, download the three files
yourself and point at them:

```
python3 sota_rf_sources.py --association W6 \
    --summits-file summitslist.csv \
    --asr-file r_tower.zip \
    --uls-file l_LMcomm.zip --uls-file l_LMpriv.zip
```

Useful flags: `--radius` (metres around each summit), `--no-uls` (structures
only, much faster), `--no-download` (use cached files in `--data-dir`),
`--out-dir`.

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

## Output

Three files, prefixed by the association (or `US`):

- **`<ASSOC>_rf_sources.csv`** — one row per (summit, RF source) within the
  radius. Columns: `summit`, `summit_name`, `distance_m`, then `rf_source_db`
  (`ASR`/`ULS`), `rf_ref`, `rf_owner`, `rf_struct_type`, `rf_height_agl_m`,
  `rf_freqs_mhz`, `rf_max_power_w`, `rf_link_reg`, …
- **`<ASSOC>_summit_summary.csv`** — one row per summit: source counts
  (`rf_source_count`, `asr_count`, `uls_count`), `nearest_m`, `nearest_owner`,
  `max_struct_height_m`. Sort by `rf_source_count` to rank the RF-hot summits.
- **`<ASSOC>_rf_sources.geojson`** — deduplicated points for mapping. Drop into
  QGIS or geojson.io to see the sources overlaid on terrain.

## Choosing the radius

There's no single "on the summit" distance — the SOTA activation zone is defined
vertically (within 25 m of the summit elevation), which maps to anything from a
couple hundred metres to ~1 km horizontally depending on the peak. Defaults:

- `--radius 250` — installations essentially on the summit block
- `--radius 1000` — summit-top sites (default; good general choice)
- `--radius 2000` — also catch nearby high sites that can still overload a receiver

## Coverage and known gaps

What this captures well:
- All ASR-registered structures (the tall masts that crown summits), regardless
  of service — including broadcast and cell towers, which appear as *structures*
  even when their frequencies come from elsewhere.
- Land-mobile transmitters with real frequency + ERP (public-safety repeaters,
  business radio — the bulk of mountaintop two-way activity).

Also included now (beyond the two core layers above):
- **Microwave point-to-point** — `l_micro.zip`, sharing the ULS HD/EN/LO/FR
  layout; adds 6/11/18 GHz backhaul/STL paths (frequency + location, no power).
- **Broadcast FM / TV / AM** — from the FCC CDBS media dataset; usually the
  highest-ERP offenders on summits like Diablo or San Bruno.

Still not included:
- **Cellular** — licensed by geographic area, so exact site coordinates aren't in
  ULS. The physical towers still show up via ASR.

Other notes:
- ASR only requires registration above ~200 ft AGL (or near airports), so short
  structures may be absent.
- Not every ASR record has surface coordinates; those are skipped.
- FCC "complete" snapshots refresh weekly; re-download for current data.

## Field layouts

The raw FCC `.dat` files are pipe-delimited with no headers and split
coordinates across degree/minute/second columns. Column indices used here follow
the published FCC public-access record layouts (Tower: CO/RA/EN; ULS: HD/EN/LO/FR)
and were cross-checked against existing open-source loaders. They're defined at
the top of `sota_rf_sources.py` so you can audit or extend them.

## Self-test

`selftest_fixtures.py` writes tiny synthetic files in the exact raw formats and
runs the pipeline against them, so you can confirm parsing/merge/spatial-join
behaviour without downloading hundreds of MB:

```
python3 selftest_fixtures.py        # writes ./fixtures/
python3 sota_rf_sources.py --association W6 --radius 1000 --out-dir testout \
    --summits-file fixtures/summitslist.csv \
    --asr-file fixtures/r_tower.zip --uls-file fixtures/l_LMcomm.zip
```
