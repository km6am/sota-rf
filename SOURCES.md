# Data sources

The mapper spatially joins the **SOTA summit list** against three families of
public **FCC** datasets, merged into one unified RF-source table. All FCC
"complete" snapshots refresh weekly — re-download for current data. Heights are
in metres. Downloads are cached in `fccdata/` (gitignored); the big zips time
out on `urllib`, so fetch with `curl -C -` and run with `--no-download`.

Live W6 totals (radius 1000 m): **1,225,982 RF sources** → 15,580 summit hits.

## Join target — SOTA summits
| File | What | Source |
|---|---|---|
| `summitslist.csv` | Every SOTA summit (code, name, lat/lon, altitude, points) — the peaks we join sources onto | [sotadata.org.uk/summitslist.csv](https://www.sotadata.org.uk/summitslist.csv) |

4,329 summits matched for association W6.

## 1. FCC ASR — antenna structures
Registered towers/masts: the **physical structures**.

| File | Records used | Fields | Source |
|---|---|---|---|
| `r_tower.zip` | CO / RA / EN | location, owner, height, structure type | [data.fcc.gov …/r_tower.zip](https://data.fcc.gov/download/pub/uls/complete/r_tower.zip) |

- **152,205** active structures with surface coordinates.
- Caveat: only structures above ~200 ft AGL (or near airports) must register —
  short ones may be absent. Not every record has surface coords (those are
  skipped).

## 2. FCC ULS — licensed transmitters (land-mobile + microwave)
The **frequency + power** layer. All three files share the ULS HD/EN/LO/FR
public-format record layout, so they flow through one loader.

| File | What | Power? | Source |
|---|---|---|---|
| `l_LMcomm.zip` | Land-mobile, commercial | yes (ERP) | [data.fcc.gov …/l_LMcomm.zip](https://data.fcc.gov/download/pub/uls/complete/l_LMcomm.zip) |
| `l_LMpriv.zip` | Land-mobile, private | yes (ERP) | [data.fcc.gov …/l_LMpriv.zip](https://data.fcc.gov/download/pub/uls/complete/l_LMpriv.zip) |
| `l_micro.zip` | Fixed point-to-point microwave (backhaul / STL) | **no** (freq + location only) | [data.fcc.gov …/l_micro.zip](https://data.fcc.gov/download/pub/uls/complete/l_micro.zip) |

- **1,043,082** licensed locations; fields: location, owner, frequency + power.
- Microwave (6/11/18/19 GHz, service codes CF/MG/MW/TI/TS) carries a frequency
  but blank power → `max_power_w` is NaN, which is correct.
- Caveat: **cellular** is geo-licensed, so exact site coords aren't in ULS — but
  the physical towers still appear via ASR.

## 3. FCC CDBS — broadcast FM/TV/AM
The **high-ERP offenders** (up to 1 MW TV). `facility.zip` supplies
identity/service/status; the engineering tables supply coords, power, and
frequency, joined by facility ID.

| File | What | Source |
|---|---|---|
| `facility.zip` | Facility identity, service, on-air status | [transition.fcc.gov …/facility.zip](https://transition.fcc.gov/Bureaus/MB/Databases/cdbs/facility.zip) |
| `fm_eng_data.zip` | FM engineering (coords, ERP, channel) | [transition.fcc.gov …/fm_eng_data.zip](https://transition.fcc.gov/Bureaus/MB/Databases/cdbs/fm_eng_data.zip) |
| `tv_eng_data.zip` | TV engineering (coords, ERP, channel) | [transition.fcc.gov …/tv_eng_data.zip](https://transition.fcc.gov/Bureaus/MB/Databases/cdbs/tv_eng_data.zip) |
| `am_eng_data.zip` | AM engineering (facility ↔ application-id link) | [transition.fcc.gov …/am_eng_data.zip](https://transition.fcc.gov/Bureaus/MB/Databases/cdbs/am_eng_data.zip) |
| `am_ant_sys.zip` | AM antenna system (coords + power) | [transition.fcc.gov …/am_ant_sys.zip](https://transition.fcc.gov/Bureaus/MB/Databases/cdbs/am_ant_sys.zip) |

- **30,695** licensed facilities (FM 19,407 + TV 6,903 + AM 4,385); fields:
  location, callsign, frequency/channel + ERP.
- Caveat: CDBS is a **frozen ~2024-01 snapshot** — broadcast filing moved to LMS,
  which is bot-blocked against scripted download. Good for the long-lived
  high-power incumbents, stale for recent low-power additions.

## Merge key
ULS `LO` records and broadcast `asrn` carry a **tower registration number**
(surfaced as `rf_link_reg`) that matches an ASR registration number — so a
transmitter is tied to the physical tower it sits on.
