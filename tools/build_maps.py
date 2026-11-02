"""Build the map files for the Power BI Shape maps.

Two maps, each one GeoJSON FeatureCollection in reporting/powerbi/maps/:

- `nl`: Dutch municipalities from PDOK (Kadaster `gemeentegebied`, WGS84), the same source
  as Notebook 32. Each feature's `id` and `municipality_code` equal
  dim_municipality[municipality_code] (e.g. "0014"); `municipality_name` and
  `province_name` are there for matching by name.
- `world`: countries from Natural Earth 1:10m Admin 0 (the version and SHA-256 pinned in
  Notebook 34), coded with notebooks/country_codes.py like silver_country_boundaries. Units
  that share a WDI code are merged (Jersey and Guernsey → CHI, Åland → FIN); Antarctica is
  left out. Each feature's `id` and `country_code` equal dim_country[country_code].

For both:
- geometry simplified with shapely (topology-preserving, tolerance in degrees) and snapped to
  a grid, so reruns on the same boundaries give byte-identical output;
- exterior rings clockwise, holes counter-clockwise. The Shape map draws with d3, which reads
  a counter-clockwise ring (the RFC 7946 order both sources use) as "the whole globe minus
  this polygon" and fills the whole visual.

Each polygon is simplified on its own, so neighbours can show slivers of up to about the
tolerance; invisible at the scale of each map.

    python -m tools.build_maps nl
    python -m tools.build_maps world
    python -m tools.build_maps nl --source gemeentegebied.geojson  # offline, from a file

Boundaries: © Kadaster via PDOK, CC BY 4.0; Made with Natural Earth (public domain).
Attribution is in the README and on the Methods page.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import requests
from shapely import set_precision
from shapely.geometry import MultiPolygon, mapping, shape
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

REPO = Path(__file__).resolve().parent.parent
MAPS = REPO / "reporting" / "powerbi" / "maps"

sys.path.insert(0, str(REPO / "notebooks"))
from country_codes import EXCLUDED, resolve  # noqa: E402

PDOK_URL = ("https://api.pdok.nl/kadaster/bestuurlijkegebieden/ogc/v1/"
            "collections/gemeentegebied/items?f=json&limit=1000")
# Same file as Notebook 34 (tests check that the constants agree).
NE_VERSION = "v5.1.2"
NE_URL = (f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{NE_VERSION}/"
          "geojson/ne_10m_admin_0_countries.geojson")
NE_SHA256 = "239eec57ac17f100a11e2536cffc56752c318b50ae765b0918ff7aab4ce8f255"

POLYGONAL = {"Polygon", "MultiPolygon"}


@dataclass(frozen=True)
class MapSpec:
    output: Path
    tolerance: float  # degrees
    decimals: int
    expected_min: int  # sanity check on the number of features


SPECS = {
    # ~35-55 m in the Netherlands; ~1 m grid; 342 municipalities (2026)
    "nl": MapSpec(MAPS / "nl_municipalities.geojson", 0.0005, 5, 300),
    # ~5 km at the equator (1.0 MB; 0.02 gives 1.9 MB with no visible gain at world scale);
    # ~100 m grid; 258 Natural Earth units → 249 codes, the rows of dim_country
    "world": MapSpec(MAPS / "world_countries.geojson", 0.05, 3, 240),
}


class BuildError(Exception):
    pass


def download_pdok(url: str = PDOK_URL) -> list[dict]:
    """Return all features, following the OGC API `next` links."""
    features, next_url = [], url
    while next_url:
        response = requests.get(next_url, timeout=120)
        response.raise_for_status()
        page = response.json()
        features += page["features"]
        next_url = next((link["href"] for link in page.get("links", [])
                         if link.get("rel") == "next"), None)
    return features


def download_natural_earth(url: str = NE_URL) -> list[dict]:
    response = requests.get(url, timeout=300)
    response.raise_for_status()
    digest = hashlib.sha256(response.content).hexdigest()
    if digest != NE_SHA256:
        raise BuildError(f"Natural Earth file changed: sha256 {digest}")
    return response.json()["features"]


def _round(coords, decimals: int):
    if isinstance(coords[0], (int, float)):
        return [round(c, decimals) for c in coords]
    return [_round(c, decimals) for c in coords]


def clockwise(geometry):
    """Exterior rings clockwise, holes counter-clockwise (the order d3 expects)."""
    if geometry.geom_type == "MultiPolygon":
        return MultiPolygon([orient(p, sign=-1.0) for p in geometry.geoms])
    return orient(geometry, sign=-1.0)


def finish(geometry, key: str, tolerance: float, decimals: int) -> dict:
    """Simplified, grid-snapped, clockwise GeoJSON geometry; raises if it is not valid."""
    if not geometry.is_valid:
        geometry = geometry.buffer(0)  # rebuilds a self-intersecting ring (Natural Earth: Egypt)
    geometry = geometry.simplify(tolerance, preserve_topology=True)
    # Snap to the output grid first: plain rounding of the coordinates afterwards can make a
    # ring touch itself (it did for Brummen and Rheden). _round below then only trims float
    # noise such as 6.079020000000001.
    geometry = clockwise(set_precision(geometry, 10 ** -decimals))
    geojson = mapping(geometry)
    geojson = {"type": geojson["type"], "coordinates": _round(geojson["coordinates"], decimals)}
    final = shape(geojson)
    if final.is_empty or not final.is_valid or final.geom_type not in POLYGONAL:
        raise BuildError(f"{key}: invalid geometry after simplification")
    return geojson


def build_nl(features: list[dict], spec: MapSpec = SPECS["nl"]) -> dict:
    codes = [f["properties"]["code"] for f in features]
    if len(set(codes)) != len(codes):
        raise BuildError("duplicate municipality codes")
    out = []
    for feature in features:
        p = feature["properties"]
        out.append({
            "type": "Feature",
            "id": p["code"],
            "properties": {
                "municipality_code": p["code"],
                "municipality_name": p["naam"],
                "province_name": p["ligt_in_provincie_naam"],
            },
            "geometry": finish(shape(feature["geometry"]), f"municipality {p['code']}",
                               spec.tolerance, spec.decimals),
        })
    return collection(out, spec)


def build_world(features: list[dict], spec: MapSpec = SPECS["world"]) -> dict:
    groups = defaultdict(list)
    for row, feature in zip(resolve(features), features, strict=True):
        if row["ne_adm0_a3"] not in EXCLUDED:
            geometry = shape(feature["geometry"])
            groups[row["country_code"]].append(geometry if geometry.is_valid
                                               else geometry.buffer(0))
    out = []
    for code, parts in groups.items():
        geometry = parts[0] if len(parts) == 1 else unary_union(parts)
        out.append({
            "type": "Feature",
            "id": code,
            "properties": {"country_code": code},
            "geometry": finish(geometry, f"country {code}", spec.tolerance, spec.decimals),
        })
    return collection(out, spec)


def collection(features: list[dict], spec: MapSpec) -> dict:
    if len(features) < spec.expected_min:
        raise BuildError(f"only {len(features)} features; expected at least {spec.expected_min}")
    return {"type": "FeatureCollection", "features": sorted(features, key=lambda f: f["id"])}


def write(collection: dict, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(collection, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text + "\n", encoding="utf-8", newline="\n")
    return len(text.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("map", choices=sorted(SPECS))
    parser.add_argument("--source", type=Path, help="local GeoJSON instead of the download")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    spec = SPECS[args.map]

    if args.source:
        features = json.loads(args.source.read_text(encoding="utf-8"))["features"]
    else:
        features = download_pdok() if args.map == "nl" else download_natural_earth()
    try:
        result = build_nl(features, spec) if args.map == "nl" else build_world(features, spec)
    except BuildError as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1
    output = args.output or spec.output
    size = write(result, output)
    print(f"{args.map}: {len(result['features'])} features, {size / 1024:.0f} KiB -> {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
