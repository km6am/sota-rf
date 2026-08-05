# Deploying the RF-source layer into `sota-wfs`

This adds a **live RF-source WFS layer** (summits coloured by overload risk, popup =
per-band ERP breakdown) and a **monthly rebuild** to a `jeffkowalski/sota-wfs`
checkout, mirroring that repo's own fetch → loader → registry → systemd pattern.
Nothing here runs on a dev laptop — it targets the sota-wfs box (Linux + systemd).

## What the pieces map to

| This repo | sota-wfs counterpart | Role |
|---|---|---|
| `fetch/fetch_rf_sources.py` | `fetch/fetch_sota.py` | download FCC data (conditional/frozen), rebuild layers, atomic swap into `data/` |
| `systemd/fetch-rf-sources.{service,timer}` | `systemd/fetch-sota.*` | monthly `oneshot` + `Persistent=true` timer |
| `sota_rf_sources.py` | *(new)* | the pipeline the fetch script calls |
| loader + registry entry (below) | `sota_wfs/loaders.py`, `registry.py` | serve `data/rf_summits.geojson` as `sota:RF_Sources` |

The fetch writes `data/rf_summits.geojson` (and `data/rf_sources.geojson`); the
server's existing 15 s mtime hot-reload picks up each rebuild with no restart.

## 1. Copy files into the sota-wfs checkout
```sh
cd ~/Dropbox/workspace/sota-wfs           # wherever the checkout lives
cp /path/to/sota-rf/sota_rf_sources.py .
cp /path/to/sota-rf/fetch/fetch_rf_sources.py fetch/
cp /path/to/sota-rf/systemd/fetch-rf-sources.* systemd/
```

## 2. Add the pipeline's deps to `pyproject.toml`
The pipeline needs numpy + scikit-learn on top of sota-wfs's pandas:
```toml
dependencies = [
    "flask>=3.0",
    "waitress>=3.0",
    "pandas>=2.0",
    "numpy>=1.24",          # added
    "scikit-learn>=1.3",    # added (BallTree spatial join)
]
```

## 3. Add the loader — append to `sota_wfs/loaders.py`
Our GeoJSON already carries `title`, `description`, `marker-color`, `marker-symbol`
and the analysis fields in each feature's properties, so the loader just passes
them through (a near-clone of `nrel_geojson_loader`):
```python
def rf_geojson_loader(path: Path) -> LayerData:
    """Load a pre-styled RF-source GeoJSON (produced by fetch_rf_sources.py).
    Properties (marker-color/symbol, description, risk, …) pass straight through."""
    mtime = path.stat().st_mtime
    with open(path) as f:
        fc = json.load(f)
    rows, lons, lats = [], [], []
    for feat in fc.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        lon, lat = geom["coordinates"][:2]
        if lon is None or lat is None:
            continue
        rows.append(feat.get("properties", {}) or {})
        lons.append(float(lon))
        lats.append(float(lat))
    df = pd.DataFrame(rows)
    return _finish(df, np.asarray(lons), np.asarray(lats), mtime)
```

## 4. Register the layer — in `sota_wfs/registry.py`
Import the loader and add one `Layer` to the `LAYERS` list:
```python
from .loaders import LayerData, nrel_geojson_loader, sota_csv_loader, rf_geojson_loader
...
    Layer(
        name="RF_Sources",
        ns="sota",
        title="Summit RF Sources",
        abstract="Fixed RF sources (FCC ASR + ULS + CDBS) near SOTA summits, coloured by overload risk",
        source=DATA_DIR / "rf_summits.geojson",
        loader=rf_geojson_loader,
    ),
```

## 5. Install (mirrors sota-wfs's `install.sh` steps)
```sh
.venv/bin/pip install -e .                        # picks up numpy + scikit-learn
SOTA_RF_ASSOCIATION=W6 .venv/bin/python fetch/fetch_rf_sources.py   # first build (W6 to smoke-test; drop the var for national US)
cp systemd/fetch-rf-sources.* ~/.config/systemd/user/   # (install.sh rewrites the %h path; do the same sed if copying by hand)
systemctl --user daemon-reload
systemctl --user enable --now fetch-rf-sources.timer
```

## 6. Add to CalTopo

**Auto-Configure** (`Add → WFS Source → Auto-Configure URL`): paste the **bare
endpoint**, nothing after it —
```
https://<host>/geoserver/wfs
```
CalTopo appends `?SERVICE=WFS&VERSION=1.1.0&REQUEST=GetCapabilities` itself, so a
URL that already has a query string produces a mangled double-`?` request. It
parses **only WFS 1.1.0 in GeoServer's dialect** — the `wfs_server.py`
capabilities are shaped for exactly that (`capabilities_110`); a stock 2.0 doc
or a relabelled one makes it fail with a generic 500 / "Unable to auto configure".

Then pick the layer (`rf:Summits` — the summit score-cards; `rf:Sources` for
every transmitter) and set the label to `title`.

**URL Template** (`Add → WFS Source → URL Template`), if you prefer explicit
control — full GetFeature URL, label `title`:
```
https://<host>/geoserver/wfs?SERVICE=WFS&VERSION=1.1.0&REQUEST=GetFeature&BBOX={bottom},{left},{top},{right}&OUTPUTFORMAT=application/json&TYPENAMES=rf:Summits
```

## Notes / caveats
- **National (`--us`) rebuild is heavy** — ~700 MB conditional download + a spatial
  join over ~1.2 M sources with the 10 km field-strength query. It's a monthly
  `oneshot`, niced/idle-scheduled, `TimeoutStartSec=4h`. If it strains the box,
  set `SOTA_RF_ASSOCIATION=W6` (or run per-association) instead of US.
- **Incremental, not delta.** Conditional GET skips unchanged files and CDBS is
  fetched once (frozen), so a month with no ULS change downloads ~nothing; a month
  with a change still pulls the full ULS complete files. True per-record deltas
  (FCC daily transaction files) would need a persistent datastore — out of scope
  for path (a).
- `data/fcc/` (the raw cache) and `data/.staging/` should be git-ignored on the
  server; only `data/rf_summits.geojson` is what the WFS serves.
