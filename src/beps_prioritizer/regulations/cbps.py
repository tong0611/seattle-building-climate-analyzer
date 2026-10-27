"""Washington Clean Buildings Performance Standard (CBPS) reference values.

Sources (checked 2026-10-13):
- EUI targets (climate zone 4C, Seattle): WA Commerce "EUI Targets & Building Activity
  Type Definitions" (Table 7-2a); copy in docs/reference/.
- Tiers, deadlines, penalties: RCW 19.27A.210 and commerce.wa.gov/cbps.
"""

from __future__ import annotations

# Site EUI targets, climate zone 4C (Seattle), kBtu/sf/yr. None = no target:
# not covered (manufacturing, utility, data center) or the type must use the
# mixed-use method (strip mall, lifestyle center, other mall) and has no value.
PM_TO_CBPS_EUIT: dict[str, float | None] = {
    "Adult Education": 49, "Ambulatory Surgical Center": 90, "Aquarium": 55,
    "Bank Branch": 69, "Bar/Nightclub": 55, "Barracks": 88, "Bowling Alley": 73,
    "College/University": 102, "Convenience Store without Gas Station": 244,
    "Convention Center": 50, "Courthouse": 101, "Data Center": None,
    "Distribution Center": 36, "Electric Vehicle Charging Station": None,
    "Enclosed Mall": 58, "Energy/Power Station": None, "Fast Food Restaurant": 427,
    "Financial Office": 69, "Fire Station": 65, "Fitness Center/Health Club/Gym": 73,
    "Food Sales": 184, "Food Service": 293, "Hospital (General Medical & Surgical)": 215,
    "Hotel": 68, "Ice/Curling Rink": 73, "Indoor Arena": 67, "K-12 School": 49,
    "Laboratory": 237, "Library": 56, "Lifestyle Center": None,
    "Mailing Center/Post Office": 51, "Manufacturing/Industrial Plant": None,
    "Medical Office": 60, "Movie Theater": 67, "Multifamily Housing": 32, "Museum": 67,
    "Non-Refrigerated Warehouse": 36, "Office": 63, "Other": None,
    "Other - Education": 49, "Other - Entertainment/Public Assembly": 55,
    "Other - Lodging/Residential": 71, "Other - Mall": None, "Other - Public Services": 66,
    "Other - Recreation": 73, "Other - Restaurant/Bar": 361, "Other - Services": 66,
    "Other - Technology/Science": 66, "Other - Utility": None,
    "Other/Specialty Hospital": 196, "Outpatient Rehabilitation/Physical Therapy": 90,
    "Parking": None, "Performing Arts": 55,
    "Personal Services (Health/Beauty, Dry Cleaning, etc)": 66, "Police Station": 65,
    "Pre-school/Daycare": 59, "Prison/Incarceration": 101, "Refrigerated Warehouse": 121,
    "Residence Hall/Dormitory": 88, "Residential Care Facility": 78, "Restaurant": 361,
    "Retail Store": 68, "Self-Storage Facility": 36, "Senior Living Community": 78,
    "Single Family Home": None, "Social/Meeting Hall": 50, "Stadium (Open)": 67,
    "Strip Mall": None, "Supermarket/Grocery Store": 191, "Swimming Pool": None,
    "Transportation Terminal/Station": 55, "Urgent Care/Clinic/Other Outpatient": 90,
    "Vehicle Dealership": 59, "Vehicle Repair Services": 60, "Veterinary Office": 90,
    "Vocational School": 49, "Wholesale Club/Supercenter": 68, "Worship Facility": 39,
}


# Tier 1: > 50,000 sf of nonresidential, hotel, motel, and dormitory floor area.
# Multifamily is Tier 2 (reporting and management requirements, no EUI target).
CBPS_TIER1_MIN_SF = 50_000


CBPS_NOT_TIER1_USES = {"Multifamily Housing", "Parking", "Single Family Home"}


CBPS_DEADLINES = [(220_000, 2026), (90_000, 2027), (50_000, 2028)]


CBPS_PENALTY_BASE = 5_000.0


CBPS_PENALTY_PER_SF = 1.50  # Commerce's current maximum (statute: $1, inflation-adjusted)
