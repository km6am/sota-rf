#!/usr/bin/env python3
"""
sota_rf_sources.py
==================
Identify fixed RF sources (registered antenna structures + licensed land-mobile
transmitters) on or near SOTA summits, by spatially joining:

  * SOTA summit list           (summitslist.csv  from sotadata.org.uk)
  * FCC ASR registered towers  (r_tower.zip      from data.fcc.gov)  -> structures
  * FCC ULS Land Mobile        (l_LMcomm.zip, l_LMpriv.zip)          -> freq + power

Output (for a chosen association, e.g. W6):
  <ASSOC>_rf_sources.csv          one row per (summit, RF source) within radius
  <ASSOC>_summit_summary.csv      one row per summit: counts + closest source
  <ASSOC>_rf_sources.geojson      points for mapping (QGIS / geojson.io)

The heavy data files live on data.fcc.gov and sotadata.org.uk. If your network
can reach them, the script downloads + caches automatically. If not (locked-down
box, CI, etc.), download them yourself and pass them with --summits-file /
--asr-file / --uls-file, or drop them in --data-dir and use --no-download.

Field layouts:
  ASR  CO/RA/EN  -> FCC "Tower" public-access record layout
  ULS  HD/EN/LO/FR -> FCC ULS public-access record layout (Land Mobile)
(Verified against published FCC record definitions; see README.)
"""

import argparse
import csv
import io
import json
import os
import sys
import zipfile
from collections import defaultdict

import numpy as np
import pandas as pd

# csv field counts in the FCC dumps can be large; lift the limit.
csv.field_size_limit(min(sys.maxsize, 2_147_483_647))

# --------------------------------------------------------------------------- #
# Data source URLs (FCC "complete" weekly snapshots; SOTA full summit list)
# --------------------------------------------------------------------------- #
URLS = {
    "summits": "https://www.sotadata.org.uk/summitslist.csv",
    "asr":     "https://data.fcc.gov/download/pub/uls/complete/r_tower.zip",
    # Land-mobile + microwave. All share the ULS HD/EN/LO/FR public-format
    # record layout, so load_uls() handles them uniformly; l_micro adds fixed
    # point-to-point microwave paths (STL/backhaul), whose distinct service
    # codes (CF/MG/etc.) surface in the `services` column.
    "uls": [
        "https://data.fcc.gov/download/pub/uls/complete/l_LMcomm.zip",
        "https://data.fcc.gov/download/pub/uls/complete/l_LMpriv.zip",
        "https://data.fcc.gov/download/pub/uls/complete/l_micro.zip",
    ],
    # FCC CDBS media/broadcast public files (FM/TV/AM). NOTE: CDBS was frozen
    # ~2024-01 when broadcast filing moved to LMS; it is a static snapshot, good
    # for the long-lived high-ERP incumbents (full-power FM/TV) that dominate
    # receiver-overload risk, stale for recent low-power additions. The live
    # successor (LMS) is bot-blocked against scripted download. See CLAUDE.md.
    "broadcast": {
        "facility":    "https://transition.fcc.gov/Bureaus/MB/Databases/cdbs/facility.zip",
        "fm_eng_data": "https://transition.fcc.gov/Bureaus/MB/Databases/cdbs/fm_eng_data.zip",
        "tv_eng_data": "https://transition.fcc.gov/Bureaus/MB/Databases/cdbs/tv_eng_data.zip",
        "am_eng_data": "https://transition.fcc.gov/Bureaus/MB/Databases/cdbs/am_eng_data.zip",
        "am_ant_sys":  "https://transition.fcc.gov/Bureaus/MB/Databases/cdbs/am_ant_sys.zip",
    },
}

# US SOTA association code prefixes (SummitCode starts with one of these + "/").
US_PREFIXES = ("W0", "W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8", "W9",
               "KH6", "KH8", "KL7", "KP4")

# --------------------------------------------------------------------------- #
# Raw-record column indices (0-based, after splitting a line on "|")
# --------------------------------------------------------------------------- #
# FCC "Tower" (ASR) records
CO = dict(usi=4, reg=3, lat_dir=9, lat_tot=10, lon_dir=14, lon_tot=15)
RA = dict(usi=4, reg=3, date_constructed=12, date_dismantled=13,
          state=25, height_struct=28, gnd_elev=29, height_agl=30,
          height_amsl=31, structure_type=32)
EN_ASR = dict(usi=4, entity_name=9)

# FCC ULS records
HD = dict(usi=1, call=4, status=5, service=6, grant=7, expired=8)
EN_ULS = dict(usi=1, call=4, entity_name=7)
LO = dict(usi=1, call=4, loc_type=6, loc_num=8,
          lat_deg=19, lat_min=20, lat_sec=21, lat_dir=22,
          lon_deg=23, lon_min=24, lon_sec=25, lon_dir=26,
          tower_reg=37, loc_name=42)
FR = dict(usi=1, call=4, loc_num=6, freq_mhz=10, power_output=15, power_erp=16)

# Sanity cap: any ULS ERP above this is data-entry garbage. Legit land-mobile
# ERP tops out in the low kW and microwave carries no power at all; the only
# legitimate megawatts (full-power TV) come from CDBS, never ULS. Observed
# garbage: 5 MW / 11.7 MW "STL" values. Readings above the cap are dropped
# (→ NaN power) rather than trusted, so they can't dominate a summit's total.
ULS_POWER_CAP_W = 1_000_000.0

# FCC CDBS broadcast (media) records. Verified against the live files + DDL:
# real files carry a few extra trailing columns vs the published DDL, but FCC
# appends new fields at the end so these indices hold. (See CLAUDE.md.)
FAC = dict(comm_city=0, comm_state=1, callsign=5, channel=6, freq=9,
           service=10, facility_id=14, status=16)
# FM: effective_erp is almost always blank -- horiz_erp/vert_erp carry the ERP.
FM_ENG = dict(facility_id=20, eng_rec=19, asrn=9, station_class=49, channel=62,
              erp_eff=16, erp_h=29, erp_v=52, haat=23, rcamsl=47,
              lat_deg=30, lat_dir=31, lat_min=32, lat_sec=33,
              lon_deg=34, lon_dir=35, lon_min=36, lon_sec=37)
# TV: effective_erp is the reliable field (power_output_vis_kw as fallback).
TV_ENG = dict(facility_id=21, eng_rec=19, asrn=7, channel=66,
              erp_eff=15, erp_vis=44, haat=24, rcamsl=56,
              lat_deg=28, lat_dir=29, lat_min=30, lat_sec=31,
              lon_deg=32, lon_dir=33, lon_min=34, lon_sec=35)
# AM antenna system holds coords + power but no facility_id; join via am_eng_data
# (application_id -> facility_id).
AM_ANT = dict(app_id=2, eng_rec=27, power=22,
              lat_deg=12, lat_dir=13, lat_min=14, lat_sec=15,
              lon_deg=16, lon_dir=17, lon_min=18, lon_sec=19)
AM_ENG = dict(app_id=1, facility_id=4)

# CDBS facility statuses we treat as "on the air" (vs void/cancelled/CP-only).
BROADCAST_LIVE_STATUS = {"LICEN"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def log(*a):
    print(*a, file=sys.stderr, flush=True)


def download(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        log(f"  cached: {dest}")
        return dest
    import urllib.request
    log(f"  downloading {url} -> {dest}")
    req = urllib.request.Request(url, headers={"User-Agent": "sota-rf/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return dest


def fix_lines(raw_bytes):
    """FCC dumps embed stray CR/CRLF inside records. Normalise to clean lines.
    Mirrors the canonical fix: \\r\\r\\n and \\r -> space, \\r\\n -> newline."""
    return (raw_bytes.replace(b"\r\r\n", b" ")
                     .replace(b"\r\n", b"\n")
                     .replace(b"\r", b" "))


def read_dat_from_zip(zip_path, member):
    """Yield split rows for one .dat member inside an FCC zip."""
    with zipfile.ZipFile(zip_path) as z:
        name = next((n for n in z.namelist()
                     if n.upper().endswith(member.upper() + ".DAT")), None)
        if name is None:
            return
        data = fix_lines(z.read(name)).decode("cp1252", errors="replace")
    for row in csv.reader(io.StringIO(data), delimiter="|", quoting=csv.QUOTE_NONE):
        if row:
            yield row


def g(row, idx):
    return row[idx] if idx < len(row) else ""


def to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


def nz(x):
    """nan/None -> 0.0. For optional minute/second fields: a blank minute must
    not poison the whole coordinate. (Note `np.nan or 0` is `np.nan`, since NaN
    is truthy in Python -- so `(m or 0)` does NOT guard against it.)"""
    return 0.0 if (x is None or (isinstance(x, float) and np.isnan(x))) else x


def dms_to_dec(deg, mn, sec, direction, neg="S"):
    """Degrees/minutes/seconds (+ hemisphere char) -> signed decimal degrees.
    Returns nan if the degree field is missing."""
    d = to_float(deg)
    if np.isnan(d):
        return np.nan
    val = abs(d) + nz(to_float(mn)) / 60.0 + nz(to_float(sec)) / 3600.0
    return -val if str(direction).strip().upper() == neg else val


def tv_channel_center_mhz(ch):
    """US over-the-air TV channel number -> band-center frequency (MHz). Lets a
    TV source be compared on frequency to nearby receivers. nan if out of range."""
    c = to_float(ch)
    if np.isnan(c):
        return np.nan
    c = int(c)
    if 2 <= c <= 4:     lo = 54 + (c - 2) * 6      # VHF-lo ch 2-4
    elif 5 <= c <= 6:   lo = 76 + (c - 5) * 6      # VHF-lo ch 5-6
    elif 7 <= c <= 13:  lo = 174 + (c - 7) * 6     # VHF-hi ch 7-13
    elif 14 <= c <= 36: lo = 470 + (c - 14) * 6    # UHF ch 14-36 (post-repack)
    else:               return np.nan
    return lo + 3.0     # 6 MHz wide; report the center


# --------------------------------------------------------------------------- #
# SOTA summits
# --------------------------------------------------------------------------- #
def load_summits(path, prefixes):
    with open(path, "r", encoding="cp1252", errors="replace", newline="") as f:
        lines = f.read().splitlines()
    # The summit list has a version/date line first; the real header row is the
    # one beginning with "SummitCode".
    hdr_i = next(i for i, ln in enumerate(lines) if ln.startswith("SummitCode"))
    reader = csv.DictReader(io.StringIO("\n".join(lines[hdr_i:])))
    rows = []
    pref = tuple(p + "/" for p in prefixes)
    for r in reader:
        code = (r.get("SummitCode") or "").strip()
        if not code.startswith(pref):
            continue
        lat, lon = to_float(r.get("Latitude")), to_float(r.get("Longitude"))
        if np.isnan(lat) or np.isnan(lon):
            continue
        rows.append(dict(
            summit=code,
            name=(r.get("SummitName") or "").strip(),
            region=(r.get("RegionName") or "").strip(),
            alt_m=to_float(r.get("AltM")),
            points=to_float(r.get("Points")),
            lat=lat, lon=lon,
        ))
    df = pd.DataFrame(rows)
    log(f"  summits matched: {len(df)}")
    return df


# --------------------------------------------------------------------------- #
# ASR registered structures
# --------------------------------------------------------------------------- #
def load_asr(zip_path):
    owners = {g(r, EN_ASR["usi"]): g(r, EN_ASR["entity_name"]).strip()
              for r in read_dat_from_zip(zip_path, "EN")}

    coords = {}
    for r in read_dat_from_zip(zip_path, "CO"):
        usi = g(r, CO["usi"])
        lat_tot, lon_tot = to_float(g(r, CO["lat_tot"])), to_float(g(r, CO["lon_tot"]))
        if np.isnan(lat_tot) or np.isnan(lon_tot):
            continue
        lat = (lat_tot / 3600.0) * (-1 if g(r, CO["lat_dir"]) == "S" else 1)
        lon = (lon_tot / 3600.0) * (-1 if g(r, CO["lon_dir"]) == "W" else 1)
        coords[usi] = (lat, lon)  # one surface coordinate per structure

    rows = []
    for r in read_dat_from_zip(zip_path, "RA"):
        usi = g(r, RA["usi"])
        if usi not in coords:
            continue
        if g(r, RA["date_dismantled"]).strip():           # gone
            continue
        if not g(r, RA["date_constructed"]).strip():       # never built
            continue
        lat, lon = coords[usi]
        rows.append(dict(
            source_db="ASR",
            ref=g(r, RA["reg"]).strip(),
            owner=owners.get(usi, "").strip(),
            lat=lat, lon=lon,
            struct_type=g(r, RA["structure_type"]).strip(),
            height_agl_m=to_float(g(r, RA["height_agl"])),   # metres (FCC stores m)
            height_amsl_m=to_float(g(r, RA["height_amsl"])),
            freqs_mhz="", max_power_w=np.nan, services="",
            link_reg=g(r, RA["reg"]).strip(),
        ))
    df = pd.DataFrame(rows)
    log(f"  ASR active structures w/ coords: {len(df)}")
    return df


# --------------------------------------------------------------------------- #
# ULS land-mobile transmitters (freq + power)
# --------------------------------------------------------------------------- #
def load_uls(zip_paths):
    owners, status, service = {}, {}, {}
    freqs = defaultdict(list)        # usi -> [freq_mhz, ...]
    powers = defaultdict(float)      # usi -> max power (W)
    locs = []                        # (usi, lat, lon, name, tower_reg)

    for zp in zip_paths:
        for r in read_dat_from_zip(zp, "HD"):
            usi = g(r, HD["usi"])
            status[usi] = g(r, HD["status"]).strip()
            service[usi] = g(r, HD["service"]).strip()
        for r in read_dat_from_zip(zp, "EN"):
            usi = g(r, EN_ULS["usi"])
            nm = g(r, EN_ULS["entity_name"]).strip()
            if nm:
                owners[usi] = nm
        for r in read_dat_from_zip(zp, "FR"):
            usi = g(r, FR["usi"])
            fq = to_float(g(r, FR["freq_mhz"]))
            if not np.isnan(fq):
                freqs[usi].append(fq)
            for k in ("power_erp", "power_output"):
                p = to_float(g(r, FR[k]))
                if not np.isnan(p) and p <= ULS_POWER_CAP_W:   # drop garbage outliers
                    powers[usi] = max(powers[usi], p)
        for r in read_dat_from_zip(zp, "LO"):
            usi = g(r, LO["usi"])
            ld, lm, ls_ = (to_float(g(r, LO["lat_deg"])),
                           to_float(g(r, LO["lat_min"])),
                           to_float(g(r, LO["lat_sec"])))
            od, om, os_ = (to_float(g(r, LO["lon_deg"])),
                           to_float(g(r, LO["lon_min"])),
                           to_float(g(r, LO["lon_sec"])))
            if np.isnan(ld) or np.isnan(od):
                continue
            lat = (ld + nz(lm)/60 + nz(ls_)/3600) * (-1 if g(r, LO["lat_dir"]) == "S" else 1)
            lon = (od + nz(om)/60 + nz(os_)/3600) * (-1 if g(r, LO["lon_dir"]) == "W" else 1)
            locs.append((usi, lat, lon, g(r, LO["loc_name"]).strip(),
                         g(r, LO["tower_reg"]).strip()))

    rows = []
    for usi, lat, lon, name, tower_reg in locs:
        if status.get(usi, "A") not in ("A", ""):    # keep Active (and blanks)
            continue
        fl = sorted(set(round(x, 4) for x in freqs.get(usi, [])))
        rows.append(dict(
            source_db="ULS",
            ref=usi,
            owner=owners.get(usi, "").strip(),
            lat=lat, lon=lon,
            struct_type="", height_agl_m=np.nan, height_amsl_m=np.nan,
            freqs_mhz=";".join(f"{x:g}" for x in fl[:40]),
            max_power_w=powers.get(usi, np.nan),
            services=service.get(usi, ""),
            link_reg=tower_reg,            # ASR reg # if the license names one
            loc_name=name,
        ))
    df = pd.DataFrame(rows)
    log(f"  ULS locations (land-mobile + microwave): {len(df)}")
    return df


# --------------------------------------------------------------------------- #
# Broadcast FM/TV/AM (FCC CDBS media) -- the high-ERP sources
# --------------------------------------------------------------------------- #
def _broadcast_row(db, fid, finfo, lat, lon, erp_kw, freq_mhz, rcamsl, asrn, services):
    """Assemble one unified RF-source row from a broadcast facility + engineering
    record. ERP comes in kW; the unified table is watts (broadcast dwarfs the
    land-mobile sources, which is exactly the point)."""
    cs = (finfo.get("callsign") or "").strip() or fid
    city, state = finfo.get("city", ""), finfo.get("state", "")
    where = ", ".join(p for p in (city, state) if p)
    erp = to_float(erp_kw)
    fq = to_float(freq_mhz)
    return dict(
        source_db=db,
        ref=cs,
        owner=(f"{cs} ({where})" if where else cs),
        lat=lat, lon=lon,
        struct_type="",
        height_agl_m=np.nan,                       # HAAT != AGL; leave blank
        height_amsl_m=to_float(rcamsl),
        freqs_mhz=("" if np.isnan(fq) else f"{fq:g}"),
        max_power_w=(np.nan if np.isnan(erp) else erp * 1000.0),
        services=services,
        link_reg=str(asrn).strip(),                # ASR registration -> physical tower
        loc_name=where,
    )


def load_broadcast(facility_zip, fm_zip, tv_zip, am_eng_zip, am_ant_zip):
    # facility table: identity + service + on-air status, keyed by facility_id
    fac = {}
    for r in read_dat_from_zip(facility_zip, "facility"):
        fid = g(r, FAC["facility_id"]).strip()
        if not fid:
            continue
        fac[fid] = dict(
            callsign=g(r, FAC["callsign"]).strip(),
            service=g(r, FAC["service"]).strip(),
            status=g(r, FAC["status"]).strip(),
            freq=g(r, FAC["freq"]).strip(),
            channel=g(r, FAC["channel"]).strip(),
            city=g(r, FAC["comm_city"]).strip(),
            state=g(r, FAC["comm_state"]).strip(),
        )

    def live(fid):
        f = fac.get(fid)
        return f if (f and f["status"] in BROADCAST_LIVE_STATUS) else None

    # Keep one current ("C") engineering record per facility -- the highest-ERP
    # one (the main antenna, not an auxiliary).
    fm_best, tv_best, am_best = {}, {}, {}

    # ---- FM (incl. translators FX / LP FL) ----
    for r in read_dat_from_zip(fm_zip, "fm_eng_data"):
        if g(r, FM_ENG["eng_rec"]).strip() != "C":
            continue
        fid = g(r, FM_ENG["facility_id"]).strip()
        finfo = live(fid)
        if not finfo:
            continue
        lat = dms_to_dec(g(r, FM_ENG["lat_deg"]), g(r, FM_ENG["lat_min"]),
                         g(r, FM_ENG["lat_sec"]), g(r, FM_ENG["lat_dir"]), "S")
        lon = dms_to_dec(g(r, FM_ENG["lon_deg"]), g(r, FM_ENG["lon_min"]),
                         g(r, FM_ENG["lon_sec"]), g(r, FM_ENG["lon_dir"]), "W")
        if np.isnan(lat) or np.isnan(lon):
            continue
        erps = [to_float(g(r, FM_ENG[k])) for k in ("erp_h", "erp_v", "erp_eff")]
        erp = max([x for x in erps if not np.isnan(x)], default=np.nan)
        cls = g(r, FM_ENG["station_class"]).strip()
        svc = finfo["service"] or "FM"
        services = svc + (f" class {cls}" if cls else "")
        row = _broadcast_row("FM", fid, finfo, lat, lon, erp,
                             to_float(finfo["freq"]),               # MHz already
                             g(r, FM_ENG["rcamsl"]), g(r, FM_ENG["asrn"]), services)
        key = -1.0 if np.isnan(erp) else erp
        if fid not in fm_best or key > fm_best[fid][0]:
            fm_best[fid] = (key, row)

    # ---- TV (full power TV/DT, low power LD/TX, etc.) ----
    for r in read_dat_from_zip(tv_zip, "tv_eng_data"):
        if g(r, TV_ENG["eng_rec"]).strip() != "C":
            continue
        fid = g(r, TV_ENG["facility_id"]).strip()
        finfo = live(fid)
        if not finfo:
            continue
        lat = dms_to_dec(g(r, TV_ENG["lat_deg"]), g(r, TV_ENG["lat_min"]),
                         g(r, TV_ENG["lat_sec"]), g(r, TV_ENG["lat_dir"]), "S")
        lon = dms_to_dec(g(r, TV_ENG["lon_deg"]), g(r, TV_ENG["lon_min"]),
                         g(r, TV_ENG["lon_sec"]), g(r, TV_ENG["lon_dir"]), "W")
        if np.isnan(lat) or np.isnan(lon):
            continue
        erp = to_float(g(r, TV_ENG["erp_eff"]))
        if np.isnan(erp):
            erp = to_float(g(r, TV_ENG["erp_vis"]))
        ch = g(r, TV_ENG["channel"]).strip() or finfo["channel"]
        svc = finfo["service"] or "TV"
        services = svc + (f" ch{ch}" if ch else "")
        row = _broadcast_row("TV", fid, finfo, lat, lon, erp,
                             tv_channel_center_mhz(ch),
                             g(r, TV_ENG["rcamsl"]), g(r, TV_ENG["asrn"]), services)
        key = -1.0 if np.isnan(erp) else erp
        if fid not in tv_best or key > tv_best[fid][0]:
            tv_best[fid] = (key, row)

    # ---- AM (coords+power live in am_ant_sys, keyed by application_id) ----
    am_app2fac = {}
    for r in read_dat_from_zip(am_eng_zip, "am_eng_data"):
        aid = g(r, AM_ENG["app_id"]).strip()
        fid = g(r, AM_ENG["facility_id"]).strip()
        if aid and fid:
            am_app2fac[aid] = fid
    for r in read_dat_from_zip(am_ant_zip, "am_ant_sys"):
        if g(r, AM_ANT["eng_rec"]).strip() != "C":
            continue
        fid = am_app2fac.get(g(r, AM_ANT["app_id"]).strip())
        if not fid:
            continue
        finfo = live(fid)
        if not finfo:
            continue
        lat = dms_to_dec(g(r, AM_ANT["lat_deg"]), g(r, AM_ANT["lat_min"]),
                         g(r, AM_ANT["lat_sec"]), g(r, AM_ANT["lat_dir"]), "S")
        lon = dms_to_dec(g(r, AM_ANT["lon_deg"]), g(r, AM_ANT["lon_min"]),
                         g(r, AM_ANT["lon_sec"]), g(r, AM_ANT["lon_dir"]), "W")
        if np.isnan(lat) or np.isnan(lon):
            continue
        power = to_float(g(r, AM_ANT["power"]))                      # kW
        khz = to_float(finfo["freq"])                               # AM facility freq is kHz
        freq = np.nan if np.isnan(khz) else khz / 1000.0           # -> MHz
        row = _broadcast_row("AM", fid, finfo, lat, lon, power, freq,
                             np.nan, "", finfo["service"] or "AM")
        key = -1.0 if np.isnan(power) else power
        if fid not in am_best or key > am_best[fid][0]:
            am_best[fid] = (key, row)

    rows = ([v[1] for v in fm_best.values()]
            + [v[1] for v in tv_best.values()]
            + [v[1] for v in am_best.values()])
    df = pd.DataFrame(rows)
    log(f"  broadcast facilities (licensed): {len(df)}  "
        f"(FM {len(fm_best)} + TV {len(tv_best)} + AM {len(am_best)})")
    return df


# --------------------------------------------------------------------------- #
# Merge + spatial join
# --------------------------------------------------------------------------- #
COLS = ["source_db", "ref", "owner", "lat", "lon", "struct_type",
        "height_agl_m", "height_amsl_m", "freqs_mhz", "max_power_w",
        "services", "link_reg"]


def merge_sources(*dfs):
    frames = [d for d in dfs if d is not None and len(d)]
    if not frames:
        return pd.DataFrame(columns=COLS)
    out = pd.concat(frames, ignore_index=True)
    for c in COLS:
        if c not in out.columns:
            out[c] = "" if c not in ("lat", "lon", "height_agl_m",
                                     "height_amsl_m", "max_power_w") else np.nan
    return out[COLS + (["loc_name"] if "loc_name" in out.columns else [])]


def spatial_join(summits, rf, radius_m):
    from sklearn.neighbors import BallTree
    if len(summits) == 0 or len(rf) == 0:
        return pd.DataFrame()
    # Drop sources with missing or out-of-range coordinates before indexing:
    # live FCC data carries a few blank/garbage lat/lon (e.g. lat > 90), which
    # would crash BallTree or yield nonsense distances.
    n_before = len(rf)
    valid = (np.isfinite(rf["lat"]) & np.isfinite(rf["lon"])
             & rf["lat"].between(-90, 90) & rf["lon"].between(-180, 180))
    rf = rf[valid].reset_index(drop=True)
    if len(rf) < n_before:
        log(f"  dropped {n_before - len(rf)} source(s) with bad coordinates")
    if len(rf) == 0:
        return pd.DataFrame()
    R = 6_371_000.0
    rf_rad = np.radians(rf[["lat", "lon"]].to_numpy())
    tree = BallTree(rf_rad, metric="haversine")
    su_rad = np.radians(summits[["lat", "lon"]].to_numpy())
    ind, dist = tree.query_radius(su_rad, r=radius_m / R,
                                  return_distance=True, sort_results=True)
    recs = []
    s = summits.reset_index(drop=True)
    for i, (idxs, ds) in enumerate(zip(ind, dist)):
        srow = s.iloc[i]
        for j, d in zip(idxs, ds):
            rfr = rf.iloc[j]
            rec = dict(
                summit=srow["summit"], summit_name=srow["name"],
                region=srow["region"], summit_lat=srow["lat"],
                summit_lon=srow["lon"], summit_alt_m=srow["alt_m"],
                distance_m=round(d * R, 1),
            )
            for c in COLS:
                rec[f"rf_{c}"] = rfr[c]
            recs.append(rec)
    return pd.DataFrame(recs)


def summarise(joined, summits):
    if len(joined) == 0:
        base = summits[["summit", "name", "region", "lat", "lon", "alt_m"]].copy()
        base["rf_source_count"] = 0
        return base
    grp = joined.groupby("summit")
    out = grp.agg(
        rf_source_count=("rf_ref", "size"),
        asr_count=("rf_source_db", lambda s: (s == "ASR").sum()),
        uls_count=("rf_source_db", lambda s: (s == "ULS").sum()),
        bcast_count=("rf_source_db", lambda s: s.isin(("FM", "TV", "AM")).sum()),
        nearest_m=("distance_m", "min"),
        nearest_owner=("rf_owner", "first"),
        max_struct_height_m=("rf_height_agl_m", "max"),
        max_power_w=("rf_max_power_w", "max"),
    ).reset_index()
    meta = summits[["summit", "name", "region", "lat", "lon", "alt_m", "points"]]
    return meta.merge(out, on="summit", how="left").fillna(
        {"rf_source_count": 0, "asr_count": 0, "uls_count": 0, "bcast_count": 0})


def to_geojson(joined, path):
    feats = []
    seen = set()
    for _, r in joined.iterrows():
        key = (r["rf_source_db"], r["rf_ref"], round(r["rf_lat"], 6), round(r["rf_lon"], 6))
        if key in seen:
            continue
        seen.add(key)
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["rf_lon"], r["rf_lat"]]},
            "properties": {
                "db": r["rf_source_db"], "ref": r["rf_ref"], "owner": r["rf_owner"],
                "struct_type": r["rf_struct_type"],
                "height_agl_m": None if pd.isna(r["rf_height_agl_m"]) else r["rf_height_agl_m"],
                "freqs_mhz": r["rf_freqs_mhz"], "max_power_w": None if pd.isna(r["rf_max_power_w"]) else r["rf_max_power_w"],
                "nearest_summit": r["summit"], "distance_m": r["distance_m"],
            },
        })
    with open(path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f)
    return len(feats)


# --------------------------------------------------------------------------- #
# CalTopo layers: overload-risk summit points + power-coloured source points.
# CalTopo renders GeoJSON points and honours the simplestyle-spec properties
# (marker-color / marker-symbol / marker-size) and shows the rest as a plain-
# text popup (no HTML/img/links). So the analysis is compressed into text
# fields + a `description` blob, and severity is encoded as marker colour.
# --------------------------------------------------------------------------- #

# Ham bands an activator operates (MHz band-centre). "Risk" to each is a
# front-end-overload proxy: Σ ERP_watts / dist_m² over powered sources whose
# frequency lands within a ±octave window (0.5×–2× the centre). Thresholds are
# heuristic, calibrated so the known-hot Bay Area masts read HIGH.
HAM_BANDS = [("40m", 7.15), ("20m", 14.2), ("6m", 52),
             ("2m", 146), ("70cm", 435), ("23cm", 1270)]
RISK_ORDER = ["CLEAR", "LOW", "MODERATE", "HIGH"]
RISK_COLOR = {"HIGH": "#c8101e", "MODERATE": "#e0952b",
              "LOW": "#6da536", "CLEAR": "#4d7a20"}

# Transmit-band breakdown for the popup. Coarse service groupings (below) may
# nominally span a ham band; they are SPLIT at every ham-band edge so a reported
# range never crosses a ham allocation, and any source that lands inside a ham
# band is called out under the ham-band label (e.g. "144-148 MHz 2m").
SERVICE_BANDS = [("AM", 0.5, 1.71), ("HF", 1.71, 50), ("VHF-lo", 50, 88),
                 ("FM", 88, 108), ("air", 108, 137), ("VHF", 137, 174),
                 ("VHF-TV", 174, 225), ("gov", 225, 400), ("UHF", 400, 470),
                 ("UHF-TV", 470, 700), ("700/800/900", 700, 960),
                 ("microwave", 960, 60000)]
# US amateur allocations within range (label, lo, hi MHz).
HAM_ALLOC = [("160m", 1.8, 2.0), ("80m", 3.5, 4.0), ("40m", 7.0, 7.3),
             ("30m", 10.1, 10.15), ("20m", 14.0, 14.35), ("17m", 18.068, 18.168),
             ("15m", 21.0, 21.45), ("12m", 24.89, 24.99), ("10m", 28.0, 29.7),
             ("6m", 50.0, 54.0), ("2m", 144.0, 148.0), ("1.25m", 222.0, 225.0),
             ("70cm", 420.0, 450.0), ("33cm", 902.0, 928.0),
             ("23cm", 1240.0, 1300.0), ("13cm", 2300.0, 2450.0)]


def _build_rf_bins():
    """Split the service bands at every ham-band edge -> contiguous, non-ham-
    spanning bins. Each is (lo, hi, label, is_ham)."""
    edges = sorted({e for _, lo, hi in SERVICE_BANDS + HAM_ALLOC for e in (lo, hi)})
    bins = []
    for a, b in zip(edges, edges[1:]):
        mid = (a + b) / 2
        ham = next((n for n, lo, hi in HAM_ALLOC if lo <= mid < hi), None)
        svc = next((n for n, lo, hi in SERVICE_BANDS if lo <= mid < hi), None)
        if ham or svc:
            bins.append((a, b, ham or svc, ham is not None))
    return bins


RF_BINS = _build_rf_bins()


def _bin_of(f):
    """Frequency (MHz) -> (lo, hi, label) of its bin, or None if uncovered."""
    for a, b, label, _ in RF_BINS:
        if a <= f < b:
            return (a, b, label)
    return None


def _risk_tier(score):
    if score >= 20:  return "HIGH"
    if score >= 3:   return "MODERATE"
    if score >= 0.3: return "LOW"
    return "CLEAR"


def human_w(w):
    """Watts -> compact human string; NaN/None -> em dash."""
    if w is None or (isinstance(w, float) and np.isnan(w)):
        return "—"
    if w >= 1e6: return f"{w/1e6:.2f} MW"
    if w >= 1e3: return f"{w/1e3:.0f} kW"
    return f"{w:.0f} W"


# thermal ramp (cool -> hot), matching the report-card mockup
_RAMP = [(0.0, (74, 139, 153)), (0.42, (198, 121, 27)),
         (0.72, (188, 80, 40)), (1.0, (204, 74, 48))]


def power_to_hex(w):
    """Map an ERP (watts) to a heat colour; no-ERP sources get a neutral grey."""
    if w is None or (isinstance(w, float) and np.isnan(w)):
        return "#8a97a3"
    t = min(1.0, max(0.0, (np.log10(max(w, 1.0)) - 2) / 4))   # 100 W..1 MW -> 0..1
    for i in range(1, len(_RAMP)):
        if t <= _RAMP[i][0]:
            (a, ca), (b, cb) = _RAMP[i-1], _RAMP[i]
            u = (t - a) / ((b - a) or 1)
            return "#%02x%02x%02x" % tuple(round(ca[j] + (cb[j]-ca[j])*u) for j in range(3))
    return "#%02x%02x%02x" % _RAMP[-1][1]


def _summit_analysis(joined):
    """Per-summit overload analysis keyed by summit code:
    per-ham-band risk tier, overall tier, total ERP, source counts, strongest."""
    info = {}
    for summit, g in joined.groupby("summit"):
        recv = {b: 0.0 for b, _ in HAM_BANDS}   # ham-band overload score (drives colour)
        bandp = {}                              # transmit-band ERP total (drives popup)
        for _, r in g.iterrows():
            p = r["rf_max_power_w"]
            if pd.isna(p):
                continue
            contrib = p / (max(r["distance_m"], 10.0) ** 2)
            fs = []
            for tok in str(r["rf_freqs_mhz"]).split(";"):
                try:
                    f = float(tok)
                except ValueError:
                    continue
                if not np.isnan(f):
                    fs.append(f)
            for f in fs:
                for b, fc in HAM_BANDS:
                    if 0.5 * fc <= f <= 2 * fc:
                        recv[b] += contrib
            hit = {bn for f in fs if (bn := _bin_of(f)) is not None}
            for key in (hit or {(None, None, "unknown freq")}):   # e.g. TV ch>36, blank freq
                bandp[key] = bandp.get(key, 0.0) + p
        overall = max((_risk_tier(recv[b]) for b, _ in HAM_BANDS), key=RISK_ORDER.index)
        pw = g["rf_max_power_w"]
        total = float(pw.sum(min_count=1)) if pw.notna().any() else float("nan")
        info[summit] = dict(overall=overall, total=total, bandp=bandp, n=len(g))
    return info


def to_caltopo_summits(summary, joined, path):
    """One point per summit that has ≥1 source, coloured by overload risk. The
    popup is terse: total ERP, then the ERP broken down by transmit band
    (biggest first). Directly importable into CalTopo."""
    info = _summit_analysis(joined)
    smeta = summary.set_index("summit")
    feats = []
    for code, d in info.items():
        s = smeta.loc[code]
        # RF-band breakdown, biggest ERP first: "88-108 MHz FM  425 kW". Bins
        # never span a ham band; in-ham sources appear under the ham label.
        band_lines = []
        for (a, b, label), w in sorted(d["bandp"].items(), key=lambda kv: -kv[1]):
            if a is None:
                head = label
            elif b >= 6000:                    # microwave catch-all: show ">lo"
                head = f">{a:g} MHz {label}"
            else:
                head = f"{a:g}-{b:g} MHz {label}"
            band_lines.append(f"{head}  {human_w(w)}")
        desc = (f'{s["name"]}\n'
                f'Total ERP  {human_w(d["total"])}\n'
                + "\n".join(band_lines))
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(s["lon"], 6), round(s["lat"], 6)]},
            "properties": {
                # Minimal props → a clean CalTopo tab. Label = compact SOTA code;
                # the full name + band breakdown lives in the popup (comments).
                "title": code,
                "description": desc,
                "marker-color": RISK_COLOR[d["overall"]],
                "marker-symbol": "point",
            },
        })
    with open(path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f)
    return len(feats)


def to_caltopo_sources(joined, path):
    """One point per distinct source, coloured by ERP (grey = no ERP figure),
    symbol by class. Directly importable into CalTopo."""
    feats, seen = [], set()
    for _, r in joined.iterrows():
        key = (r["rf_source_db"], r["rf_ref"], round(r["rf_lat"], 6), round(r["rf_lon"], 6))
        if key in seen:
            continue
        seen.add(key)
        p = None if pd.isna(r["rf_max_power_w"]) else r["rf_max_power_w"]
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(r["rf_lon"], 6), round(r["rf_lat"], 6)]},
            "properties": {
                "title": f'{r["rf_owner"] or r["rf_ref"]} — {human_w(p)}',
                "description": (f'{r["rf_source_db"]}  {r["rf_ref"]}\n'
                               f'ERP: {human_w(p)}\nFreqs: {r["rf_freqs_mhz"] or "—"} MHz\n'
                               f'Services: {r["rf_services"] or "—"}\n'
                               f'{round(r["distance_m"])} m from {r["summit"]}'),
                "db": r["rf_source_db"],
                "erp": human_w(p),
                "freqs_mhz": r["rf_freqs_mhz"],
                "services": r["rf_services"],
                "distance_m": r["distance_m"],
                "marker-color": power_to_hex(p),
                "marker-symbol": "point",
            },
        })
    with open(path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f)
    return len(feats)


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--association", default="W6",
                    help="SOTA association prefix, e.g. W6 (default), W7W, W0C")
    ap.add_argument("--us", action="store_true",
                    help="ignore --association and process all US associations")
    ap.add_argument("--radius", type=float, default=1000.0,
                    help="search radius around each summit, metres (default 1000)")
    ap.add_argument("--data-dir", default="fccdata", help="download/cache dir")
    ap.add_argument("--out-dir", default=".", help="output dir")
    ap.add_argument("--no-download", action="store_true",
                    help="use only files already present in --data-dir / flags")
    ap.add_argument("--summits-file"); ap.add_argument("--asr-file")
    ap.add_argument("--uls-file", action="append",
                    help="repeatable; pre-downloaded l_LM*.zip path(s)")
    ap.add_argument("--no-uls", action="store_true",
                    help="ASR structures only (skip the frequency/power layer)")
    ap.add_argument("--no-broadcast", action="store_true",
                    help="skip the FM/TV/AM broadcast layer (CDBS media files)")
    ap.add_argument("--broadcast-dir",
                    help="dir holding pre-downloaded CDBS media zips "
                         "(facility.zip, fm_eng_data.zip, tv_eng_data.zip, "
                         "am_eng_data.zip, am_ant_sys.zip); skips their download")
    args = ap.parse_args()

    os.makedirs(args.data_dir, exist_ok=True)
    os.makedirs(args.out_dir, exist_ok=True)
    prefixes = US_PREFIXES if args.us else (args.association,)
    tag = "US" if args.us else args.association.replace("/", "_")

    def fetch(url, fname, override):
        if override:
            return override
        dest = os.path.join(args.data_dir, fname)
        if args.no_download:
            return dest if os.path.exists(dest) else None
        return download(url, dest)

    log("[1/5] summits")
    sfile = fetch(URLS["summits"], "summitslist.csv", args.summits_file)
    summits = load_summits(sfile, prefixes)

    log("[2/5] ASR structures")
    afile = fetch(URLS["asr"], "r_tower.zip", args.asr_file)
    asr = load_asr(afile)

    uls = None
    if not args.no_uls:
        log("[3/5] ULS land-mobile + microwave (freq + power)")
        ufiles = args.uls_file or [fetch(u, os.path.basename(u), None)
                                   for u in URLS["uls"]]
        ufiles = [u for u in ufiles if u and os.path.exists(u)]
        if ufiles:
            uls = load_uls(ufiles)
        else:
            log("  (no ULS files available; structures only)")

    bcast = None
    if not args.no_broadcast:
        log("[4/5] broadcast FM/TV/AM (CDBS media)")
        if args.broadcast_dir:
            bf = {k: os.path.join(args.broadcast_dir, os.path.basename(u))
                  for k, u in URLS["broadcast"].items()}
        else:
            bf = {k: fetch(u, os.path.basename(u), None)
                  for k, u in URLS["broadcast"].items()}
        if all(bf[k] and os.path.exists(bf[k]) for k in URLS["broadcast"]):
            bcast = load_broadcast(bf["facility"], bf["fm_eng_data"],
                                   bf["tv_eng_data"], bf["am_eng_data"],
                                   bf["am_ant_sys"])
        else:
            missing = [k for k in URLS["broadcast"]
                       if not (bf[k] and os.path.exists(bf[k]))]
            log(f"  (broadcast files missing: {missing}; skipping FM/TV/AM)")

    log("[5/5] merge + spatial join")
    rf = merge_sources(asr, uls, bcast)
    joined = spatial_join(summits, rf, args.radius)
    summary = summarise(joined, summits)

    p_join = os.path.join(args.out_dir, f"{tag}_rf_sources.csv")
    p_sum = os.path.join(args.out_dir, f"{tag}_summit_summary.csv")
    p_geo = os.path.join(args.out_dir, f"{tag}_rf_sources.geojson")
    p_ct_sum = os.path.join(args.out_dir, f"{tag}_summits_caltopo.geojson")
    p_ct_src = os.path.join(args.out_dir, f"{tag}_sources_caltopo.geojson")
    joined.to_csv(p_join, index=False)
    summary.to_csv(p_sum, index=False)
    n_geo = to_geojson(joined, p_geo) if len(joined) else 0
    n_ct_sum = to_caltopo_summits(summary, joined, p_ct_sum) if len(joined) else 0
    n_ct_src = to_caltopo_sources(joined, p_ct_src) if len(joined) else 0

    log("\n=== done ===")
    log(f"summits processed : {len(summits)}")
    log(f"RF sources in DB   : {len(rf)}  (ASR {len(asr) if asr is not None else 0}"
        f" + ULS {0 if uls is None else len(uls)}"
        f" + broadcast {0 if bcast is None else len(bcast)})")
    log(f"summit<->source hits within {args.radius:g} m : {len(joined)}")
    if len(summary):
        hot = summary.sort_values('rf_source_count', ascending=False).head(8)
        log("hottest summits:")
        for _, r in hot.iterrows():
            extra = ""
            if "bcast_count" in r and r.get("bcast_count", 0):
                extra = f"  (incl. {int(r['bcast_count'])} broadcast)"
            log(f"  {r['summit']:<12} {str(r['name'])[:26]:<26} "
                f"sources={int(r['rf_source_count'])}{extra}")
    log(f"\nwrote:\n  {p_join}\n  {p_sum}\n  {p_geo}")
    log(f"  {p_ct_sum}  ({n_ct_sum} summit-risk points, CalTopo)")
    log(f"  {p_ct_src}  ({n_ct_src} source points, CalTopo)")


if __name__ == "__main__":
    main()
