"""Districts, global filters, staleness and spatial areas."""

from __future__ import annotations

import datetime as dt

import pytest

from app.core import districts
from app.core.errors import BadRequest, DataUnavailable
from app.core.filters import parse_compare, parse_scope, parse_window
from app.core.provenance import is_stale
from app.core.spatial import get_spatial

# ---------------------------------------------------------------- districts


def test_52_districts_by_province() -> None:
    counts = {p: len(districts.in_province(p)) for p in districts.PROVINCES}
    assert counts == {"EC": 8, "FS": 5, "GP": 5, "KZN": 11, "LP": 5, "MP": 3, "NC": 5, "NW": 4, "WC": 6}


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("Alfred Nzo", "DC44"),
        ("alfred nzo", "DC44"),
        ("cape town", "CPT"),  # satellite_remote_sensing spelling
        ("city of cape town", "CPT"),  # hydrological_drought_master spelling
        ("ortambo", "DC15"),
        ("O.R.Tambo", "DC15"),
        ("sarah baartman", "DC10"),
        ("Cacadu", "DC10"),
        ("Eden", "DC4"),
        ("Garden Route", "DC4"),
        ("johannesburg", "JHB"),
        ("tshwane", "TSH"),
        ("  Z F  Mgcawu ", "DC8"),
        ("dc44", "DC44"),
    ],
)
def test_resolve_aliases(name: str, code: str) -> None:
    d = districts.resolve(name)
    assert d is not None and d.code == code


def test_db_names_cover_every_spelling() -> None:
    names = set(districts.db_names())
    assert {"cape town", "City of Cape Town", "city of cape town", "ortambo"} <= names


# ---------------------------------------------------------------- scope filters


def test_national_default() -> None:
    s = parse_scope()
    assert (s.level, s.id) == ("national", "ZA") and len(s.district_codes) == 52


def test_province_scope() -> None:
    s = parse_scope(province="wc")
    assert s.level == "province" and s.id == "WC" and "CPT" in s.district_codes and len(s.district_codes) == 6


def test_municipality_wins_and_must_match_province() -> None:
    assert parse_scope(province="WC", municipality="CPT").level == "municipality"
    with pytest.raises(BadRequest):
        parse_scope(province="GP", municipality="CPT")


def test_local_municipality_rejected_with_hint() -> None:
    with pytest.raises(BadRequest) as e:
        parse_scope(municipality="EC101")
    assert "district" in e.value.error


@pytest.mark.parametrize("bad", [{"province": "XX"}, {"municipality": "NOPE"}, {"wma": "WMA10"}])
def test_bad_scope_values(bad: dict[str, str]) -> None:
    with pytest.raises(BadRequest):
        parse_scope(**bad)


def test_catchment_is_unavailable() -> None:
    with pytest.raises(DataUnavailable) as e:
        parse_scope(catchment="C8")
    assert e.value.status_code == 501 and e.value.body()["code"] == "DATA_UNAVAILABLE"


def test_wma_needs_district_overlap() -> None:
    with pytest.raises(DataUnavailable):
        parse_scope(wma="WMA5")
    s = parse_scope(wma="wma5", wma_districts={"WMA5": ("DC18", "JHB")})
    assert s.level == "wma" and s.approximate and s.district_codes == ("DC18", "JHB")


# ---------------------------------------------------------------- period window


def test_window_periods() -> None:
    newest = dt.date(2026, 9, 6)
    assert parse_window(None, None, None).resolve(newest) == (dt.date(2025, 9, 7), newest)
    assert parse_window("3M", None, None).resolve(newest) == (dt.date(2026, 6, 7), newest)
    assert parse_window("latest", None, None).resolve(newest) == (newest, newest)
    assert parse_window("historical", None, None).resolve(newest, dt.date(2000, 1, 1)) == (dt.date(2000, 1, 1), newest)
    assert parse_window("12M", "2020-01-01", "2020-12-31").resolve(newest) == (
        dt.date(2020, 1, 1),
        dt.date(2020, 12, 31),
    )


@pytest.mark.parametrize(
    ("p", "f", "t"), [("7M", None, None), (None, "2020-13-01", None), (None, "2021-01-01", "2020-01-01")]
)
def test_window_errors(p: str | None, f: str | None, t: str | None) -> None:
    with pytest.raises(BadRequest):
        parse_window(p, f, t)


def test_compare() -> None:
    assert parse_compare("1") and not parse_compare(None) and not parse_compare("0")
    with pytest.raises(BadRequest):
        parse_compare("yes")


# ---------------------------------------------------------------- staleness


def test_is_stale() -> None:
    newest = dt.date(2026, 9, 6)
    assert is_stale(dt.date(2025, 12, 31), newest, 90)  # satellite / hydrology
    assert not is_stale(dt.date(2026, 9, 1), newest, 90)
    assert not is_stale(newest - dt.timedelta(days=90), newest, 90)
    assert is_stale(newest - dt.timedelta(days=91), newest, 90)
    assert not is_stale(None, newest, 90)


# ---------------------------------------------------------------- spatial


@pytest.mark.parametrize(
    ("prov", "official_km2"),
    # Stats SA official areas
    [("NC", 372_889), ("GP", 18_178), ("WC", 129_462), ("KZN", 94_361), ("EC", 168_966)],
)
def test_province_areas_within_1pct(prov: str, official_km2: float) -> None:
    area = next(r.area_km2 for r in get_spatial().provinces if r.id == prov)
    assert abs(area - official_km2) / official_km2 < 0.01


def test_wmas_loaded() -> None:
    assert sorted(w.id for w in get_spatial().wmas) == sorted(f"WMA{i}" for i in range(1, 10))


def test_all_52_district_boundaries_loaded() -> None:
    s = get_spatial()
    assert s.has_districts and not s.district_problems
    areas = s.district_areas()
    assert areas is not None and set(areas) == {d.code for d in districts.districts()}


@pytest.mark.parametrize(
    ("code", "official_km2"),
    # Stats SA / MDB official areas
    [("CPT", 2_446), ("JHB", 1_645), ("ETH", 2_556), ("TSH", 6_298), ("DC6", 126_836), ("DC5", 38_854)],
)
def test_district_areas_within_half_pct(code: str, official_km2: float) -> None:
    areas = get_spatial().district_areas()
    assert areas is not None and abs(areas[code] - official_km2) / official_km2 < 0.005


def test_districts_tile_their_provinces() -> None:
    s = get_spatial()
    areas = s.district_areas()
    assert areas is not None
    for p in s.provinces:
        total = sum(areas[d.code] for d in districts.in_province(p.id))
        assert abs(total - p.area_km2) / p.area_km2 < 0.005, p.id


def test_wma_overlap() -> None:
    s = get_spatial()
    ov = s.wma_overlap()
    assert ov is not None and set(ov) == {f"WMA{i}" for i in range(1, 10)}
    for w in s.wmas:
        assert abs(sum(ov[w.id].values()) - w.area_km2) / w.area_km2 < 0.005, w.id  # districts cover each WMA
    assert {"DC1", "DC2", "CPT"} <= set(ov["WMA9"])  # Berg-Olifants: West Coast, Cape Winelands, Cape Town
    wd = s.wma_districts()
    assert wd is not None and "DC34" in wd["WMA1"]  # Limpopo WMA includes Vhembe
