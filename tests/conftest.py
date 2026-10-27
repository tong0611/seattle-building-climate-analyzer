from __future__ import annotations

import pandas as pd
import pytest

from beps_prioritizer.ingestion.ingest import COLUMN_MAP


def _raw_row(**overrides: str | None) -> dict[str, str | None]:
    """One plausible raw CSV row (all strings, as read with dtype=str)."""
    row: dict[str, str | None] = {c: None for c in COLUMN_MAP}
    row.update({
        "OSEBuildingID": "1",
        "DataYear": "2024",
        "BuildingName": "  Example   Office ",
        "BuildingType": "NonResidential",
        "TaxParcelIdentificationNumber": "659000030",
        "Address": "405 OLIVE WAY",
        "City": "Seattle",
        "State": "WA",
        "ZipCode": "98101",
        "Latitude": "47.61",
        "Longitude": "-122.33",
        "Neighborhood": "MAGNOLIA / QUEEN ANNE",
        "CouncilDistrictCode": "7",
        "YearBuilt": "1927",
        "NumberofFloors": "12",
        "NumberofBuildings": "1",
        "PropertyGFATotal": "100000",
        "PropertyGFABuildings": "90000",
        "PropertyGFAParking": "10000",
        "SelfReportGFATotal": "100000",
        "SelfReportGFABuildings": "90000",
        "SelfReportParking": "10000",
        "ENERGYSTARScore": "75",
        "SiteEUIWN(kBtu/sf)": "59.5",
        "SiteEUI(kBtu/sf)": "60.13",  # = site energy / 90,000 sf
        "SiteEnergyUse(kBtu)": "5412000",  # = electricity + gas kBtu
        "SiteEnergyUseWN(kBtu)": "5355000",
        "SourceEUIWN(kBtu/sf)": "120",
        "SourceEUI(kBtu/sf)": "121",
        "EPAPropertyType": "Office",
        "LargestPropertyUseType": "Office",
        "LargestPropertyUseTypeGFA": "90000",
        "Electricity(kWh)": "1000000",
        "NaturalGas(therms)": "20000",
        "ComplianceStatus": "Compliant",
        "ComplianceIssue": "No Issue",
        "Electricity(kBtu)": "3412000",
        "NaturalGas(kBtu)": "2000000",
        "TotalGHGEmissions": "125.79",  # = fuels x BEPS factors
        "GHGEmissionsIntensity": "1.398",
        "Demolished": "false",
    })
    row.update(overrides)
    return row


@pytest.fixture
def raw_row():
    return _raw_row


@pytest.fixture
def raw_df() -> pd.DataFrame:
    return pd.DataFrame([
        _raw_row(),
        _raw_row(DataYear="2023", Neighborhood="MAGNOLIA/QUEEN ANNE", City="SEATTLE"),
        _raw_row(OSEBuildingID="2", **{"SiteEUI(kBtu/sf)": "44197.9", "Demolished": "true"}),
    ])
