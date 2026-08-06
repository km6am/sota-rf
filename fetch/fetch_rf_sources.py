#!/usr/bin/env python3
"""Download the FCC source data, rebuild the CalTopo RF-source layers, and
atomically swap them into place. Mirrors fetch_sota.py / fetch_superchargers.py.

Run monthly (systemd timer). Downloads are incremental where possible:
  * the big ULS/ASR complete files are only re-fetched when FCC's copy is newer
    than the local one (HTTP Last-Modified check), resumably (curl -C -);
  * the CDBS broadcast files are frozen (~2024-01, filing moved to LMS) so they
    are fetched once and then skipped forever.
The raw FCC zips live under data/fcc/ and persist between runs (that cache is
what makes the conditional/skip logic work). On any failure the previously
published data/rf_*.geojson are left untouched and the process exits non-zero,
so the failure lands in the journal.

Env:
  SOTA_WFS_ROOT       repo root (default: parent of this file's dir)
  SOTA_RF_ASSOCIATION "US" for national (default) or an association, e.g. "W6"
"""

import json
import os
import shutil
import subprocess
import sys
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(os.environ.get("SOTA_WFS_ROOT", Path(__file__).resolve().parent.parent))
DATA_DIR = ROOT / "data"
FCC_DIR = DATA_DIR / "fcc"                 # raw FCC cache (persists across runs)
PIPELINE = ROOT / "sota_rf_sources.py"
ASSOCIATION = os.environ.get("SOTA_RF_ASSOCIATION", "US")

DEST_SUMMITS = DATA_DIR / "rf_summits.geojson"     # the WFS layer (summit-risk)
DEST_SOURCES = DATA_DIR / "rf_sources.geojson"     # optional per-source layer
# If set, per-summit report cards + qrm_index.json are (re)published here — e.g.
# a static web docroot the propagation map serves (/opt/sota-matcher/web/rf).
REPORTS_DIR = os.environ.get("SOTA_RF_REPORTS_DIR")
# Public base URL where the report cards are hosted; adds a report link to each
# CalTopo summit popup (e.g. https://km6am.com/rf).
REPORT_BASE = os.environ.get("SOTA_RF_REPORT_BASE")
MIN_FEATURES = 100

# (url, filename, frozen). Frozen CDBS files never change → fetch once, then skip.
# The SOTA summit list rides along here too (not FCC, but the same conditional-GET
# cache): the pipeline runs --no-download and reads summitslist.csv straight from
# this dir, so it MUST be fetched here or load_summits gets a None path.
FCC_FILES = [
    ("https://www.sotadata.org.uk/summitslist.csv",                "summitslist.csv", False),
    ("https://data.fcc.gov/download/pub/uls/complete/r_tower.zip",  "r_tower.zip",  False),
    ("https://data.fcc.gov/download/pub/uls/complete/l_LMcomm.zip", "l_LMcomm.zip", False),
    ("https://data.fcc.gov/download/pub/uls/complete/l_LMpriv.zip", "l_LMpriv.zip", False),
    ("https://data.fcc.gov/download/pub/uls/complete/l_micro.zip",  "l_micro.zip",  False),
    ("https://transition.fcc.gov/Bureaus/MB/Databases/cdbs/facility.zip",    "facility.zip",    True),
    ("https://transition.fcc.gov/Bureaus/MB/Databases/cdbs/fm_eng_data.zip", "fm_eng_data.zip", True),
    ("https://transition.fcc.gov/Bureaus/MB/Databases/cdbs/tv_eng_data.zip", "tv_eng_data.zip", True),
    ("https://transition.fcc.gov/Bureaus/MB/Databases/cdbs/am_eng_data.zip", "am_eng_data.zip", True),
    ("https://transition.fcc.gov/Bureaus/MB/Databases/cdbs/am_ant_sys.zip",  "am_ant_sys.zip",  True),
]


def _remote_newer(url: str, dest: Path) -> bool:
    """True if the server's Last-Modified is newer than the local file (or if we
    can't tell / the file is missing — refetch to be safe)."""
    if not dest.exists():
        return True
    head = subprocess.run(["curl", "-sSfLI", "--connect-timeout", "30", url],
                          capture_output=True, text=True)
    if head.returncode != 0:
        return True
    for line in head.stdout.splitlines():
        if line.lower().startswith("last-modified:"):
            try:
                return parsedate_to_datetime(line.split(":", 1)[1].strip()).timestamp() \
                    > dest.stat().st_mtime
            except (ValueError, TypeError):
                return True
    return True


def fetch_one(url: str, dest: Path, frozen: bool) -> str:
    if frozen and dest.exists():
        return "cached (frozen)"
    if not _remote_newer(url, dest):
        return "unchanged"
    part = dest.with_suffix(dest.suffix + ".part")
    # resumable, retrying; write to .part then atomically rename into the cache.
    subprocess.run(["curl", "-sSfL", "--retry", "3", "--retry-delay", "5",
                    "--connect-timeout", "30", "-C", "-", "-o", str(part), url], check=True)
    os.replace(part, dest)
    return "downloaded"


def validate(path: Path) -> None:
    with open(path) as f:
        fc = json.load(f)
    if fc.get("type") != "FeatureCollection":
        raise ValueError(f"not a FeatureCollection: type={fc.get('type')!r}")
    n = len(fc.get("features", []))
    if n < MIN_FEATURES:
        raise ValueError(f"only {n} features (expected > {MIN_FEATURES})")


def main() -> int:
    FCC_DIR.mkdir(parents=True, exist_ok=True)
    staging = DATA_DIR / ".staging"        # same filesystem as data/ -> os.replace is atomic
    try:
        for url, name, frozen in FCC_FILES:
            print(f"  {name}: {fetch_one(url, FCC_DIR / name, frozen)}", flush=True)

        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        arg = "--us" if ASSOCIATION == "US" else f"--association={ASSOCIATION}"
        cmd = [sys.executable, str(PIPELINE), arg, "--radius", "1000",
               "--no-download", "--data-dir", str(FCC_DIR), "--out-dir", str(staging)]
        if REPORTS_DIR:
            cmd += ["--reports-dir", str(staging / "bundle")]
        if REPORT_BASE:
            cmd += ["--report-base-url", REPORT_BASE]
        # Terrain diffraction cache (built off-box where the DEM lives, shipped
        # into data/). Read-only here — no --dem-dir, so uncached pairs assume
        # clear LOS. Missing file just means terrain is skipped this run.
        tcache = DATA_DIR / "terrain_cache.json"
        if tcache.exists():
            cmd += ["--terrain-cache", str(tcache)]
        subprocess.run(cmd, check=True)

        tag = "US" if ASSOCIATION == "US" else ASSOCIATION.replace("/", "_")
        built = staging / f"{tag}_summits_caltopo.geojson"
        validate(built)
        os.replace(built, DEST_SUMMITS)
        src = staging / f"{tag}_sources_caltopo.geojson"
        if src.exists():
            os.replace(src, DEST_SOURCES)
        # publish the report bundle (reports/ + qrm_index.json) into the web docroot
        bundle = staging / "bundle"
        if REPORTS_DIR and bundle.is_dir():
            os.makedirs(REPORTS_DIR, exist_ok=True)
            subprocess.run(["rsync", "-a", "--delete", str(bundle) + "/", REPORTS_DIR + "/"], check=True)
    except Exception as exc:
        print(f"fetch_rf_sources failed: {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    print(f"fetch_rf_sources: updated {DEST_SUMMITS} ({DEST_SUMMITS.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
