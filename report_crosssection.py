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
            # ~700 m: a broadcast *peak* (e.g. all of Mt Wilson) is one display site,
            # so its stations don't split into stacked towers on the cross-section
            if abs(r["lat"] - s["lat"]) < 0.006 and abs(r["lon"] - s["lon"]) < 0.006:
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
                     field_min=0.15):
    """Return an SVG string for the off-summit sources lighting this summit, or None.

    `d` = the summit's join rows (DataFrame); `summit` = dict(lat,lon,alt,code).
    A site is shown when its own incident field is ≥ `field_min` V/m (≈ the level
    that desenses a cheap HT, FIELD_MOD_VM) — an *absolute* test, not a share of
    the total, so a summit still surfaces its off-site impactors even when co-sited
    sources dominate its overall field (e.g. San Bruno, whose Sutro-TV field is
    ~2 V/m though a fraction of its own co-sited farm)."""
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
    sites = [s for s in sites if math.sqrt(30.0 * s["contrib"]) >= field_min
             and not np.isnan(s["rc"])][:3]
    if not sites:
        return None
    # co-sited: transmitters on the summit itself (within the near zone). Their
    # combined ERP is shown as a tower + red near-field glow at the summit, so the
    # plot doesn't read as if the only sources are remote.
    near = [r for r in rows if r["dist"] <= base_radius and r["p"]]
    co = dict(erp=sum(r["p"] for r in near),
              rc=max([r["rc"] for r in near if not np.isnan(r["rc"])], default=np.nan),
              n=len(near))
    return _draw(summit, sites, dem_dir, vpat_gain, human_w, co)


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


def _draw(summit, sites, dem_dir, vpat_gain, human_w, co=None):
    """Polyline transect through the summit and its dominant off-summit sites.
    Sites sharing a bearing sit on one side; opposite bearings straddle the
    summit (San Bruno — Mt Davidson — Sutro), each with its own terrain leg and
    sightline. The dominant site drives the vertical-pattern glyph."""
    salt = summit["alt"]
    for s in sites:
        s["brg"] = _bearing(summit["lat"], summit["lon"], s["lat"], s["lon"])
    # split sites >90° apart in heading onto opposite sides of the summit...
    b0 = sites[0]["brg"]
    left, right = [], []
    for s in sites:
        (right if abs((s["brg"] - b0 + 180) % 360 - 180) <= 90 else left).append(s)
    # ...then orient it so the more-easterly side is on the right (west on the left)
    mean_e = lambda g: (sum(math.sin(math.radians(s["brg"])) for s in g) / len(g)) if g else 0.0
    if left and right:
        if mean_e(right) < mean_e(left):     # put the more-easterly group on the right
            left, right = right, left
    else:                                     # all one side: orient by its own E/W
        grp = right or left
        left, right = ([], grp) if mean_e(grp) >= 0 else (grp, [])
    left.sort(key=lambda s: -s["dist"]); right.sort(key=lambda s: s["dist"])
    seq = [("site", s) for s in left] + [("summit", None)] + [("site", s) for s in right]
    coords = [(summit["lat"], summit["lon"]) if a[0] == "summit"
              else (a[1]["lat"], a[1]["lon"]) for a in seq]
    # when the summit sits at an end, extend the terrain past it (~1/8 of the span)
    # so the peak is inset within the DEM profile, not on its edge
    smt0 = next(i for i, a in enumerate(seq) if a[0] == "summit")
    sla, slo = summit["lat"], summit["lon"]
    ext = (sum(dt._hav(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1])
               for i in range(len(coords) - 1)) or 1.0) / 7.0

    def _beyond(alat, alon):                  # a point `ext` m past the summit, away from (alat,alon)
        f = ext / max(dt._hav(alat, alon, sla, slo), 1.0)
        return (sla + (sla - alat) * f, slo + (slo - alon) * f)
    if len(seq) > 1 and smt0 == len(seq) - 1:
        coords.append(_beyond(*coords[smt0 - 1])); seq.append(("ext", None))
    elif len(seq) > 1 and smt0 == 0:
        coords.insert(0, _beyond(*coords[1])); seq.insert(0, ("ext", None))

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

    # ---- layout ----  a beam glyph sits above each antenna in a top band whose
    # height is sized to the beam placement (glyphs stagger DOWN, not sideways, on
    # collision); the terrain plot and a numbered legend strip follow.
    W, GR, GCY, STAG = 900, 48, 46, 26
    PL, PR = 60, 742
    span = cum or 1.0
    SX = (PR - PL) / span
    xx = lambda m: PL + m * SX
    # beam-glyph height per bay-antenna: at the tower x, dropped in steps to clear
    gpos, plist = {}, []
    for xt, s in sorted(((xx(xa[i]), a[1]) for i, a in enumerate(seq) if a[0] == "site"),
                        key=lambda t: t[0]):
        if np.isnan(s["bays"]) or s["bays"] < 1 or s["spacing"] <= 0:
            continue
        gy = GCY
        for cand in (GCY, GCY + STAG, GCY + 2 * STAG, GCY + 3 * STAG):
            if not any(abs(xt - px) < 1.85 * GR and abs(cand - py) < 22 for px, py in plist):
                gy = cand; break
        plist.append((xt, gy)); gpos[id(s)] = gy
    PT = int(max((gy for _, gy in plist), default=GCY) + 30)
    PB = PT + 94
    hs = [e for _, e in profF] + [s["rc"] for s in sites] + [salt]
    ymax = max(hs) * 1.10 + 12
    ymin = min(min(hs) - 25, min(hs) * 0.92)
    SY = (PB - PT) / (ymax - ymin)
    ve = SY / SX
    yy = lambda e: PB - (e - ymin) * SY
    terr = "M" + " L".join(f"{xx(x):.1f},{yy(e):.1f}" for x, e in profF)
    fill = terr + f" L{xx(profF[-1][0]):.1f},{PB} L{xx(profF[0][0]):.1f},{PB} Z"
    xs, ys_ = xx(sx_m), yy(salt)

    co = co or {}
    co_erp = co.get("erp", 0.0) or 0.0
    yc = (yy(co["rc"] if (co.get("rc") is not None and not np.isnan(co["rc"])) else salt + 45)
          if co_erp > 0 else ys_)
    parts = [f'<path d="{fill}" class="xs-terrain"/><path d="{terr}" class="xs-terrline"/>']
    # near-field glow at the summit's own co-sited transmitters — drawn first so it
    # sits far behind every tower, beam and label
    if co_erp > 0:
        parts.append(f'<circle cx="{xs:.1f}" cy="{yc:.1f}" r="84" fill="url(#xsglow)"/>')
    # pass 1: tower + sightline + numbered badge; collect per-site geometry
    info, legend, n = [], [], 0
    for i, a in enumerate(seq):
        if a[0] != "site":
            continue
        n += 1
        s = a[1]; xt = xx(xa[i])
        base = dt.elevation(s["lat"], s["lon"], dem_dir)
        if np.isnan(base):
            base = min(hs)
        yr = yy(s["rc"])
        elev = math.degrees(math.atan2(s["rc"] - salt, s["dist"]))
        loss = dt.terrain_loss(summit["lat"], summit["lon"], salt,
                               s["lat"], s["lon"], s["rc"], s["freq"], dem_dir)
        los = "LOS clear" if loss < 6 else f"diffraction &#8722;{loss:.0f} dB"
        gain = vpat_gain(s["rc"], salt, s["dist"], s["bays"], s["spacing"], near_floor=0.0)
        pdb = (10 * math.log10(gain)) if gain > 0 else None
        parts.append(
            f'<line x1="{xt:.1f}" y1="{yy(base):.1f}" x2="{xt:.1f}" y2="{yr:.1f}" class="xs-tower"/>'
            f'<line x1="{xt:.1f}" y1="{yr:.1f}" x2="{xs:.1f}" y2="{ys_:.1f}" class="xs-los"/>'
            f'<circle cx="{xt:.1f}" cy="{yr:.1f}" r="9" class="xs-badge"/>'
            f'<text x="{xt:.1f}" y="{yr+4:.1f}" class="xs-badgetxt" text-anchor="middle">{n}</text>')
        info.append((xt, yr, s, elev))
        name = str(s["owner"]).split(" (")[0][:22]
        stn = f' &#183; {s["n"]} stn' if s["n"] > 1 else ""
        det = (f'{human_w(s["erp"]) if s["erp"] else ""} &#183; {s["dist"]/1000:.1f} km'
               f'{stn} &#183; {elev:.1f}&#176; &#183; {los}')
        if pdb is not None:
            bv = 0.0 if abs(pdb) < 0.05 else pdb
            det += f' &#183; beam {bv:.1f} dB'
        legend.append((n, name, det))

    # pass 2: the beam glyph above each antenna (its strongest interferer's
    # vertical pattern), at the tower x and its assigned height, summit ray marked.
    for xt, yr, s, elev in info:
        if id(s) not in gpos:
            continue
        lob = _lobes(s["bays"], s["spacing"], GR)
        if not lob:
            continue
        gy = gpos[id(s)]
        sign = -1.0 if xs < xt else 1.0     # ray points toward the summit's side
        rx = xt + sign * GR * 1.12 * math.cos(math.radians(elev))
        ry = gy + GR * 1.12 * math.sin(math.radians(elev))
        parts.append(
            f'<line x1="{xt:.1f}" y1="{gy+13:.1f}" x2="{xt:.1f}" y2="{yr-9:.1f}" class="xs-stem"/>'
            f'<line x1="{xt-GR*1.1:.1f}" y1="{gy:.1f}" x2="{xt+GR*1.1:.1f}" y2="{gy:.1f}" class="xs-hz"/>'
            f'<g transform="translate({xt:.1f},{gy:.1f})"><path d="{lob[0]}" class="xs-lobe"/>'
            f'<path d="{lob[1]}" class="xs-lobe"/></g>'
            f'<line x1="{xt:.1f}" y1="{gy:.1f}" x2="{rx:.1f}" y2="{ry:.1f}" class="xs-ray"/>'
            f'<circle cx="{rx:.1f}" cy="{ry:.1f}" r="3.4" class="xs-sum"/>'
            f'<text x="{xt:.1f}" y="{gy-GR*0.2-8:.1f}" class="xs-erp" text-anchor="middle">'
            f'{(human_w(s["erp"]) + " @ " + str(int(round(s["brg"]))) + "&#176;") if s["erp"] else ""}</text>')

    # co-sited transmitters ON the summit: a tower + total ERP, so the summit reads
    # as a transmitter site too (not only a victim of remote ones)
    if co_erp > 0:
        parts.append(
            f'<line x1="{xs:.1f}" y1="{ys_:.1f}" x2="{xs:.1f}" y2="{yc:.1f}" class="xs-tower"/>'
            f'<text x="{xs:.1f}" y="{yc-9:.1f}" class="xs-erp" text-anchor="middle">{human_w(co_erp)}</text>')
    # summit (receiver) marker + label (anchor inward so it never clips the edge)
    sanc = "start" if xs < W / 2 else "end"
    sdx = 8 if sanc == "start" else -8
    parts.append(
        f'<circle cx="{xs:.1f}" cy="{ys_:.1f}" r="5" class="xs-sum"/>'
        f'<text x="{xs + sdx:.1f}" y="{ys_ + 16:.1f}" class="xs-lbl" text-anchor="{sanc}">'
        f'{summit["code"]} &#183; {salt:.0f} m</text>')

    parts.append(f'<text x="{PL}" y="{PB + 16:.0f}" class="xs-mut">ELEVATION m &#183; '
                 f'distance km &#183; vertical exaggeration &#8776;{ve:.0f}&#215; &#183; '
                 f'beam = each site&#8217;s strongest interferer, vertical pattern</text>')
    ly = PB + 40
    if co_erp > 0:                                    # co-sited row first (red marker)
        parts.append(
            f'<circle cx="{PL + 8:.0f}" cy="{ly - 4:.0f}" r="6" class="xs-cored"/>'
            f'<text x="{PL + 26:.0f}" y="{ly:.0f}" class="xs-legname">On {summit["code"]}'
            f'<tspan class="xs-legdim" dx="8">{human_w(co_erp)} &#183; {co["n"]} stn '
            f'&#183; co-sited (near-field)</tspan></text>')
        ly += 22
    for num, name, detail in legend:
        parts.append(
            f'<circle cx="{PL + 8:.0f}" cy="{ly - 4:.0f}" r="9" class="xs-badge"/>'
            f'<text x="{PL + 8:.0f}" y="{ly:.0f}" class="xs-badgetxt" text-anchor="middle">{num}</text>'
            f'<text x="{PL + 26:.0f}" y="{ly:.0f}" class="xs-legname">{name}'
            f'<tspan class="xs-legdim" dx="8">{detail}</tspan></text>')
        ly += 22
    H = int(ly + 6)
    defs = ('<defs><radialGradient id="xsglow">'
            '<stop offset="0" style="stop-color:var(--high);stop-opacity:.40"/>'
            '<stop offset="1" style="stop-color:var(--high);stop-opacity:0"/>'
            '</radialGradient></defs>')
    return (f'<svg viewBox="0 0 {W} {H}" class="xs-fig" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="Terrain cross-section for {summit["code"]}">'
            + defs + "".join(parts) + "</svg>")
