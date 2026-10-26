# Data sources, credits and terms

The MIT license in [LICENSE](LICENSE) covers this project's own code. It does
not cover the third-party data the code downloads or the outputs that contain
that data (CSV, GeoJSON, report cards, map tiles). Each of those keeps its own
terms, listed below.

| Source | Used for | Terms | Credit line |
|---|---|---|---|
| FCC ASR, ULS, CDBS | Towers, licensed transmitters, broadcast stations | US federal data, not subject to copyright (17 USC 105). Don't imply FCC endorsement. | Transmitters: FCC |
| NOAA / NWS Weather Radio list | NWR transmitters | US federal data | NOAA Weather Radio: NWS |
| SOTA summit list (sotadata.org.uk) | Summit codes, names, coordinates, heights | No published reuse terms. **Permission to republish requested from the SOTA Management Team; not yet granted.** | Summits: Summits on the Air (sota.org.uk) |
| AWS Terrain Tiles (SRTM, 3DEP) | Terrain shadowing | Attribution required ([Tilezen attribution](https://github.com/tilezen/joerd/blob/master/docs/attribution.md)). US sources are public domain. | Terrain: USGS SRTM/3DEP via AWS Terrain Tiles (Mapzen) |
| Esri World Light Gray | Field Estimate Map basemap only | Esri terms. Commercial-use terms not yet confirmed. The service is in mature support. | Basemap © Esri, HERE, Garmin, © OpenStreetMap contributors |
| Leaflet 1.9.4, IBM Plex | Map library, web fonts | BSD-2-Clause, SIL OFL 1.1 | — |

## Our outputs

Field strengths are free-space estimates with terrain applied, built from
registered ERP. They are **not measurements and not an RF-exposure (MPE)
compliance assessment**. Published surveys near Sutro Tower measure well below
the model close to the mast. A license for the derived layers is deferred until
SOTA answers on the summit data.

## Ground-truth notes (developer mode)

The ground-truth notes service (in development, not yet live) stores each
note's call sign, text, optional measurement, and the model values at the time. It also
logs the poster's IP address and browser string for abuse control and deletes
both after 30 days. Deleting a note hides it from the map and from exports, but the
row stays in the database (`deleted` timestamp set).
