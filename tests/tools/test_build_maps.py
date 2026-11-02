import hashlib
import json
import re
from pathlib import Path

import pytest
import responses
from shapely.geometry import Polygon, shape

from tools import build_maps
from tools.build_maps import (
    NE_SHA256,
    NE_URL,
    NE_VERSION,
    PDOK_URL,
    SPECS,
    BuildError,
    build_nl,
    build_world,
    download_natural_earth,
    download_pdok,
    write,
)

NL = SPECS["nl"]
NOTEBOOK_34 = Path(__file__).resolve().parents[2] / "notebooks" / "34_country_boundaries.py"


def square(x0: float, y0: float = 52.0, size: float = 0.1) -> list:
    # Counter-clockwise like PDOK and Natural Earth (RFC 7946), with an extra point mid-edge
    # that simplification should drop.
    return [[x0, y0], [x0 + size / 2, y0], [x0 + size, y0], [x0 + size, y0 + size],
            [x0, y0 + size], [x0, y0]]


def municipality(code: str, x0: float = 5.0) -> dict:
    return {
        "type": "Feature",
        "properties": {"code": code, "naam": f"Gemeente {code}", "identificatie": f"GM{code}",
                       "ligt_in_provincie_code": "PV20",
                       "ligt_in_provincie_naam": "Groningen"},
        "geometry": {"type": "Polygon", "coordinates": [square(x0)]},
    }


def municipalities(n: int) -> list[dict]:
    return [municipality(f"{i:04d}", x0=3 + i * 0.2) for i in range(n, 0, -1)]


def country(adm0: str, iso: str, x0: float, name: str | None = None) -> dict:
    return {
        "type": "Feature",
        "properties": {"ADM0_A3": adm0, "ISO_A3": iso, "ISO_A3_EH": iso, "NAME": name or adm0},
        "geometry": {"type": "Polygon", "coordinates": [square(x0, 10.0, 1.0)]},
    }


def test_nl_keeps_key_and_drops_mid_edge_point():
    out = build_nl(municipalities(342))["features"][0]
    assert out["id"] == "0001"
    assert out["properties"] == {"municipality_code": "0001",
                                 "municipality_name": "Gemeente 0001",
                                 "province_name": "Groningen"}
    ring = out["geometry"]["coordinates"][0]
    assert len(ring) == 5
    assert [3.25, 52.0] not in ring  # mid-edge point of the square at x0 = 3.2


def test_rings_are_clockwise_for_d3():
    features = municipalities(342)
    assert Polygon(features[0]["geometry"]["coordinates"][0]).exterior.is_ccw
    holed = features[0]
    holed["geometry"] = {"type": "MultiPolygon", "coordinates": [
        [square(70.0), [[70.02, 52.02], [70.02, 52.04], [70.04, 52.04], [70.04, 52.02],
                        [70.02, 52.02]]],
        [square(71.0)],
    ]}
    out = {f["id"]: shape(f["geometry"]) for f in build_nl(features)["features"]}
    multi = out[holed["properties"]["code"]]
    assert multi.geom_type == "MultiPolygon"
    assert all(not p.exterior.is_ccw for p in multi.geoms)
    assert [len(p.interiors) for p in multi.geoms].count(1) == 1
    assert all(ring.is_ccw for p in multi.geoms for ring in p.interiors)
    assert not out["0002"].exterior.is_ccw


def test_coordinates_are_snapped_to_the_grid():
    features = municipalities(342)
    features[0]["geometry"]["coordinates"] = [square(5.123456789)]
    for f in build_nl(features)["features"]:
        for point in f["geometry"]["coordinates"][0]:
            assert all(len(str(c).split(".")[1]) <= NL.decimals for c in point)


def test_nl_sorts_by_code_and_rejects_too_few_and_duplicates():
    ids = [f["id"] for f in build_nl(municipalities(342))["features"]]
    assert ids == sorted(ids) and len(ids) == 342
    with pytest.raises(BuildError, match="only 10"):
        build_nl(municipalities(10))
    features = municipalities(342)
    features[1]["properties"]["code"] = features[0]["properties"]["code"]
    with pytest.raises(BuildError, match="duplicate"):
        build_nl(features)


def test_world_merges_shared_codes_and_leaves_out_antarctica():
    spec = build_maps.MapSpec(Path("unused"), 0.05, 3, expected_min=3)
    features = [
        country("NLD", "NLD", 0.0),
        country("JEY", "JEY", 2.0),  # Jersey and Guernsey → CHI (WDI_OVERRIDES)
        country("GGY", "GGY", 4.0),
        country("ATA", "ATA", 6.0),  # Antarctica: EXCLUDED
        country("SOL", "-99", 8.0, "Somaliland"),  # no ISO code: keeps ADM0_A3
    ]
    features[4]["properties"]["ISO_A3_EH"] = "-99"
    out = {f["id"]: f for f in build_world(features, spec)["features"]}
    assert sorted(out) == ["CHI", "NLD", "SOL"]
    assert out["CHI"]["geometry"]["type"] == "MultiPolygon"
    assert len(out["CHI"]["geometry"]["coordinates"]) == 2
    assert out["NLD"]["properties"] == {"country_code": "NLD"}


def test_world_repairs_a_self_intersecting_ring():
    bowtie = country("EGY", "EGY", 0.0)
    bowtie["geometry"]["coordinates"] = [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]]
    spec = build_maps.MapSpec(Path("unused"), 0.05, 3, expected_min=1)
    geometry = shape(build_world([bowtie], spec)["features"][0]["geometry"])
    assert geometry.is_valid and geometry.area > 0


def test_natural_earth_constants_match_notebook_34():
    text = NOTEBOOK_34.read_text(encoding="utf-8")
    assert re.search(rf'^NE_VERSION = "{re.escape(NE_VERSION)}"$', text, re.M)
    assert re.search(rf'^NE_SHA256 = "{NE_SHA256}"$', text, re.M)
    assert "geojson/ne_10m_admin_0_countries.geojson" in text and NE_URL.endswith(
        "geojson/ne_10m_admin_0_countries.geojson")


@responses.activate
def test_natural_earth_download_checks_sha256(monkeypatch):
    body = json.dumps({"features": []}).encode()
    responses.get(NE_URL, body=body)
    with pytest.raises(BuildError, match="sha256"):
        download_natural_earth()
    monkeypatch.setattr(build_maps, "NE_SHA256", hashlib.sha256(body).hexdigest())
    assert download_natural_earth() == []


def test_write_is_byte_identical_on_rerun(tmp_path):
    path = tmp_path / "nl.geojson"
    write(build_nl(municipalities(342)), path)
    first = path.read_bytes()
    write(build_nl(municipalities(342)), path)
    assert path.read_bytes() == first
    assert json.loads(first)["type"] == "FeatureCollection"


@responses.activate
def test_pdok_download_follows_next_links():
    page2 = "https://api.pdok.nl/page2"
    responses.get(PDOK_URL, json={"features": [municipality("0001")],
                                  "links": [{"rel": "next", "href": page2}]})
    responses.get(page2, json={"features": [municipality("0002")], "links": []})
    assert [f["properties"]["code"] for f in download_pdok()] == ["0001", "0002"]
