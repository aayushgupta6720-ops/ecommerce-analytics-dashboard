"""Country name cleanup and ISO-3 codes for the choropleth map."""

# Dataset spellings -> display names. Applied once in the ETL.
DISPLAY_NAMES = {
    "EIRE": "Ireland",
    "RSA": "South Africa",
    "USA": "United States",
    "Korea": "South Korea",
}

# Display name -> (ISO-3, region). None for pseudo-countries that can't go on a map.
COUNTRIES: dict[str, tuple[str | None, str]] = {
    "United Kingdom": ("GBR", "UK"),
    "Ireland": ("IRL", "Europe"),
    "Channel Islands": (None, "Europe"),  # Jersey/Guernsey, no single ISO code
    "France": ("FRA", "Europe"),
    "Germany": ("DEU", "Europe"),
    "Netherlands": ("NLD", "Europe"),
    "Belgium": ("BEL", "Europe"),
    "Spain": ("ESP", "Europe"),
    "Portugal": ("PRT", "Europe"),
    "Switzerland": ("CHE", "Europe"),
    "Austria": ("AUT", "Europe"),
    "Italy": ("ITA", "Europe"),
    "Sweden": ("SWE", "Europe"),
    "Norway": ("NOR", "Europe"),
    "Denmark": ("DNK", "Europe"),
    "Finland": ("FIN", "Europe"),
    "Iceland": ("ISL", "Europe"),
    "Poland": ("POL", "Europe"),
    "Czech Republic": ("CZE", "Europe"),
    "Lithuania": ("LTU", "Europe"),
    "Greece": ("GRC", "Europe"),
    "Cyprus": ("CYP", "Europe"),
    "Malta": ("MLT", "Europe"),
    "European Community": (None, "Europe"),
    "United States": ("USA", "Americas"),
    "Canada": ("CAN", "Americas"),
    "Brazil": ("BRA", "Americas"),
    "Bermuda": ("BMU", "Americas"),
    "West Indies": (None, "Americas"),
    "Australia": ("AUS", "Asia-Pacific"),
    "Japan": ("JPN", "Asia-Pacific"),
    "Singapore": ("SGP", "Asia-Pacific"),
    "Hong Kong": ("HKG", "Asia-Pacific"),
    "Thailand": ("THA", "Asia-Pacific"),
    "South Korea": ("KOR", "Asia-Pacific"),
    "United Arab Emirates": ("ARE", "Middle East & Africa"),
    "Saudi Arabia": ("SAU", "Middle East & Africa"),
    "Bahrain": ("BHR", "Middle East & Africa"),
    "Israel": ("ISR", "Middle East & Africa"),
    "Lebanon": ("LBN", "Middle East & Africa"),
    "Nigeria": ("NGA", "Middle East & Africa"),
    "South Africa": ("ZAF", "Middle East & Africa"),
    "Unspecified": (None, "Unknown"),
}


def iso3(country: str) -> str | None:
    return COUNTRIES.get(country, (None, "Unknown"))[0]


def region(country: str) -> str:
    return COUNTRIES.get(country, (None, "Unknown"))[1]
