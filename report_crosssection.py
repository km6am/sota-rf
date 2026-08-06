"""Davidson-style terrain + radiation-pattern cross-section for a report card.

Only meaningful when a summit is lit by a source on *another* peak (a distant
site), which is exactly the geometry where terrain LOS and the transmitter's
vertical pattern matter. When the RF is co-sited on the summit itself there is
nothing to draw (distance ≈ 0), so `crosssection_svg` returns None and the report
card omits the section.

Terrain comes from the local SRTM DEM (dem_terrain); the SVG is themed with the
report template's CSS variables. Field/pattern helpers are passed in by the
caller to avoid importing the main module (circular).
"""
import math

import numpy as np

import dem_terrain as dt


def _cluster_distant_sites(rows, base_radius):
    """Group a summit's distant (> base_radius) powered sources into sites by
    location (~250 m). Returns list of site dicts, richest field first."""
    sites = []
    for r in rows:
        p = r["p"]
        if p is None or r["dist"] <= base_radius:
            continue
        for s in sites:
            if abs(r["lat"] - s["lat"]) < 0.0025 and abs(r["lon"] - s["lon"]) < 0.0025:
                s["members"].append(r)
                break
        else:
            sites.append(dict(lat=r["lat"], lon=r["lon"], members=[r]))
    out = []
    for s in sites:
        m = s["members"]
        top = max(m, key=lambda x: (x["p"] or 0))       # ERP leader → site name
        # pattern glyph: strongest station that actually has bay data (the ERP
        # leader is often a TV, which CDBS gives no bays for)
        with_bays = [x for x in m if not np.isnan(x["bays"]) and x["bays"] >= 1]
        patt = max(with_bays, key=lambda x: (x["p"] or 0)) if with_bays else top
        erp = sum(x["p"] for x in m if x["p"])
        out.append(dict(
            lat=np.mean([x["lat"] for x in m]), lon=np.mean([x["lon"] for x in m]),
            dist=min(x["dist"] for x in m),
            rc=max([x["rc"] for x in m if not np.isnan(x["rc"])], default=np.nan),
            erp=erp, contrib=sum(x["contrib"] for x in m), n=len(m),
            owner=str(top["owner"]), db=top["db"],
            freq=patt["freq"], bays=patt["bays"], spacing=patt["spacing"],
            patt_owner=str(patt["owner"])))
    out.sort(key=lambda s: -s["contrib"])
    return out


def crosssection_svg(d, summit, base_radius, dem_dir, *, vpat_gain, human_w,
                     min_share=0.02):
    """Return an SVG string for the dominant off-summit source, or None.

    `d` = the summit's join rows (DataFrame); `summit` = dict(lat,lon,alt,code).
    A figure is drawn only if the richest distant site carries at least
    `min_share` of the summit's total broadband field — i.e. an off-summit peak
    genuinely drives the exposure."""
    salt = summit["alt"]
    if salt is None or np.isnan(salt):
        return None
    rows, total = [], 0.0
    for r in d.itertuples(index=False):
        p = None if np.isnan(r.rf_max_power_w) else float(r.rf_max_power_w)
        dist = max(float(r.distance_m), 10.0)
        c = 0.0
        if p is not None:
            gv = vpat_gain(r.rf_height_amsl_m, r.summit_alt_m, r.distance_m,
                           r.rf_bays, r.rf_spacing, near_floor=base_radius)
            c = gv * p / (dist * dist)          # physical field proxy (site ranking)
            total += c
        fs = [float(t) for t in str(r.rf_freqs_mhz).split(";")
              if t and t.replace(".", "", 1).replace("-", "", 1).isdigit()]
        rows.append(dict(lat=float(r.rf_lat), lon=float(r.rf_lon),
                         rc=float(r.rf_height_amsl_m), p=p, dist=float(r.distance_m),
                         contrib=c, owner=r.rf_owner, db=r.rf_source_db,
                         freq=(fs[0] if fs else 100.0),
                         bays=float(r.rf_bays), spacing=float(r.rf_spacing)))
    if total <= 0:
        return None
    sites = _cluster_distant_sites(rows, base_radius)
    sites = [s for s in sites if s["contrib"] >= min_share * total
             and not np.isnan(s["rc"])][:3]
    if not sites:
        return None
    return _draw(summit, sites, dem_dir, vpat_gain, human_w)


def _bearing(la1, lo1, la2, lo2):
    dl = math.radians(lo2 - lo1)
    y = math.sin(dl) * math.cos(math.radians(la2))
    x = (math.cos(math.radians(la1)) * math.sin(math.radians(la2))
         - math.sin(math.radians(la1)) * math.cos(math.radians(la2)) * math.cos(dl))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _lobes(bays, sp, R):
    if np.isnan(bays) or np.isnan(sp) or bays < 1 or sp <= 0:
        return None
    anull = math.degrees(math.asin(min(1.0 / (bays * sp), 1.0)))
    pr, pl = [(0, 0)], [(0, 0)]
    a = -anull
    while a <= anull:
        x = math.pi * sp * math.sin(math.radians(a))
        af = 1.0 if abs(math.sin(x)) < 1e-9 else abs(math.sin(bays * x) / (bays * math.sin(x)))
        fld = af * max(math.cos(math.radians(a)), 0.0)
        c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
        pr.append((R * fld * c, -R * fld * s)); pl.append((-R * fld * c, -R * fld * s))
        a += 0.5
    pr.append((0, 0)); pl.append((0, 0))
    p = lambda q: "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in q) + " Z"
    return p(pr), p(pl)


def _draw(summit, sites, dem_dir, vpat_gain, human_w):
    """Polyline transect through the summit and its dominant off-summit sites.
    Sites sharing a bearing sit on one side; opposite bearings straddle the
    summit (San Bruno — Mt Davidson — Sutro), each with its own terrain leg and
    sightline. The dominant site drives the vertical-pattern glyph."""
    salt = summit["alt"]
    b0 = _bearing(summit["lat"], summit["lon"], sites[0]["lat"], sites[0]["lon"])
    left, right = [], []
    for s in sites:
        b = _bearing(summit["lat"], summit["lon"], s["lat"], s["lon"])
        (right if abs((b - b0 + 180) % 360 - 180) <= 90 else left).append(s)
    left.sort(key=lambda s: -s["dist"]); right.sort(key=lambda s: s["dist"])
    seq = [("site", s) for s in left] + [("summit", None)] + [("site", s) for s in right]
    coords = [(summit["lat"], summit["lon"]) if a[0] == "summit"
              else (a[1]["lat"], a[1]["lon"]) for a in seq]

    # sample terrain along each leg; record each anchor's cumulative x
    prof, xa, cum = [], [0.0], 0.0
    for i in range(len(seq) - 1):
        a, b = coords[i], coords[i + 1]
        dseg = dt._hav(a[0], a[1], b[0], b[1])
        npts = int(min(70, max(4, dseg / 90)))
        for k in range(npts + 1):
            f = k / npts
            e = dt.elevation(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, dem_dir)
            prof.append((cum + dseg * f, e))
        cum += dseg
        xa.append(cum)
    profF = [(x, e) for x, e in prof if not np.isnan(e)]
    if len(profF) < 5:
        return None
    smt_i = next(i for i, a in enumerate(seq) if a[0] == "summit")
    sx_m = xa[smt_i]

    # ---- layout ----
    W, H = 900, 250
    PL, PR, PT, PB = 60, 748, 30, 198
    xmax = cum or 1.0
    hs = [e for _, e in profF] + [s["rc"] for s in sites] + [salt]
    ymax = max(hs) * 1.10 + 12
    ymin = min(min(hs) - 25, min(hs) * 0.92)
    SX = (PR - PL) / xmax
    SY = (PB - PT) / (ymax - ymin)
    ve = SY / SX
    xx = lambda m: PL + m * SX
    yy = lambda e: PB - (e - ymin) * SY
    terr = "M" + " L".join(f"{xx(x):.1f},{yy(e):.1f}" for x, e in profF)
    fill = terr + f" L{xx(profF[-1][0]):.1f},{PB} L{xx(profF[0][0]):.1f},{PB} Z"
    xs, ys_ = xx(sx_m), yy(salt)

    parts = [f'<path d="{fill}" class="xs-terrain"/><path d="{terr}" class="xs-terrline"/>']
    # each site: tower + sightline + labels
    for i, a in enumerate(seq):
        if a[0] != "site":
            continue
        s = a[1]; xm = xa[i]; xt = xx(xm)
        base = dt.elevation(s["lat"], s["lon"], dem_dir)
        if np.isnan(base):
            base = min(hs)
        yr = yy(s["rc"]); yb = yy(base)
        elev = math.degrees(math.atan2(s["rc"] - salt, s["dist"]))
        loss = dt.terrain_loss(summit["lat"], summit["lon"], salt,
                               s["lat"], s["lon"], s["rc"], s["freq"], dem_dir)
        los = "LOS clear" if loss < 6 else f"diffraction &#8722;{loss:.0f} dB"
        name = str(s["owner"]).split(" (")[0][:18]
        anc = "start" if xt < xs else "end"
        parts.append(
            f'<line x1="{xt:.1f}" y1="{yb:.1f}" x2="{xt:.1f}" y2="{yr:.1f}" class="xs-tower"/>'
            f'<circle cx="{xt:.1f}" cy="{yr:.1f}" r="4" class="xs-rc"/>'
            f'<line x1="{xt:.1f}" y1="{yr:.1f}" x2="{xs:.1f}" y2="{ys_:.1f}" class="xs-los"/>'
            f'<text x="{xt:.1f}" y="{yr-10:.1f}" class="xs-lbl" text-anchor="{anc}">{name}</text>'
            f'<text x="{xt:.1f}" y="{yr+2:.1f}" class="xs-mut" text-anchor="{anc}">'
            f'{human_w(s["erp"]) if s["erp"] else ""} &#183; {s["dist"]/1000:.1f} km'
            f'{(" &#183; " + str(s["n"]) + " stn") if s["n"] > 1 else ""}</text>'
            f'<text x="{(xt+xs)/2:.1f}" y="{(yr+ys_)/2-5:.1f}" class="xs-los-lbl" '
            f'text-anchor="middle">{elev:.1f}&#176; &#183; {los}</text>')
    # summit marker + label
    parts.append(
        f'<circle cx="{xs:.1f}" cy="{ys_:.1f}" r="4.6" class="xs-sum"/>'
        f'<text x="{xs:.1f}" y="{ys_+16:.1f}" class="xs-lbl" text-anchor="middle">'
        f'{summit["code"]} &#183; {salt:.0f} m</text>')

    # vertical-pattern glyph for the dominant site (right margin)
    dom = sites[0]
    gx, gy, R = 812, 92, 42
    lob = _lobes(dom["bays"], dom["spacing"], R)
    if lob:
        elev = math.degrees(math.atan2(dom["rc"] - salt, dom["dist"]))
        gain = vpat_gain(dom["rc"], salt, dom["dist"], dom["bays"], dom["spacing"], near_floor=0.0)
        pdb = 10 * math.log10(gain) if gain > 0 else -30
        rx = gx + R * 1.15 * math.cos(math.radians(elev))
        ry = gy + R * 1.15 * math.sin(math.radians(elev))
        pn = str(dom.get("patt_owner", "")).split(" (")[0][:14]
        parts.append(
            f'<text x="{gx}" y="{gy-R-16:.0f}" class="xs-mut" text-anchor="middle">{pn} pattern</text>'
            f'<line x1="{gx-R*1.1:.0f}" y1="{gy}" x2="{gx+R*1.2:.0f}" y2="{gy}" class="xs-hz"/>'
            f'<g transform="translate({gx},{gy})"><path d="{lob[0]}" class="xs-lobe"/>'
            f'<path d="{lob[1]}" class="xs-lobe"/></g>'
            f'<line x1="{gx}" y1="{gy}" x2="{rx:.1f}" y2="{ry:.1f}" class="xs-ray"/>'
            f'<circle cx="{rx:.1f}" cy="{ry:.1f}" r="3.4" class="xs-sum"/>'
            f'<text x="{gx}" y="{gy+R+16:.0f}" class="xs-mut" text-anchor="middle">'
            f'summit {elev:.1f}&#176; &#183; {pdb:+.1f} dB</text>')

    parts.append(f'<text x="{PL}" y="{PT-12}" class="xs-mut">ELEVATION m &#183; '
                 f'distance km &#183; vertical exaggeration &#8776;{ve:.0f}&#215;</text>')
    return (f'<svg viewBox="0 0 {W} {H}" class="xs-fig" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="Terrain cross-section for {summit["code"]}">'
            + "".join(parts) + "</svg>")
