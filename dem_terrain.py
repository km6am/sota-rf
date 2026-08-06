"""Terrain line-of-sight + knife-edge diffraction loss, backed by SRTM tiles.

Phase B of the RF-source model (see CLAUDE.md roadmap #8b). The result for a
(summit, source) pair is a function of fixed geometry + terrain, so it is
computed once and cached; the pipeline reads the cache and only new/moved
sources are ever recomputed.

DEM: SRTM 1-arc-sec (~30 m) "skadi" .hgt tiles from the public AWS open-data
bucket (no auth). Tiles are fetched on demand into `dem_dir` and memory-mapped,
so a build only ever touches the 1° tiles under the summits it scores. This
module holds the DEM; the droplet only ever sees the compact loss cache.
"""
import gzip
import math
import os
import urllib.request

import numpy as np

SKADI = "https://s3.amazonaws.com/elevation-tiles-prod/skadi/{ns}{la:02d}/{ns}{la:02d}{ew}{lo:03d}.hgt.gz"
_TILES = {}                      # (lat0, lon0) -> np.ndarray | None (ocean/missing)


def _tile_name(lat, lon):
    la = int(math.floor(lat)); lo = int(math.floor(lon))
    ns = "N" if la >= 0 else "S"; ew = "E" if lo >= 0 else "W"
    return la, lo, f"{ns}{abs(la):02d}{ew}{abs(lo):03d}"


def _load_tile(lat, lon, dem_dir):
    la, lo, name = _tile_name(lat, lon)
    key = (la, lo)
    if key in _TILES:
        return _TILES[key], la, lo
    path = os.path.join(dem_dir, name + ".hgt.gz")
    if not os.path.exists(path):
        la_, lo_, _ = _tile_name(lat, lon)
        ns = "N" if la_ >= 0 else "S"; ew = "E" if lo_ >= 0 else "W"
        url = SKADI.format(ns=ns, la=abs(la_), ew=ew, lo=abs(lo_))
        try:
            os.makedirs(dem_dir, exist_ok=True)
            tmp = path + ".part"
            urllib.request.urlretrieve(url, tmp)
            os.replace(tmp, path)
        except Exception:
            _TILES[key] = None                   # ocean / outside SRTM coverage
            return None, la, lo
    raw = gzip.open(path).read()
    n = int(round((len(raw) / 2) ** 0.5))        # 3601 (30 m) or 1201 (90 m)
    arr = np.frombuffer(raw, dtype=">i2").reshape(n, n).astype(np.float32)
    arr[arr < -1000] = np.nan
    _TILES[key] = arr
    return arr, la, lo


def elevation(lat, lon, dem_dir):
    """Terrain elevation (m AMSL) at lat/lon, or nan outside SRTM coverage."""
    arr, la, lo = _load_tile(lat, lon, dem_dir)
    if arr is None:
        return float("nan")
    n = arr.shape[0]
    row = (1.0 - (lat - la)) * (n - 1)
    col = (lon - lo) * (n - 1)
    r = min(max(int(round(row)), 0), n - 1)
    c = min(max(int(round(col)), 0), n - 1)
    return float(arr[r, c])


def _hav(a, b, c, d):
    R = 6_371_000.0
    p1, p2 = math.radians(a), math.radians(c)
    dp = math.radians(c - a); dl = math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def _knife(v):
    """ITU-R P.526 single knife-edge diffraction loss (dB) for parameter v."""
    if v <= -0.78:
        return 0.0
    return 6.9 + 20.0 * math.log10(math.sqrt((v - 0.1) ** 2 + 1) + v - 0.1)


def terrain_loss(slat, slon, salt, tlat, tlon, talt, freq_mhz, dem_dir,
                 step_m=90.0, max_pts=140):
    """Diffraction loss (dB, ≥0) for a source at (tlat,tlon,talt AMSL) reaching a
    summit at (slat,slon,salt). Rx = summit + 2 m operator. Effective-Earth
    (k=4/3) bulge; the worst knife edge along the path governs. Returns 0.0 when
    the DEM is missing so a source is never dropped for lack of terrain data."""
    D = _hav(slat, slon, tlat, tlon)
    if D < step_m or freq_mhz <= 0 or np.isnan(freq_mhz):
        return 0.0
    n = int(min(max_pts, max(8, D / step_m)))
    lam = 300.0 / freq_mhz
    keff = 4.0 / 3.0 * 6_371_000.0
    rx = salt + 2.0
    tx = talt
    worst = 0.0
    for i in range(1, n):
        f = i / n
        lat = slat + (tlat - slat) * f
        lon = slon + (tlon - slon) * f
        g = elevation(lat, lon, dem_dir)
        if np.isnan(g):
            continue
        d1 = D * f; d2 = D - d1
        los = rx + (tx - rx) * f                 # straight ray, summit→source
        bulge = d1 * d2 / (2.0 * keff)
        h = (g + bulge) - los                     # + = obstruction above the ray
        v = h * math.sqrt(2.0 / lam * (1.0 / d1 + 1.0 / d2))
        loss = _knife(v)
        if loss > worst:
            worst = loss
    return round(worst, 1)


if __name__ == "__main__":
    D = "/tmp/dem"
    cases = [
        ("Mt Davidson <- Sutro (clear)", (37.73829, -122.45438, 286), (37.75521, -122.45280, 443), 96.5),
        ("across Mt Diablo (shadowed)",  (37.8450, -121.8600, 90),    (37.9250, -122.0300, 60),    100.0),
    ]
    for name, rx, tx, fq in cases:
        print(f"{name:34} loss={terrain_loss(*rx, *tx, fq, D)} dB")
