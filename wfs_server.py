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
import os
import threading
import time
from datetime import datetime, timezone

from flask import Flask, Response, request

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
}

_cache = {}          # name -> (data, mtime, checked_at)
_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Data loading (hot-reload on mtime)
# --------------------------------------------------------------------------- #
def _path(name):
    return os.path.join(DATA_DIR, LAYERS[name][2])


def _load(path):
    with open(path, encoding="utf-8") as f:
        fc = json.load(f)
    lons, lats, props = [], [], []
    for feat in fc.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        c = geom.get("coordinates") or []
        if len(c) < 2 or c[0] is None or c[1] is None:
            continue
        lons.append(float(c[0]))
        lats.append(float(c[1]))
        props.append(feat.get("properties") or {})
    return {"lons": lons, "lats": lats, "props": props}


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


def parse_bbox(raw):
    """WFS BBOX -> (minx, miny, maxx, maxy). CalTopo (VERSION 1.1.0, no CRS
    token) sends {bottom},{left},{top},{right} = lat-first; a trailing CRS84
    token means lon-first."""
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) not in (4, 5):
        raise WfsError("InvalidParameterValue", f"BBOX must have 4 values, got {len(parts)}", "bbox")
    try:
        a, b, c, d = (float(p) for p in parts[:4])
    except ValueError:
        raise WfsError("InvalidParameterValue", f"Malformed BBOX: {raw!r}", "bbox")
    lat_first = not (len(parts) == 5 and "CRS84" in parts[4].upper())
    if lat_first and (abs(a) > 90 or abs(c) > 90):
        lat_first = False                    # values can't be latitudes
    return (b, a, d, c) if lat_first else (a, b, c, d)


def properties_for(props_keys, raw):
    if raw is None:
        return None                          # all
    by_lower = {k.lower(): k for row in props_keys for k in row}  # not used; kept simple below
    return raw


def select(name, data, bbox, prop_raw, count):
    lons, lats, props = data["lons"], data["lats"], data["props"]
    if bbox is None:
        idx = range(len(lons))
    else:
        minx, miny, maxx, maxy = bbox
        idx = [i for i in range(len(lons))
               if minx <= lons[i] <= maxx and miny <= lats[i] <= maxy]
    idx = list(idx)
    matched = len(idx)
    if count is not None:
        idx = idx[:count]

    want = None
    if prop_raw is not None:
        want = [p.strip() for p in prop_raw.split(",")
                if p.strip() and p.strip().lower() != "the_geom"]

    feats = []
    for i in idx:
        p = props[i]
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
        lon, lat = round(lons[i], COORD_DECIMALS), round(lats[i], COORD_DECIMALS)
        feats.append({
            "type": "Feature",
            "id": f"{name}.{i + 1}",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "geometry_name": "the_geom",
            "properties": out,
            "bbox": [lon, lat, lon, lat],
        })
    fc = {
        "type": "FeatureCollection",
        "features": feats,
        "totalFeatures": matched,
        "numberMatched": matched,
        "numberReturned": len(feats),
        "timeStamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::4326"}},
    }
    if feats:
        xs = [f["geometry"]["coordinates"][0] for f in feats]
        ys = [f["geometry"]["coordinates"][1] for f in feats]
        fc["bbox"] = [min(xs), min(ys), max(xs), max(ys)]
    return fc


def merge_collections(fcs):
    """Concatenate several per-layer FeatureCollections into one (WFS allows a
    GetFeature to name multiple typeNames; the result is their union). Feature
    ids are already layer-qualified (``Summits.n``/``Sources.n``) so they stay
    unique across the merge."""
    feats = [f for c in fcs for f in c["features"]]
    matched = sum(c.get("numberMatched", 0) for c in fcs)
    merged = {
        "type": "FeatureCollection",
        "features": feats,
        "totalFeatures": matched,
        "numberMatched": matched,
        "numberReturned": len(feats),
        "timeStamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::4326"}},
    }
    if feats:
        xs = [f["geometry"]["coordinates"][0] for f in feats]
        ys = [f["geometry"]["coordinates"][1] for f in feats]
        merged["bbox"] = [min(xs), min(ys), max(xs), max(ys)]
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
                '<DefaultSRS>urn:ogc:def:crs:EPSG::4326</DefaultSRS>'
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
    for p in (data["props"][:500] if data else []):
        for k in p:
            if k not in seen:
                seen.add(k)
                keys.append(k)
    return keys


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
        '<xsd:element name="the_geom" minOccurs="0" nillable="true" type="gml:PointPropertyType"/>'
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
                bbox = parse_bbox(params["bbox"]) if params.get("bbox") else None
                count_raw = params.get("count") or params.get("maxfeatures")
                count = int(count_raw) if count_raw else None
                prop = params.get("propertyname") or params.get("propertynames")
                fcs = []
                for nm in names:
                    data = get_data(nm)
                    if data is None:
                        raise WfsError("NoApplicableCode", f"Data for {NS}:{nm} not available")
                    fcs.append(select(nm, data, bbox, prop, count))
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
