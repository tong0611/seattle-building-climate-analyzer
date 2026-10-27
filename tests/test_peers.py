from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from beps_prioritizer.ingestion.ingest import standardize
from beps_prioritizer.peers.crossfit import cross_fit, folds, peer_confidence
from beps_prioritizer.peers.features import build_features, common_use_types, use_shares
from beps_prioritizer.peers.models import BlendedPlusBoosting, BlendedUseMedian


@pytest.fixture
def city(raw_row) -> pd.DataFrame:
    """60 offices + 60 apartment buildings, 3 years each. Building 7 wastes 5x energy."""
    rng = np.random.default_rng(0)
    rows = []
    for b in range(1, 121):
        office = b <= 60
        base = 60.0 if office else 35.0
        if b == 7:
            base *= 5
        use = "Office" if office else "Multifamily Housing"
        for year in (2022, 2023, 2024):
            eui = base * rng.uniform(0.9, 1.1)
            rows.append(raw_row(**{
                "OSEBuildingID": str(b),
                "DataYear": str(year),
                "BuildingType": "NonResidential" if office else "Multifamily MR (5-9)",
                "LargestPropertyUseType": use,
                "LargestPropertyUseTypeGFA": "90000",
                "SiteEUIWN(kBtu/sf)": f"{eui:.2f}",
                "GHGEmissionsIntensity": f"{eui / 40:.3f}",
            }))
    return standardize(pd.DataFrame(rows))


def test_folds_keep_each_building_together(city: pd.DataFrame) -> None:
    f = folds(city, n=5)
    assert (city.assign(f=f).groupby("building_id")["f"].nunique() == 1).all()


def test_shares_exclude_parking_and_sum_to_one(raw_row) -> None:
    df = standardize(pd.DataFrame([raw_row(**{
        "LargestPropertyUseType": "Office", "LargestPropertyUseTypeGFA": "60000",
        "SecondLargestPropertyUseType": "Parking", "SecondLargestPropertyUseTypeGFA": "50000",
        "ThirdLargestPropertyUseType": "Retail Store", "ThirdLargestPropertyUseTypeGFA": "20000",
    })]))
    shares = use_shares(df, ["Office", "Retail Store"])
    assert shares.loc[0, "Office"] == pytest.approx(0.75)
    assert shares.loc[0, "Retail Store"] == pytest.approx(0.25)
    assert "Parking" not in shares.columns


def test_blended_median_mixes_by_floor_area(city: pd.DataFrame, raw_row) -> None:
    x = build_features(city, ["Multifamily Housing", "Office"])
    model = BlendedUseMedian().fit(city, x, city["site_eui_wn"])
    mixed = standardize(pd.DataFrame([raw_row(**{
        "OSEBuildingID": "999",
        "LargestPropertyUseType": "Office", "LargestPropertyUseTypeGFA": "45000",
        "SecondLargestPropertyUseType": "Multifamily Housing",
        "SecondLargestPropertyUseTypeGFA": "45000",
    })]))
    pred = model.predict(mixed, build_features(mixed, ["Multifamily Housing", "Office"]))
    assert pred.iloc[0] == pytest.approx((60 + 35) / 2, rel=0.1)


def test_out_of_building_prediction_does_not_learn_own_history(city: pd.DataFrame) -> None:
    """A persistently wasteful building must be compared with peers, not with itself."""
    x = build_features(city, common_use_types(city, min_rows=10))
    train_ok = pd.Series(True, index=city.index)
    pred = cross_fit(city, x, train_ok, "site_eui_wn", BlendedPlusBoosting, folds(city, n=5))
    waste = city["building_id"] == 7
    ratio = city.loc[waste, "site_eui_wn"] / pred[waste]
    assert (ratio > 3).all()  # actual ~300 vs expected ~60
    normal = city["building_id"].between(1, 60) & ~waste
    assert (city.loc[normal, "site_eui_wn"] / pred[normal]).between(0.7, 1.4).all()


def test_peer_confidence_low_for_rare_use(city: pd.DataFrame, raw_row) -> None:
    x = build_features(city, ["Multifamily Housing", "Office"])
    conf = peer_confidence(x, pd.Series(True, index=city.index))
    assert (conf == 1).all()
    rare = standardize(pd.DataFrame([raw_row(**{"LargestPropertyUseType": "Data Center"})]))
    x_rare = build_features(rare, ["Multifamily Housing", "Office"])
    both = pd.concat([x, x_rare], ignore_index=True)
    ok = pd.Series([True] * len(x) + [False], index=both.index)
    assert peer_confidence(both, ok).iloc[-1] == 0
