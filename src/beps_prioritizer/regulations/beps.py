"""Seattle Building Emissions Performance Standard (BEPS) reference values.

Sources (checked 2026-10-13; copies in docs/reference/):
- GHGI targets: OSE factsheet "GHGI Targets & Emissions Factors" (Jan 2026),
  from 2025 Director's Rule Table 4.
- Emission factors, ACP formula, Appendix A use-type mapping, GFA definition:
  OSE Director's Rule 2025-01 (draft for public review), sections 10, 12.02, App. A.
- Penalties and deadlines: seattle.gov BEPS FAQ.
"""

from __future__ import annotations

# kgCO2e/sf/yr by compliance interval: 2031-35 (required), later intervals provisional.
BEPS_GHGIT: dict[str, tuple[float, float, float, float]] = {
    "College/University": (2.69, 1.57, 0.00, 0.00),
    "Data Center": (1.43, 0.83, 0.00, 0.00),
    "Entertainment/Public Assembly": (1.18, 0.69, 0.00, 0.00),
    "Fire/Police Station": (2.23, 1.30, 0.00, 0.00),
    "Hospital": (4.68, 2.73, 0.00, 0.00),
    "Hotel": (2.06, 1.20, 0.00, 0.00),
    "K-12 School": (0.95, 0.56, 0.00, 0.00),
    "Laboratory": (6.30, 3.68, 0.00, 0.00),
    "Medical Office": (2.11, 1.23, 0.00, 0.00),
    "Multifamily Housing": (0.89, 0.63, 0.37, 0.00),
    "Museum": (2.11, 1.23, 0.00, 0.00),
    "Non-Refrigerated Warehouse": (0.77, 0.45, 0.00, 0.00),
    "Office": (0.81, 0.47, 0.00, 0.00),
    "Other": (2.48, 1.45, 0.00, 0.00),
    "Recreation": (3.22, 1.88, 0.00, 0.00),
    "Refrigerated Warehouse": (0.98, 0.57, 0.00, 0.00),
    "Residence Hall/Dormitory": (1.16, 0.68, 0.00, 0.00),
    "Restaurant": (5.73, 3.34, 0.00, 0.00),
    "Retail Store": (1.03, 0.60, 0.00, 0.00),
    "Self-Storage Facility": (0.31, 0.18, 0.00, 0.00),
    "Senior Living Community": (2.11, 1.23, 0.00, 0.00),
    "Services": (1.36, 0.79, 0.00, 0.00),
    "Supermarket/Grocery Store": (3.42, 2.00, 0.00, 0.00),
    "Worship Facility": (1.20, 0.70, 0.00, 0.00),
}


# Director's Rule 2025-01, Appendix A (Portfolio Manager type -> BEPS activity type),
# keyed by the spellings used in the Seattle dataset. None = no target / exempt.
PM_TO_BEPS: dict[str, str | None] = {
    "Adult Education": "Other",
    "Ambulatory Surgical Center": "Hospital",
    "Aquarium": "Museum",
    "Bank Branch": "Retail Store",
    "Bar/Nightclub": "Restaurant",
    "Barracks": "Other",
    "Bowling Alley": "Other",
    "College/University": "College/University",
    "Convenience Store without Gas Station": "Other",
    "Convention Center": "Entertainment/Public Assembly",
    "Courthouse": "Other",
    "Data Center": "Data Center",
    "Distribution Center": "Non-Refrigerated Warehouse",
    "Electric Vehicle Charging Station": None,  # not in Appendix A; recorded with 0 sf
    "Enclosed Mall": "Retail Store",
    "Energy/Power Station": "Other",
    "Fast Food Restaurant": "Restaurant",
    "Financial Office": "Office",
    "Fire Station": "Fire/Police Station",
    "Fitness Center/Health Club/Gym": "Recreation",
    "Food Sales": "Supermarket/Grocery Store",
    "Food Service": "Restaurant",
    "Hospital (General Medical & Surgical)": "Hospital",
    "Hotel": "Hotel",
    "Ice/Curling Rink": "Other",
    "Indoor Arena": "Other",
    "K-12 School": "K-12 School",
    "Laboratory": "Laboratory",
    "Library": "Services",
    "Lifestyle Center": "Entertainment/Public Assembly",
    "Mailing Center/Post Office": "Services",
    "Manufacturing/Industrial Plant": None,
    "Medical Office": "Medical Office",
    "Movie Theater": "Entertainment/Public Assembly",
    "Multifamily Housing": "Multifamily Housing",
    "Museum": "Museum",
    "Non-Refrigerated Warehouse": "Non-Refrigerated Warehouse",
    "Office": "Office",
    "Other": "Other",
    "Other - Education": "Other",
    "Other - Entertainment/Public Assembly": "Entertainment/Public Assembly",
    "Other - Lodging/Residential": "Hotel",
    "Other - Mall": "Retail Store",
    "Other - Public Services": "Services",
    "Other - Recreation": "Recreation",
    "Other - Restaurant/Bar": "Restaurant",
    "Other - Services": "Services",
    "Other - Technology/Science": "Other",
    "Other - Utility": "Other",
    "Other/Specialty Hospital": "Hospital",
    "Outpatient Rehabilitation/Physical Therapy": "Medical Office",
    "Parking": None,
    "Performing Arts": "Entertainment/Public Assembly",
    "Personal Services (Health/Beauty, Dry Cleaning, etc)": "Services",
    "Police Station": "Fire/Police Station",
    "Pre-school/Daycare": "Entertainment/Public Assembly",
    "Prison/Incarceration": "Other",
    "Refrigerated Warehouse": "Refrigerated Warehouse",
    "Residence Hall/Dormitory": "Residence Hall/Dormitory",
    "Residential Care Facility": "Senior Living Community",
    "Restaurant": "Restaurant",
    "Retail Store": "Retail Store",
    "Self-Storage Facility": "Self-Storage Facility",
    "Senior Living Community": "Senior Living Community",
    "Single Family Home": None,
    "Social/Meeting Hall": "Entertainment/Public Assembly",
    "Stadium (Open)": "Entertainment/Public Assembly",
    "Strip Mall": "Retail Store",
    "Supermarket/Grocery Store": "Supermarket/Grocery Store",
    "Swimming Pool": "Other",
    "Transportation Terminal/Station": "Entertainment/Public Assembly",
    "Urgent Care/Clinic/Other Outpatient": "Medical Office",
    "Vehicle Dealership": "Retail Store",
    "Vehicle Repair Services": "Services",
    "Veterinary Office": "Medical Office",
    "Vocational School": "College/University",
    "Wholesale Club/Supercenter": "Supermarket/Grocery Store",
    "Worship Facility": "Worship Facility",
}


# kgCO2e per kBtu (Director's Rule Table 5). Baseline factors (column A) reproduce the
# dataset's reported GHG exactly; the 2031-2035 compliance GHGI uses the provisional
# factors (column B), where electricity halves.
BEPS_FACTORS_BASELINE = {"electricity_kbtu": 0.0058, "natural_gas_kbtu": 0.053,
                         "steam_kbtu": 0.081}
BEPS_FACTORS_2031 = {"electricity_kbtu": 0.0029, "natural_gas_kbtu": 0.053,
                     "steam_kbtu": 0.081}


# (minimum GFA exclusive, first GHGIT deadline year), floor area excluding parking.
BEPS_DEADLINES = [(220_000, 2031), (90_000, 2032), (50_000, 2033), (30_000, 2034),
                  (20_000, 2035)]


BEPS_PENALTY_PER_SF = {"nonresidential": 10.00, "multifamily": 7.50}  # per 5-yr interval


BEPS_COST_PER_MTCO2E = 190.0


BEPS_ACP_MINIMUM = {"small": 1_250.0, "large": 2_500.0}  # <= 50,000 sf / > 50,000 sf
