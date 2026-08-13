#!/usr/bin/env python3
"""Build an enriched "reference" summit layer for CalTopo.

Mirrors jeffkowalski's `sota:SOTA_Summits` WFS layer for one association (its
Point features + marker styling kept **verbatim** — we never touch the markers),
adds our per-summit RF report-card link + RFI summary to each summit's detail,
and adds a DEM-derived 25 m **activation-zone polygon** per summit. Emits one
CalTopo-importable GeoJSON (summit Points + AZ Polygons).

The reference fetch depends on jeffkowalski's (ephemeral) ngrok server; if it is
unreachable the build is skipped rather than emitting a partial layer. AZ polygons
are fixed geometry, so they are cached by SummitCode and can be reused DEM-free.
"""

import json
import math
import os
import urllib.request

import numpy as np
from scipy import ndimage

import dem_terrain

REF_WFS = "https://noneligible-unlithographic-robbie.ngrok-free.dev"
AZ_DROP_M = 25.0                    # SOTA activation zone: within 25 m of the summit
AZ_HALF_DEG = 0.025                 # DEM window half-size (~2.8 km); AZ clipped if larger
DP_EPS_DEG = 0.00012               # Douglas-Peucker simplify tolerance (~13 m)
# marker fields we mirror UNCHANGED from the reference layer
REF_MARKER_KEYS = ("marker-color", "marker-symbol", "marker-size")


# --------------------------------------------------------------------------- #
# Reference layer fetch
# --------------------------------------------------------------------------- #
def fetch_ref_summits(assoc, bbox, base=REF_WFS, timeout=90):
    """Fetch reference SOTA_Summits Point features whose SummitCode is in `assoc`
    (e.g. "W6"), within `bbox` = (min_lat, min_lon, max_lat, max_lon). Returns the
    list of GeoJSON features, or raises on an unreachable server."""
    miny, minx, maxy, maxx = bbox
    url = (f"{base}/geoserver/sota/wfs?service=WFS&version=2.0.0&request=GetFeature"
           f"&typeNames=sota:SOTA_Summits&outputFormat=application/json"
           f"&srsName=EPSG:4326"
           f"&bbox={miny},{minx},{maxy},{maxx},urn:ogc:def:crs:EPSG::4326")
    req = urllib.request.Request(url, headers={"ngrok-skip-browser-warning": "1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        fc = json.loads(r.read().decode("utf-8"))
    pre = assoc.rstrip("/") + "/"
    return [f for f in fc.get("features", [])
            if str(f.get("properties", {}).get("SummitCode", "")).startswith(pre)]


# --------------------------------------------------------------------------- #
# Activation-zone polygon (25 m contour) from the DEM
# --------------------------------------------------------------------------- #
def _dp(points, eps):
    """Douglas-Peucker polyline simplification (points = list of (lon,lat))."""
    if len(points) < 3:
        return points
    a, b = points[0], points[-1]
    ax, ay = a; bx, by = b
    dx, dy = bx - ax, by - ay
    denom = math.hypot(dx, dy) or 1e-12
    dmax, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        d = abs(dy * px - dx * py + bx * ay - by * ax) / denom
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        left = _dp(points[:idx + 1], eps)
        right = _dp(points[idx:], eps)
        return left[:-1] + right
    return [a, b]


def _dp_ring(ring, eps):
    """Douglas-Peucker for a closed ring: split the loop at its two most-distant
    vertices and simplify each arc, so the straight-baseline DP is never
    degenerate. Returns a closed ring (first == last)."""
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else list(ring)
    if len(pts) < 4:
        return pts + [pts[0]]

    def d2(i, j):
        return (pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2

    a = 0
    b = max(range(len(pts)), key=lambda i: d2(a, i))
    if b < a:
        a, b = b, a
    arc1 = _dp(pts[a:b + 1], eps)
    arc2 = _dp(pts[b:] + pts[:a + 1], eps)
    out = arc1[:-1] + arc2[:-1]
    out.append(out[0])
    return out


def _moore_trace(mask, seed):
    """Moore-neighbour boundary trace (clockwise) of the True component of `mask`
    that contains `seed`=(r,c). Returns an ordered list of (r,c) boundary cells."""
    H, W = mask.shape
    # start: topmost-then-leftmost cell of this component
    rs, cs = np.where(mask)
    if len(rs) == 0:
        return []
    order = np.lexsort((cs, rs))
    start = (int(rs[order[0]]), int(cs[order[0]]))
    # clockwise Moore neighbourhood offsets, starting from West
    nbrs = [(0, -1), (-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1)]
    boundary = [start]
    prev_dir = 0                         # came from the West of start
    cur = start
    guard = 8 * int(mask.sum()) + 16
    for _ in range(guard):
        found = False
        # start searching clockwise from the neighbour after where we came from
        for k in range(8):
            d = (prev_dir + 1 + k) % 8
            dr, dc = nbrs[d]
            nr, nc = cur[0] + dr, cur[1] + dc
            if 0 <= nr < H and 0 <= nc < W and mask[nr, nc]:
                boundary.append((nr, nc))
                # we entered (nr,nc) from direction opposite of d
                prev_dir = (d + 4) % 8
                cur = (nr, nc)
                found = True
                break
        if not found:
            break                        # isolated pixel
        if cur == start and len(boundary) > 2:
            break
    return boundary


def activation_zone(lat, lon, alt_m, dem_dir, drop=AZ_DROP_M, half_deg=AZ_HALF_DEG):
    """A polygon ring [[lon,lat],...] enclosing the DEM cells within `drop` metres
    of the summit peak (the SOTA activation zone), or None if unavailable."""
    arr, la, lo = dem_terrain._load_tile(lat, lon, dem_dir)
    if arr is None:
        return None
    n = arr.shape[0]
    step = 1.0 / (n - 1)                 # degrees per cell
    r0 = int(round((1.0 - (lat - la)) * (n - 1)))
    c0 = int(round((lon - lo) * (n - 1)))
    hw = max(2, int(round(half_deg / step)))
    r1, r2 = max(0, r0 - hw), min(n, r0 + hw + 1)
    c1, c2 = max(0, c0 - hw), min(n, c0 + hw + 1)
    sub = arr[r1:r2, c1:c2]
    if sub.size == 0 or np.all(np.isnan(sub)):
        return None
    # peak near the summit cell (guards against DEM/summit-alt mismatch)
    sr, sc = r0 - r1, c0 - c1
    pr1, pr2 = max(0, sr - 3), min(sub.shape[0], sr + 4)
    pc1, pc2 = max(0, sc - 3), min(sub.shape[1], sc + 4)
    core = sub[pr1:pr2, pc1:pc2]
    if np.all(np.isnan(core)):
        return None
    peak = float(np.nanmax(core))
    # threshold relative to the DEM peak near the summit (guarantees the peak cell
    # is included; the registered AltM can sit above the DEM's local max, which
    # would otherwise yield an empty mask).
    thr = peak - drop
    mask = np.nan_to_num(sub, nan=-1e9) >= thr
    if not mask.any():
        return None
    # keep only the component containing the peak cell
    lbl, _ = ndimage.label(mask)                     # 4-connectivity
    pk = np.unravel_index(np.nanargmax(np.where(mask, sub, np.nan)), sub.shape)
    comp = lbl == lbl[pk]
    if comp.sum() < 2:
        return None
    ring_rc = _moore_trace(comp, pk)
    if len(ring_rc) < 3:
        return None
    ring = [[lo + (c1 + c) * step, la + 1.0 - (r1 + r) * step] for r, c in ring_rc]
    simplified = _dp_ring(ring, DP_EPS_DEG)
    # fall back to the full boundary if simplification collapsed a small zone
    ring = simplified if len(simplified) >= 4 else ring
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring if len(ring) >= 4 else None


# --------------------------------------------------------------------------- #
# Layer assembly
# --------------------------------------------------------------------------- #
RISK_STROKE = {"HIGH": "#d7301f", "MODERATE": "#fc8d59",
               "LOW": "#fee08b", "CLEAR": "#8c8c8c"}


def _load_az_cache(path):
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (ValueError, OSError):
            pass
    return {}


def _save_az_cache(path, cache):
    if not path:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    os.replace(tmp, path)


def build_ref_layer(assoc, bbox, qrm_by_code, out_path, report_base=None,
                    dem_dir=None, az_cache_path=None, ref_base=REF_WFS, log=print):
    """Fetch the reference summits for `assoc`, enrich each with our RFI summary +
    report link, add a DEM activation-zone polygon, and write one GeoJSON
    FeatureCollection (summit Points with UNCHANGED markers + AZ Polygons)."""
    ref = fetch_ref_summits(assoc, bbox, ref_base)
    log(f"  reference summits fetched: {len(ref)}")
    az_cache = _load_az_cache(az_cache_path)
    feats, n_az, n_rfi, n_new = [], 0, 0, 0

    for f in ref:
        p = dict(f.get("properties", {}))
        code = p.get("SummitCode", "")
        geom = f.get("geometry")

        # --- enrich the summit point (markers untouched) ---
        q = qrm_by_code.get(code)
        desc_lines = []
        if q:
            n_rfi += 1
            p["rfi_risk"] = q["risk"]
            p["rfi"] = q["qrm"]
            p["total_erp_w"] = q.get("total_erp_w")
            desc_lines.append(f"RF interference risk: {q['risk']}")
            if q.get("qrm"):
                desc_lines.append(q["qrm"])
            erp = q.get("total_erp_w")
            if erp:
                desc_lines.append(f"Total ERP in range: {_human_w(erp)}")
            if report_base and q.get("report"):
                url = f"{report_base.rstrip('/')}/{q['report'].lstrip('/')}"
                p["report"] = url
                desc_lines.append("RF report card:")
                desc_lines.append(url)
        else:
            p["rfi_risk"] = "none catalogued"
            desc_lines.append("No catalogued fixed RF sources in range.")
        if p.get("SOTLAS"):
            desc_lines.append("SOTLAS: " + p["SOTLAS"])
        p["description"] = "\n".join(desc_lines)
        feats.append({"type": "Feature", "geometry": geom, "properties": p})

        # --- activation-zone polygon ---
        ring = az_cache.get(code)
        if ring is None and code not in az_cache and dem_dir:
            lon = p.get("Longitude"); lat = p.get("Latitude")
            alt = p.get("AltM")
            if lat is not None and lon is not None and alt is not None:
                try:
                    ring = activation_zone(float(lat), float(lon), float(alt), dem_dir)
                except Exception as e:                 # noqa: BLE001 - one bad summit must not abort
                    log(f"  AZ {code}: {e}")
                    ring = None
                az_cache[code] = ring          # cache result (incl. None) so reruns skip
                n_new += 1
        if ring:
            n_az += 1
            risk = (q or {}).get("risk", "CLEAR")
            stroke = RISK_STROKE.get(risk, "#8c8c8c")
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {
                    "title": f"{code} activation zone",
                    "SummitCode": code,
                    "stroke": stroke, "stroke-width": 2, "stroke-opacity": 0.85,
                    "fill": stroke, "fill-opacity": 0.12,
                },
            })

    if az_cache_path and n_new:
        _save_az_cache(az_cache_path, az_cache)
    log(f"  activation zones: {n_az} drawn ({n_new} newly computed), RFI enriched: {n_rfi}")
    fc = {"type": "FeatureCollection", "features": feats}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(fc, f)
    return len(ref), n_az


def _human_w(w):
    if w is None:
        return "—"
    if w >= 1e6:
        return f"{w / 1e6:.2f} MW"
    if w >= 1e3:
        return f"{round(w / 1e3)} kW"
    return f"{round(w)} W"
