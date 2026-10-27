"""Download the Seattle benchmarking CSV, standardize it, and save it as Parquet.

Ingest only fixes *representation* (column names, dtypes, text formatting).
It never drops or edits suspicious values; that is the data-quality layer's job,
so every downstream decision about bad data stays visible and testable.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

import pandas as pd

from beps_prioritizer.config import BENCHMARKING_CSV_URL, RAW_CSV

# Raw CSV header -> snake_case name. Units are kept in the name where they matter.
COLUMN_MAP: dict[str, str] = {
    "OSEBuildingID": "building_id",
    "DataYear": "data_year",
    "BuildingName": "building_name",
    "BuildingType": "building_type",
    "TaxParcelIdentificationNumber": "tax_parcel_id",
    "Address": "address",
    "City": "city",
    "State": "state",
    "ZipCode": "zip_code",
    "Latitude": "latitude",
    "Longitude": "longitude",
    "Neighborhood": "neighborhood",
    "CouncilDistrictCode": "council_district",
    "YearBuilt": "year_built",
    "NumberofFloors": "num_floors",
    "NumberofBuildings": "num_buildings",
    "PropertyGFATotal": "gfa_total",
    "PropertyGFABuildings": "gfa_buildings",
    "PropertyGFAParking": "gfa_parking",
    "SelfReportGFATotal": "self_gfa_total",
    "SelfReportGFABuildings": "self_gfa_buildings",
    "SelfReportParking": "self_gfa_parking",
    "ENERGYSTARScore": "energy_star_score",
    "SiteEUIWN(kBtu/sf)": "site_eui_wn",
    "SiteEUI(kBtu/sf)": "site_eui",
    "SiteEnergyUse(kBtu)": "site_energy_kbtu",
    "SiteEnergyUseWN(kBtu)": "site_energy_wn_kbtu",
    "SourceEUIWN(kBtu/sf)": "source_eui_wn",
    "SourceEUI(kBtu/sf)": "source_eui",
    "EPAPropertyType": "epa_property_type",
    "LargestPropertyUseType": "use1_type",
    "LargestPropertyUseTypeGFA": "use1_gfa",
    "SecondLargestPropertyUseType": "use2_type",
    "SecondLargestPropertyUseTypeGFA": "use2_gfa",
    "ThirdLargestPropertyUseType": "use3_type",
    "ThirdLargestPropertyUseTypeGFA": "use3_gfa",
    "Electricity(kWh)": "electricity_kwh",
    "SteamUse(kBtu)": "steam_kbtu",
    "NaturalGas(therms)": "natural_gas_therms",
    "ComplianceStatus": "compliance_status",
    "ComplianceIssue": "compliance_issue",
    "Electricity(kBtu)": "electricity_kbtu",
    "NaturalGas(kBtu)": "natural_gas_kbtu",
    "TotalGHGEmissions": "ghg_total_mtco2e",
    "GHGEmissionsIntensity": "ghg_intensity",
    "Demolished": "demolished",
}


INT_COLUMNS = [
    "building_id",
    "data_year",
    "council_district",
    "year_built",
    "num_floors",
    "num_buildings",
]


FLOAT_COLUMNS = [
    "latitude",
    "longitude",
    "gfa_total",
    "gfa_buildings",
    "gfa_parking",
    "self_gfa_total",
    "self_gfa_buildings",
    "self_gfa_parking",
    "energy_star_score",
    "site_eui_wn",
    "site_eui",
    "site_energy_kbtu",
    "site_energy_wn_kbtu",
    "source_eui_wn",
    "source_eui",
    "use1_gfa",
    "use2_gfa",
    "use3_gfa",
    "electricity_kwh",
    "steam_kbtu",
    "natural_gas_therms",
    "electricity_kbtu",
    "natural_gas_kbtu",
    "ghg_total_mtco2e",
    "ghg_intensity",
]


CATEGORY_COLUMNS = [
    "building_type",
    "neighborhood",
    "epa_property_type",
    "use1_type",
    "use2_type",
    "use3_type",
    "compliance_status",
    "compliance_issue",
]


TEXT_COLUMNS = ["building_name", "address", "city", "state", "zip_code", "tax_parcel_id"]


def download_raw(
    dest: Path = RAW_CSV, url: str = BENCHMARKING_CSV_URL, force: bool = False
) -> Path:
    """Download the raw CSV unless it already exists."""
    if dest.exists() and not force:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    urllib.request.urlretrieve(url, tmp)  # noqa: S310 - fixed https URL
    tmp.replace(dest)
    return dest


def _clean_text(s: pd.Series) -> pd.Series:
    """Strip and collapse internal whitespace; keep missing values missing."""
    return s.str.strip().str.replace(r"\s+", " ", regex=True)


def _normalize_neighborhood(s: pd.Series) -> pd.Series:
    """'MAGNOLIA / QUEEN ANNE' and 'MAGNOLIA/QUEEN ANNE' -> one spelling."""
    return s.str.upper().str.replace(r"\s*/\s*", "/", regex=True)


def _normalize_parcel(s: pd.Series) -> pd.Series:
    """King County parcel numbers are 10 digits; the CSV drops leading zeros."""
    is_short_digits = s.str.fullmatch(r"\d{7,9}", na=False)
    return s.where(~is_short_digits, s.str.zfill(10))


def standardize(raw: pd.DataFrame) -> pd.DataFrame:
    """Rename columns, cast types, and normalize text. Rows are never dropped."""
    missing = set(COLUMN_MAP) - set(raw.columns)
    if missing:
        raise ValueError(f"Raw data is missing expected columns: {sorted(missing)}")

    df = raw.rename(columns=COLUMN_MAP)[list(COLUMN_MAP.values())].copy()

    for col in TEXT_COLUMNS + CATEGORY_COLUMNS:
        df[col] = _clean_text(df[col].astype("string"))

    df["city"] = df["city"].str.upper()
    df["neighborhood"] = _normalize_neighborhood(df["neighborhood"])
    df["tax_parcel_id"] = _normalize_parcel(df["tax_parcel_id"])

    for col in INT_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="raise").astype("Int64")
    for col in FLOAT_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="raise").astype("float64")
    for col in CATEGORY_COLUMNS:
        df[col] = df[col].astype("category")

    demolished = df["demolished"].astype("string").str.strip().str.lower()
    df["demolished"] = demolished.map({"true": True, "false": False}).astype("boolean")

    if df.duplicated(["building_id", "data_year"]).any():
        raise ValueError("Expected one row per (building_id, data_year).")

    return df.sort_values(["building_id", "data_year"], ignore_index=True)


def load_raw(path: Path = RAW_CSV) -> pd.DataFrame:
    """Read the raw CSV with every column as text so nothing is silently coerced."""
    return pd.read_csv(path, dtype=str, keep_default_na=True)
