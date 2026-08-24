"""Shared date formats for parsing across the extraction pipeline."""

KNOWN_DATE_FORMATS = [
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d/%m/%y",
    "%m/%d/%y",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%d-%m-%y",
    "%m-%d-%y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d.%m.%Y",
    "%d.%m.%y",
]
