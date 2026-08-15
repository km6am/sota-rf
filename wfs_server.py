#!/usr/bin/env python3
"""Minimal WFS server for the SOTA RF-source layers — for CalTopo ingestion.

CalTopo only ever issues GetCapabilities and GetFeature (with a BBOX, GeoJSON
output), so this implements exactly that and nothing more: no GeoServer, no
GDAL, no database. Data is pre-built GeoJSON from sota_rf_sources.py, loaded
into memory and hot-reloaded when the file mtime changes (so the monthly
rebuild is picked up with no restart).

Layers (namespace ``rf``), from $SOTA_RF_DATA (default ./data):
  rf:Summits  <-  rf_summits.geojson   (one pin per summit, coloured by risk)
  rf:Sources  <-  rf_sources.geojson   (one pin per individual RF source)

Run behind nginx over TLS; it trusts X-Forwarded-* so the capabilities URLs
come out https. Start with `python wfs_server.py` (waitress, port
$SOTA_RF_PORT, default 8080) or point a waitress/gunicorn at `create_app()`.
"""

import json
import math
import os
import threading
import time
from datetime import datetime, timezone

from flask import Flask, Response, request

# --------------------------------------------------------------------------- #
# Projection support. Data is stored WGS84 lon/lat (EPSG:4326); CalTopo is a Web
# Mercator (EPSG:3857) client and picks a CRS from the capabilities, so we
# advertise both and reproject on demand when a request asks for 3857.
# --------------------------------------------------------------------------- #
_R3857 = 6378137.0
_MERC_TOKENS = ("3857", "900913", "102100", "3785", "54004")


def _is_mercator(srs):
    s = (srs or "").lower()
    return any(tok in s for tok in _MERC_TOKENS)


def _fwd_merc(lon, lat):
    """WGS84 lon/lat -> EPSG:3857 easting/northing (metres)."""
    lat = max(min(lat, 85.06), -85.06)
    x = _R3857 * math.radians(lon)
    y = _R3857 * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y


def _inv_merc(x, y):
    """EPSG:3857 easting/northing (metres) -> WGS84 lon/lat."""
    lon = math.degrees(x / _R3857)
    lat = math.degrees(2 * math.atan(math.exp(y / _R3857)) - math.pi / 2)
    return lon, lat

DATA_DIR = os.environ.get(
    "SOTA_RF_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
NS = "rf"
XML = "text/xml"
JSON = "application/json"
RELOAD_SECONDS = 15
COORD_DECIMALS = 6
ALWAYS_SERVED = ("marker-color", "marker-symbol")   # style hints served even if PROPERTYNAME omits them

# local name -> (title, abstract, geojson filename)
LAYERS = {
    "Summits": ("SOTA Summit RF Risk",
                "One pin per SOTA summit with an in-range RF source, coloured by overload risk; "
                "popup = total ERP and the ERP by transmit band.",
                "rf_summits.geojson"),
    "Sources": ("SOTA RF Sources",
                "Individual fixed RF sources (FCC ASR + ULS + CDBS) near SOTA summits, coloured by ERP.",
                "rf_sources.geojson"),
    "RefSummits": ("SOTA Summits (RF-enriched)",
                   "Reference SOTA summits (original markers kept) with our RF report-card "
                   "link + RFI summary added to each summit's detail.",
                   "rf_refsummits.geojson"),
    "RefZones": ("SOTA Activation Zones",
                 "25 m activation-zone polygons per summit, tinted by RF overload risk.",
                 "rf_refzones.geojson"),
}

_cache = {}          # name -> (data, mtime, checked_at)
_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Data loading (hot-reload on mtime)
# --------------------------------------------------------------------------- #
def _path(name):
    return os.path.join(DATA_DIR, LAYERS[name][2])


def _coords_xy(coords, xs, ys):
    """Collect all (x, y) pairs from an arbitrary GeoJSON coordinate nesting."""
    if not coords:
        return
    if isinstance(coords[0], (int, float)):
        if coords[0] is not None and coords[1] is not None:
            xs.append(float(coords[0])); ys.append(float(coords[1]))
        return
    for c in coords:
        _coords_xy(c, xs, ys)


def _map_coords(coords, fn):
    """Rebuild a coordinate nesting with fn applied to each (x, y) leaf."""
    if coords and isinstance(coords[0], (int, float)):
        return list(fn(coords[0], coords[1]))
    return [_map_coords(c, fn) for c in coords]


def _load(path):
    with open(path, encoding="utf-8") as f:
        fc = json.load(f)
    feats = []
    for feat in fc.get("features", []):
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates")
        if not geom.get("type") or not coords:
            continue
        xs, ys = [], []
        _coords_xy(coords, xs, ys)
        if not xs:
            continue
        feats.append({"geom": geom, "props": feat.get("properties") or {},
                      "bbox": (min(xs), min(ys), max(xs), max(ys))})
    return {"feats": feats}


def get_data(name):
    path = _path(name)
    now = time.monotonic()
    ent = _cache.get(name)
    if ent and now - ent[2] < RELOAD_SECONDS:
        return ent[0]
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return ent[0] if ent else None      # keep serving stale data if file vanishes mid-swap
    if ent and ent[1] == mtime:
        _cache[name] = (ent[0], mtime, now)
        return ent[0]
    with _lock:
        data = _load(path)
        _cache[name] = (data, mtime, time.monotonic())
        return data


def available():
    return [n for n in LAYERS if os.path.exists(_path(n))]


def resolve(typename):
    local = (typename or "").split(":", 1)[-1].strip()
    for n in LAYERS:
        if n.lower() == local.lower():
            return n
    return None


# --------------------------------------------------------------------------- #
# WFS request handling
# --------------------------------------------------------------------------- #
class WfsError(Exception):
    def __init__(self, code, text, locator=None):
        super().__init__(text)
        self.code, self.locator = code, locator


def parse_bbox(raw, mercator=False):
    """WFS BBOX -> (min_lon, min_lat, max_lon, max_lat), always in lon/lat for
    filtering. A 4326 bbox (CalTopo, VERSION 1.1.0, no CRS token) is
    {bottom},{left},{top},{right} = lat-first; a trailing CRS84 token means
    lon-first. A ``mercator`` bbox is EPSG:3857 metres, easting-first
    ({minx},{miny},{maxx},{maxy}); its corners are inverse-projected to lon/lat.
    A trailing 3857-style CRS token also forces mercator."""
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) not in (4, 5):
        raise WfsError("InvalidParameterValue", f"BBOX must have 4 values, got {len(parts)}", "bbox")
    try:
        a, b, c, d = (float(p) for p in parts[:4])
    except ValueError:
        raise WfsError("InvalidParameterValue", f"Malformed BBOX: {raw!r}", "bbox")
    if len(parts) == 5 and _is_mercator(parts[4]):
        mercator = True
    if mercator:
        lon0, lat0 = _inv_merc(a, b)          # easting-first (E, N)
        lon1, lat1 = _inv_merc(c, d)
        return (min(lon0, lon1), min(lat0, lat1), max(lon0, lon1), max(lat0, lat1))
    lat_first = not (len(parts) == 5 and "CRS84" in parts[4].upper())
    if lat_first and (abs(a) > 90 or abs(c) > 90):
        lat_first = False                    # values can't be latitudes
    return (b, a, d, c) if lat_first else (a, b, c, d)


def properties_for(props_keys, raw):
    if raw is None:
        return None                          # all
    by_lower = {k.lower(): k for row in props_keys for k in row}  # not used; kept simple below
    return raw


def select(name, data, bbox, prop_raw, count, mercator=False):
    entries = data["feats"]
    if bbox is None:
        idx = range(len(entries))
    else:
        minx, miny, maxx, maxy = bbox
        idx = [i for i, e in enumerate(entries)              # bbox-overlap (works for any geometry)
               if not (e["bbox"][2] < minx or e["bbox"][0] > maxx
                       or e["bbox"][3] < miny or e["bbox"][1] > maxy)]
    idx = list(idx)
    matched = len(idx)
    if count is not None:
        idx = idx[:count]

    want = None
    if prop_raw is not None:
        want = [p.strip() for p in prop_raw.split(",")
                if p.strip() and p.strip().lower() != "the_geom"]

    if mercator:
        project = lambda x, y: (round(_fwd_merc(x, y)[0], 2), round(_fwd_merc(x, y)[1], 2))
    else:
        project = lambda x, y: (round(x, COORD_DECIMALS), round(y, COORD_DECIMALS))

    feats, fxs, fys = [], [], []
    for i in idx:
        e = entries[i]
        p = e["props"]
        if want is None:
            out = dict(p)
        else:
            by_lower = {k.lower(): k for k in p}
            out = {}
            for w in want:
                k = by_lower.get(w.lower())
                if k is not None:
                    out[k] = p[k]
            for k in ALWAYS_SERVED:
                if k in p:
                    out[k] = p[k]
        geom = e["geom"]
        coords = _map_coords(geom["coordinates"], project)
        gxs, gys = [], []
        _coords_xy(coords, gxs, gys)
        fxs += [min(gxs), max(gxs)]; fys += [min(gys), max(gys)]
        feats.append({
            "type": "Feature",
            "id": f"{name}.{i + 1}",
            "geometry": {"type": geom["type"], "coordinates": coords},
            "geometry_name": "the_geom",
            "properties": out,
            "bbox": [min(gxs), min(gys), max(gxs), max(gys)],
        })
    crs_name = "urn:ogc:def:crs:EPSG::3857" if mercator else "urn:ogc:def:crs:EPSG::4326"
    fc = {
        "type": "FeatureCollection",
        "features": feats,
        "totalFeatures": matched,
        "numberMatched": matched,
        "numberReturned": len(feats),
        "timeStamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "crs": {"type": "name", "properties": {"name": crs_name}},
    }
    if feats:
        fc["bbox"] = [min(fxs), min(fys), max(fxs), max(fys)]
    return fc


def merge_collections(fcs):
    """Concatenate several per-layer FeatureCollections into one (WFS allows a
    GetFeature to name multiple typeNames; the result is their union). Feature
    ids are already layer-qualified (``Summits.n``/``Sources.n``) so they stay
    unique across the merge."""
    feats = [f for c in fcs for f in c["features"]]
    matched = sum(c.get("numberMatched", 0) for c in fcs)
    crs = fcs[0]["crs"] if fcs else {"type": "name",
                                     "properties": {"name": "urn:ogc:def:crs:EPSG::4326"}}
    merged = {
        "type": "FeatureCollection",
        "features": feats,
        "totalFeatures": matched,
        "numberMatched": matched,
        "numberReturned": len(feats),
        "timeStamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "crs": crs,
    }
    bboxes = [c["bbox"] for c in fcs if c.get("bbox")]
    if bboxes:
        merged["bbox"] = [min(b[0] for b in bboxes), min(b[1] for b in bboxes),
                          max(b[2] for b in bboxes), max(b[3] for b in bboxes)]
    return merged


# --------------------------------------------------------------------------- #
# XML documents
# --------------------------------------------------------------------------- #
def _xesc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def capabilities_110(base, names=None, endpoint="/geoserver/wfs"):
    """WFS 1.1.0 GetCapabilities — the ONLY version CalTopo's Auto-Configure
    parses, and only in GeoServer's dialect. The quirks below were reverse-
    engineered from CalTopo's parser (thanks km6am): 1.1 namespaces (wfs, ows —
    NOT the 2.0/1.1.1 variants); FeatureType elements in the wfs namespace (via
    the default xmlns); <DefaultSRS> (not DefaultCRS); <ows:Value> directly in
    <ows:Parameter> with NO <ows:AllowedValues> wrapper; and GetFeature must
    advertise resultType FIRST, outputFormat SECOND (CalTopo reads by position).

    ``names`` limits which feature types are advertised (default: all); a
    per-layer ``endpoint`` (e.g. /geoserver/summits/wfs) makes a URL expose a
    single layer, so CalTopo auto-configures it as its own separate layer."""
    wfs = f"{base}{endpoint}"
    fts = ""
    for n in (names if names is not None else available()):
        title, abstract, _ = LAYERS[n]
        fts += (f'<FeatureType><Name>{NS}:{n}</Name><Title>{_xesc(title)}</Title>'
                f'<Abstract>{_xesc(abstract)}</Abstract>'
                # CalTopo (a Web Mercator client) judges projection compatibility
                # off DefaultSRS ONLY and rejects 4326 — it can't reproject in the
                # WFS beta — so advertise EPSG:3857 as the default and 4326 as an
                # alternative. The server reprojects output + bbox on demand.
                '<DefaultSRS>urn:ogc:def:crs:EPSG::3857</DefaultSRS>'
                '<OtherSRS>urn:ogc:def:crs:EPSG::4326</OtherSRS>'
                '<OtherSRS>EPSG:3857</OtherSRS>'
                '<OtherSRS>EPSG:4326</OtherSRS>'
                '<OutputFormats><Format>application/json</Format></OutputFormats>'
                '<ows:WGS84BoundingBox><ows:LowerCorner>-180 -90</ows:LowerCorner>'
                '<ows:UpperCorner>180 90</ows:UpperCorner></ows:WGS84BoundingBox></FeatureType>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<wfs:WFS_Capabilities version="1.1.0" xmlns="http://www.opengis.net/wfs" '
        'xmlns:wfs="http://www.opengis.net/wfs" xmlns:ows="http://www.opengis.net/ows" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:{NS}="https://km6am.com/{NS}">'
        '<ows:ServiceIdentification><ows:Title>SOTA RF WFS</ows:Title>'
        '<ows:ServiceType>WFS</ows:ServiceType>'
        '<ows:ServiceTypeVersion>1.1.0</ows:ServiceTypeVersion></ows:ServiceIdentification>'
        '<ows:OperationsMetadata>'
        f'<ows:Operation name="GetCapabilities"><ows:DCP><ows:HTTP>'
        f'<ows:Get xlink:href="{wfs}?"/></ows:HTTP></ows:DCP></ows:Operation>'
        f'<ows:Operation name="GetFeature"><ows:DCP><ows:HTTP>'
        f'<ows:Get xlink:href="{wfs}?"/></ows:HTTP></ows:DCP>'
        '<ows:Parameter name="resultType"><ows:Value>results</ows:Value>'
        '<ows:Value>hits</ows:Value></ows:Parameter>'
        # CalTopo's Auto-Configure parser requires ≥2 <ows:Value> here; with a
        # single value it rejects the doc. Both name the same GeoJSON output.
        '<ows:Parameter name="outputFormat"><ows:Value>application/json</ows:Value>'
        '<ows:Value>json</ows:Value></ows:Parameter>'
        '</ows:Operation></ows:OperationsMetadata>'
        f'<wfs:FeatureTypeList>{fts}</wfs:FeatureTypeList>'
        '</wfs:WFS_Capabilities>')


def capabilities_200(base, names=None, endpoint="/geoserver/wfs"):
    """WFS 2.0.0 GetCapabilities, for non-CalTopo clients that ask for 2.0."""
    wfs = f"{base}{endpoint}"
    fts = ""
    for n in (names if names is not None else available()):
        title, abstract, _ = LAYERS[n]
        fts += (f'<wfs:FeatureType><wfs:Name>{NS}:{n}</wfs:Name><wfs:Title>{_xesc(title)}</wfs:Title>'
                f'<wfs:Abstract>{_xesc(abstract)}</wfs:Abstract>'
                '<wfs:DefaultCRS>urn:ogc:def:crs:EPSG::4326</wfs:DefaultCRS>'
                '<wfs:OtherCRS>urn:ogc:def:crs:EPSG::3857</wfs:OtherCRS>'
                '<wfs:OutputFormats><wfs:Format>application/json</wfs:Format></wfs:OutputFormats>'
                '<ows:WGS84BoundingBox><ows:LowerCorner>-180 -90</ows:LowerCorner>'
                '<ows:UpperCorner>180 90</ows:UpperCorner></ows:WGS84BoundingBox></wfs:FeatureType>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<wfs:WFS_Capabilities version="2.0.0" xmlns:wfs="http://www.opengis.net/wfs/2.0" '
        'xmlns:ows="http://www.opengis.net/ows/1.1" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'xmlns:{NS}="https://km6am.com/{NS}">'
        '<ows:ServiceIdentification><ows:Title>SOTA RF WFS</ows:Title>'
        '<ows:ServiceType>WFS</ows:ServiceType>'
        '<ows:ServiceTypeVersion>2.0.0</ows:ServiceTypeVersion></ows:ServiceIdentification>'
        '<ows:OperationsMetadata>'
        f'<ows:Operation name="GetCapabilities"><ows:DCP><ows:HTTP>'
        f'<ows:Get xlink:href="{wfs}?"/></ows:HTTP></ows:DCP></ows:Operation>'
        f'<ows:Operation name="GetFeature"><ows:DCP><ows:HTTP>'
        f'<ows:Get xlink:href="{wfs}?"/></ows:HTTP></ows:DCP>'
        '<ows:Parameter name="outputFormat"><ows:AllowedValues>'
        '<ows:Value>application/json</ows:Value></ows:AllowedValues></ows:Parameter>'
        '</ows:Operation></ows:OperationsMetadata>'
        f'<wfs:FeatureTypeList>{fts}</wfs:FeatureTypeList>'
        '</wfs:WFS_Capabilities>')


def _feature_columns(name):
    """Real property names served for a layer (union over the loaded features),
    so DescribeFeatureType can enumerate them — CalTopo keys its field/label
    handling off these, not off a lone the_geom."""
    data = get_data(name)
    keys, seen = [], set()
    for e in (data["feats"][:500] if data else []):
        for k in e["props"]:
            if k not in seen:
                seen.add(k)
                keys.append(k)
    return keys


def _geom_prop_type(name):
    """gml geometry property type for a layer, from its first feature."""
    data = get_data(name)
    gtype = data["feats"][0]["geom"]["type"] if data and data["feats"] else "Point"
    return {"Polygon": "gml:SurfacePropertyType",
            "MultiPolygon": "gml:MultiSurfacePropertyType",
            "LineString": "gml:CurvePropertyType"}.get(gtype, "gml:PointPropertyType")


def describe_xml(name):
    fields = "".join(
        f'<xsd:element name="{_xesc(k)}" minOccurs="0" nillable="true" type="xsd:string"/>'
        for k in _feature_columns(name))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:gml="http://www.opengis.net/gml" '
        f'xmlns:{NS}="https://km6am.com/{NS}" targetNamespace="https://km6am.com/{NS}" '
        'elementFormDefault="qualified" version="1.0">'
        '<xsd:import namespace="http://www.opengis.net/gml" '
        'schemaLocation="http://schemas.opengis.net/gml/3.1.1/base/gml.xsd"/>'
        f'<xsd:element name="{name}" type="{NS}:{name}Type" substitutionGroup="gml:_Feature"/>'
        f'<xsd:complexType name="{name}Type"><xsd:complexContent>'
        '<xsd:extension base="gml:AbstractFeatureType"><xsd:sequence>'
        f'{fields}'
        f'<xsd:element name="the_geom" minOccurs="0" nillable="true" type="{_geom_prop_type(name)}"/>'
        '</xsd:sequence></xsd:extension></xsd:complexContent></xsd:complexType></xsd:schema>')


def exception_xml(code, text, locator=None):
    loc = f' locator="{_xesc(locator)}"' if locator else ""
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1" version="2.0.0">'
            f'<ows:Exception exceptionCode="{code}"{loc}>'
            f'<ows:ExceptionText>{_xesc(text)}</ows:ExceptionText></ows:Exception></ows:ExceptionReport>')


# --------------------------------------------------------------------------- #
# Flask app
# --------------------------------------------------------------------------- #
def create_app():
    app = Flask(__name__)

    @app.after_request
    def cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    def base_url():
        proto = request.headers.get("X-Forwarded-Proto", request.scheme)
        host = request.headers.get("X-Forwarded-Host", request.host)
        return f"{proto}://{host}"

    def xml(body, status=200):
        return Response(body, status=status, content_type=XML)

    @app.route("/wfs")
    @app.route("/geoserver/wfs")
    @app.route("/geoserver/<ns>/wfs")
    def wfs(ns=None):
        params = {k.lower(): v for k, v in request.args.items()}
        req = params.get("request", "").lower()
        # A namespaced path (/geoserver/summits/wfs) pins this endpoint to a
        # single layer: capabilities advertise only it, and GetFeature serves
        # only it. That lets CalTopo auto-configure Summits and Sources as two
        # independent layers. /geoserver/wfs (ns=None) still exposes both.
        ns_layer = resolve(ns) if ns else None
        endpoint = f"/geoserver/{ns}/wfs" if ns_layer else "/geoserver/wfs"
        allowed = [ns_layer] if ns_layer else available()
        try:
            if req == "getcapabilities":
                version = (params.get("version") or params.get("acceptversions", "").split(",")[0]
                           or "1.1.0").strip()
                # CalTopo Auto-Configure always asks for 1.1.0 and parses only that.
                return xml(capabilities_110(base_url(), allowed, endpoint) if version.startswith("1.1")
                           else capabilities_200(base_url(), allowed, endpoint))
            if req == "describefeaturetype":
                raw = params.get("typenames") or params.get("typename")
                name = resolve(raw) if raw else (allowed or [None])[0]
                if name is None or name not in allowed:
                    raise WfsError("InvalidParameterValue", f"Unknown type name: {raw}", "typeNames")
                return xml(describe_xml(name))
            if req == "getfeature":
                raw = params.get("typenames") or params.get("typename")
                if not raw:
                    raise WfsError("MissingParameterValue", "typeNames is required", "typeNames")
                # A GetFeature may name several feature types (comma-separated) —
                # CalTopo asks for "rf:Summits,rf:Sources" in one call — and the
                # response is their merged FeatureCollection (as GeoServer does).
                names = []
                for token in raw.split(","):
                    token = token.strip()
                    if not token:
                        continue
                    nm = resolve(token)
                    if nm is None:
                        raise WfsError("InvalidParameterValue", f"Unknown type name: {token}", "typeNames")
                    if nm not in allowed:
                        continue          # namespaced endpoint ignores other layers
                    if nm not in names:
                        names.append(nm)
                if not names:
                    raise WfsError("InvalidParameterValue", f"Unknown type name: {raw}", "typeNames")
                out_fmt = params.get("outputformat", JSON)
                if "json" not in out_fmt.lower():
                    raise WfsError("InvalidParameterValue",
                                   f"Only GeoJSON output is supported, got {out_fmt!r}", "outputFormat")
                # Reproject to EPSG:3857 when the client asks for it — via SRSNAME,
                # a mercator CRS token on the BBOX, or (CalTopo omits SRSNAME) a
                # BBOX whose magnitude can only be metres, not degrees.
                mercator = _is_mercator(params.get("srsname"))
                if not mercator and params.get("bbox"):
                    try:
                        if max(abs(float(v)) for v in params["bbox"].split(",")[:4]) > 180.0:
                            mercator = True
                    except ValueError:
                        pass
                bbox = parse_bbox(params["bbox"], mercator) if params.get("bbox") else None
                count_raw = params.get("count") or params.get("maxfeatures")
                count = int(count_raw) if count_raw else None
                prop = params.get("propertyname") or params.get("propertynames")
                fcs = []
                for nm in names:
                    data = get_data(nm)
                    if data is None:
                        raise WfsError("NoApplicableCode", f"Data for {NS}:{nm} not available")
                    fcs.append(select(nm, data, bbox, prop, count, mercator))
                fc = fcs[0] if len(fcs) == 1 else merge_collections(fcs)
                return Response(json.dumps(fc, separators=(",", ":")), content_type=JSON)
            raise WfsError("OperationNotSupported" if req else "MissingParameterValue",
                           f"Unsupported request: {params.get('request', '(missing)')}", "request")
        except WfsError as exc:
            return xml(exception_xml(exc.code, str(exc), exc.locator), 400)
        except Exception as exc:                       # noqa: BLE001 - never 500 to CalTopo
            return xml(exception_xml("NoApplicableCode", str(exc)), 400)

    @app.route("/healthz")
    def healthz():
        return Response("ok " + ",".join(available()), content_type="text/plain")

    return app


if __name__ == "__main__":
    from waitress import serve
    port = int(os.environ.get("SOTA_RF_PORT", "8080"))
    print(f"sota-rf WFS on 0.0.0.0:{port}  data={DATA_DIR}  layers={available()}", flush=True)
    serve(create_app(), host="127.0.0.1", port=port, threads=6,
          trusted_proxy="127.0.0.1", trusted_proxy_count=1,
          trusted_proxy_headers={"x-forwarded-for", "x-forwarded-host",
                                 "x-forwarded-proto", "x-forwarded-port"})
