#!/usr/bin/env python3
"""
Butterfly Atlas — locality re-geocoder
======================================

Re-geocodes the localities in data/butterflies.json *in place*, without
re-scraping Flickr. Only unique localities are looked up (a few hundred
rather than a few thousand photos), so a full pass takes minutes, not hours.

Why the original geocoding failed on things like
"Yanachaga Chemillen NP, Pasco, Peru":

  1. Abbreviations. Nominatim indexes "National Park", not "NP". A query
     containing "NP" frequently matches nothing at all.
  2. Free-form only. One long comma string has to match as a single place.
     Nominatim's *structured* query (city / county / country as separate
     fields) is far more forgiving.
  3. One provider. If Nominatim has no entry (or was rate-limiting during
     the original run) there was no second opinion.
  4. Accents. "Chemillen" vs "Chemillén" can be the difference between a
     hit and a miss, so both are tried.

This script addresses all four: it expands abbreviations, tries structured
then free-form Nominatim, then falls back to Photon (also free, also
OpenStreetMap-based, no API key, and much better at fuzzy/partial names),
and only then starts dropping detail.

Usage:
    python3 fix_geocode.py                 # re-do only approximate/missing ones
    python3 fix_geocode.py --all           # re-do every locality from scratch
    python3 fix_geocode.py --self-test     # offline checks, no network
    python3 fix_geocode.py --dry-run       # report what would change, write nothing
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata

try:
    import requests
except ImportError:
    requests = None

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data", "butterflies.json")
CACHE = os.path.join(HERE, "geocache2.json")
UA = "ButterflyAtlas/1.0 (locality re-geocoder; contact via github)"

MAX_ATTEMPTS = 6

# ISO 3166-1 alpha-2 code for every country and territory, plus common
# alternates ("UK", "USA", "Türkiye"). Passed to Nominatim as `countrycodes`,
# which is the strongest constraint available — it filters server-side.
# Generated from the ISO 3166 dataset, so a new destination needs no edit.
COUNTRY_CODE = {
    "afghanistan": "af", "albania": "al", "algeria": "dz", "american samoa": "as",
    "andorra": "ad", "angola": "ao", "anguilla": "ai", "antigua & barbuda": "ag",
    "antigua and barbuda": "ag", "argentina": "ar", "armenia": "am", "aruba": "aw",
    "australia": "au", "austria": "at", "azerbaijan": "az", "bahamas": "bs",
    "bahrain": "bh", "bangladesh": "bd", "barbados": "bb", "belarus": "by",
    "belgium": "be", "belize": "bz", "benin": "bj", "bermuda": "bm",
    "bhutan": "bt", "bolivia": "bo", "bosnia & herzegovina": "ba", "bosnia and herzegovina": "ba",
    "botswana": "bw", "bouvet island": "bv", "brazil": "br", "britain": "gb",
    "british indian ocean territory": "io", "british virgin islands": "vg", "brunei": "bn", "bulgaria": "bg",
    "burkina faso": "bf", "burma": "mm", "burundi": "bi", "cabo verde": "cv",
    "cambodia": "kh", "cameroon": "cm", "canada": "ca", "cape verde": "cv",
    "caribbean netherlands": "bq", "cayman islands": "ky", "central african republic": "cf", "chad": "td",
    "chile": "cl", "china": "cn", "christmas island": "cx", "cocos islands": "cc",
    "colombia": "co", "comoros": "km", "cook islands": "ck", "costa rica": "cr",
    "croatia": "hr", "cuba": "cu", "curaçao": "cw", "cyprus": "cy",
    "czech republic": "cz", "czechia": "cz", "côte d'ivoire": "ci", "côte d’ivoire": "ci",
    "denmark": "dk", "djibouti": "dj", "dominica": "dm", "dominican republic": "do",
    "dr congo": "cd", "east timor": "tl", "ecuador": "ec", "egypt": "eg",
    "el salvador": "sv", "england": "gb", "equatorial guinea": "gq", "eritrea": "er",
    "estonia": "ee", "eswatini": "sz", "ethiopia": "et", "falkland islands": "fk",
    "faroe islands": "fo", "fiji": "fj", "finland": "fi", "france": "fr",
    "french guiana": "gf", "french polynesia": "pf", "french southern territories": "tf", "gabon": "ga",
    "gambia": "gm", "georgia": "ge", "germany": "de", "ghana": "gh",
    "gibraltar": "gi", "great britain": "gb", "greece": "gr", "greenland": "gl",
    "grenada": "gd", "guadeloupe": "gp", "guam": "gu", "guatemala": "gt",
    "guernsey": "gg", "guinea": "gn", "guinea-bissau": "gw", "guyana": "gy",
    "haiti": "ht", "heard & mcdonald islands": "hm", "heard and mcdonald islands": "hm", "holy see": "va",
    "honduras": "hn", "hong kong": "hk", "hungary": "hu", "iceland": "is",
    "india": "in", "indonesia": "id", "iran": "ir", "iraq": "iq",
    "ireland": "ie", "isle of man": "im", "israel": "il", "italy": "it",
    "ivory coast": "ci", "jamaica": "jm", "japan": "jp", "jersey": "je",
    "jordan": "jo", "kazakhstan": "kz", "kenya": "ke", "kiribati": "ki",
    "kuwait": "kw", "kyrgyzstan": "kg", "lao pdr": "la", "laos": "la",
    "latvia": "lv", "lebanon": "lb", "lesotho": "ls", "liberia": "lr",
    "libya": "ly", "liechtenstein": "li", "lithuania": "lt", "luxembourg": "lu",
    "macao": "mo", "macedonia": "mk", "madagascar": "mg", "malawi": "mw",
    "malaysia": "my", "maldives": "mv", "mali": "ml", "malta": "mt",
    "marshall islands": "mh", "martinique": "mq", "mauritania": "mr", "mauritius": "mu",
    "mayotte": "yt", "mexico": "mx", "micronesia": "fm", "moldova": "md",
    "monaco": "mc", "mongolia": "mn", "montenegro": "me", "montserrat": "ms",
    "morocco": "ma", "mozambique": "mz", "myanmar": "mm", "namibia": "na",
    "nauru": "nr", "nepal": "np", "netherlands": "nl", "new caledonia": "nc",
    "new zealand": "nz", "nicaragua": "ni", "niger": "ne", "nigeria": "ng",
    "niue": "nu", "norfolk island": "nf", "north korea": "kp", "north macedonia": "mk",
    "northern ireland": "gb", "northern mariana islands": "mp", "norway": "no", "oman": "om",
    "pakistan": "pk", "palau": "pw", "palestine": "ps", "panama": "pa",
    "papua new guinea": "pg", "paraguay": "py", "peru": "pe", "philippines": "ph",
    "pitcairn": "pn", "poland": "pl", "portugal": "pt", "puerto rico": "pr",
    "qatar": "qa", "republic of the congo": "cg", "romania": "ro", "russia": "ru",
    "russian federation": "ru", "rwanda": "rw", "réunion": "re", "samoa": "ws",
    "san marino": "sm", "saudi arabia": "sa", "scotland": "gb", "senegal": "sn",
    "serbia": "rs", "seychelles": "sc", "sierra leone": "sl", "singapore": "sg",
    "sint maarten": "sx", "slovakia": "sk", "slovenia": "si", "solomon islands": "sb",
    "somalia": "so", "south africa": "za", "south georgia & south sandwich islands": "gs", "south georgia and south sandwich islands": "gs",
    "south korea": "kr", "south sudan": "ss", "spain": "es", "sri lanka": "lk",
    "st. barthélemy": "bl", "st. helena": "sh", "st. kitts & nevis": "kn", "st. kitts and nevis": "kn",
    "st. lucia": "lc", "st. martin": "mf", "st. pierre & miquelon": "pm", "st. pierre and miquelon": "pm",
    "st. vincent & grenadines": "vc", "st. vincent and grenadines": "vc", "sudan": "sd", "suriname": "sr",
    "svalbard & jan mayen": "sj", "svalbard and jan mayen": "sj", "swaziland": "sz", "sweden": "se",
    "switzerland": "ch", "syria": "sy", "são tomé & príncipe": "st", "são tomé and príncipe": "st",
    "taiwan": "tw", "tajikistan": "tj", "tanzania": "tz", "thailand": "th",
    "timor-leste": "tl", "togo": "tg", "tokelau": "tk", "tonga": "to",
    "trinidad & tobago": "tt", "trinidad and tobago": "tt", "tunisia": "tn", "turkey": "tr",
    "turkiye": "tr", "turkmenistan": "tm", "turks & caicos islands": "tc", "turks and caicos islands": "tc",
    "tuvalu": "tv", "türkiye": "tr", "u.k.": "gb", "u.s.": "us",
    "u.s. outlying islands": "um", "u.s. virgin islands": "vi", "u.s.a.": "us", "uae": "ae",
    "uganda": "ug", "uk": "gb", "ukraine": "ua", "united arab emirates": "ae",
    "united kingdom": "gb", "united states": "us", "united states of america": "us", "uruguay": "uy",
    "usa": "us", "uzbekistan": "uz", "vanuatu": "vu", "vatican city": "va",
    "venezuela": "ve", "viet nam": "vn", "vietnam": "vn", "wales": "gb",
    "wallis & futuna": "wf", "wallis and futuna": "wf", "western sahara": "eh", "yemen": "ye",
    "zaire": "cd", "zambia": "zm", "zimbabwe": "zw", "åland islands": "ax",
}

# Bounding boxes, used to REJECT a result that lands in the wrong country —
# geocoders happily return "Bolivia, Missouri" for Bolivia. Fetched from
# Nominatim, so they include overseas territories (Ecuador's covers the
# Galapagos; the old hand-written box did not, and would have rejected a
# correct Galapagos hit). Countries whose territories straddle the
# antimeridian are omitted: their box spans the whole globe and constrains
# nothing. A country with no box here simply skips the check.
COUNTRY_BOX = {
    "afghanistan": (29.38, 38.49, 60.52, 74.89),
    "albania": (39.64, 42.66, 19.0, 21.06),
    "algeria": (18.97, 37.3, -8.67, 12.0),
    "andorra": (42.43, 42.66, 1.41, 1.79),
    "angola": (-18.04, -4.35, 11.46, 24.09),
    "anguilla": (18.06, 18.8, -63.64, -62.71),
    "antigua & barbuda": (16.76, 17.95, -62.55, -61.45),
    "argentina": (-55.19, -21.78, -73.56, -53.64),
    "armenia": (38.84, 41.3, 43.45, 46.63),
    "australia": (-55.32, -9.09, 72.25, 168.23),
    "austria": (46.37, 49.02, 9.53, 17.16),
    "azerbaijan": (38.39, 41.96, 44.76, 51.18),
    "bahamas": (20.71, 27.47, -80.7, -72.45),
    "bahrain": (25.54, 26.69, 50.27, 50.92),
    "bangladesh": (20.37, 26.64, 88.01, 92.68),
    "barbados": (12.85, 13.54, -59.86, -59.21),
    "belarus": (51.26, 56.17, 23.18, 32.76),
    "belgium": (49.5, 51.55, 2.39, 6.41),
    "belize": (15.89, 18.5, -89.23, -87.28),
    "benin": (6.04, 12.41, 0.78, 3.85),
    "bermuda": (32.05, 32.59, -65.12, -64.41),
    "bhutan": (26.7, 28.25, 88.75, 92.13),
    "bolivia": (-22.9, -9.67, -69.65, -57.45),
    "bosnia & herzegovina": (42.56, 45.28, 15.73, 19.62),
    "botswana": (-26.91, -17.78, 20.0, 29.38),
    "brazil": (-33.87, 5.27, -73.98, -28.63),
    "britain": (49.67, 61.06, -14.02, 2.09),
    "british indian ocean territory": (-7.49, -5.19, 71.19, 72.55),
    "british virgin islands": (18.11, 18.95, -64.96, -64.06),
    "brunei": (4.0, 5.2, 114.0, 115.36),
    "bulgaria": (41.24, 44.22, 22.36, 28.89),
    "burkina faso": (9.41, 15.08, -5.51, 2.41),
    "burma": (9.53, 28.55, 92.17, 101.17),
    "burundi": (-4.47, -2.31, 29.0, 30.85),
    "cabo verde": (14.61, 17.41, -25.57, -22.45),
    "cambodia": (9.41, 14.69, 102.33, 107.63),
    "cameroon": (1.65, 13.08, 8.38, 16.19),
    "canada": (41.68, 83.34, -141.0, -52.32),
    "cape verde": (14.61, 17.41, -25.57, -22.45),
    "cayman islands": (19.06, 19.96, -81.63, -79.51),
    "central african republic": (2.22, 11.0, 14.41, 27.47),
    "chad": (7.44, 23.45, 13.47, 24.0),
    "chile": (-56.73, -17.5, -109.68, -66.08),
    "china": (8.67, 53.56, 73.5, 134.78),
    "colombia": (-4.23, 16.05, -82.12, -66.85),
    "comoros": (-12.99, -11.16, 42.83, 44.97),
    "cook islands": (-22.16, -8.72, -166.13, -157.11),
    "costa rica": (5.5, 11.22, -87.1, -82.43),
    "croatia": (42.18, 46.56, 13.21, 19.45),
    "cuba": (19.63, 23.48, -85.17, -73.92),
    "cyprus": (34.44, 35.91, 32.02, 34.86),
    "czech republic": (48.55, 51.06, 12.09, 18.86),
    "czechia": (48.55, 51.06, 12.09, 18.86),
    "côte d’ivoire": (4.16, 10.74, -8.6, -2.49),
    "denmark": (54.45, 57.95, 7.72, 15.55),
    "djibouti": (10.91, 12.79, 41.77, 43.66),
    "dominica": (15.01, 15.79, -61.69, -61.03),
    "dominican republic": (17.27, 21.29, -72.07, -68.11),
    "dr congo": (-13.46, 5.39, 12.04, 31.31),
    "east timor": (-9.61, -8.11, 124.06, 127.54),
    "ecuador": (-5.02, 1.88, -92.21, -75.19),
    "egypt": (21.99, 31.83, 24.65, 37.12),
    "el salvador": (12.95, 14.45, -90.22, -87.6),
    "england": (49.67, 61.06, -14.02, 2.09),
    "equatorial guinea": (-1.67, 3.99, 5.42, 11.41),
    "eritrea": (12.35, 18.07, 36.43, 43.3),
    "estonia": (57.51, 59.94, 21.38, 28.21),
    "eswatini": (-27.32, -25.72, 30.79, 32.13),
    "ethiopia": (3.4, 14.89, 33.0, 47.98),
    "falkland islands": (-53.12, -50.8, -61.77, -57.37),
    "faroe islands": (61.14, 62.6, -8.12, -5.83),
    "finland": (59.45, 70.09, 19.08, 31.59),
    "gabon": (-4.11, 2.32, 8.5, 14.53),
    "gambia": (13.06, 13.83, -17.02, -13.8),
    "georgia": (41.06, 43.59, 39.88, 46.74),
    "germany": (47.27, 55.1, 5.87, 15.04),
    "ghana": (4.54, 11.17, -3.26, 1.27),
    "gibraltar": (36.06, 36.16, -5.4, -5.28),
    "great britain": (49.67, 61.06, -14.02, 2.09),
    "greece": (34.72, 41.75, 19.11, 29.68),
    "greenland": (59.52, 83.88, -74.13, -10.03),
    "grenada": (11.78, 12.61, -62.01, -61.21),
    "guatemala": (13.56, 17.82, -92.36, -88.21),
    "guernsey": (49.22, 49.94, -3.02, -2.05),
    "guinea": (7.19, 12.68, -15.57, -7.64),
    "guinea-bissau": (10.65, 12.69, -16.9, -13.63),
    "guyana": (1.17, 8.6, -61.38, -56.47),
    "haiti": (17.82, 20.29, -75.24, -71.62),
    "holy see": (41.9, 41.91, 12.45, 12.46),
    "honduras": (12.98, 17.62, -89.36, -82.18),
    "hungary": (45.74, 48.59, 16.11, 22.9),
    "iceland": (63.09, 67.35, -25.01, -12.8),
    "india": (6.55, 35.67, 67.95, 97.4),
    "indonesia": (-11.21, 6.27, 94.77, 141.02),
    "iran": (24.84, 39.78, 44.03, 63.33),
    "iraq": (29.06, 37.38, 38.79, 49.11),
    "ireland": (51.22, 55.64, -11.01, -5.66),
    "isle of man": (53.84, 54.55, -5.17, -3.97),
    "israel": (29.45, 33.34, 34.27, 35.9),
    "italy": (35.29, 47.09, 6.63, 18.78),
    "ivory coast": (4.16, 10.74, -8.6, -2.49),
    "jamaica": (16.59, 18.73, -78.58, -75.75),
    "japan": (20.21, 45.71, 122.71, 154.21),
    "jersey": (48.87, 49.46, -2.56, -1.83),
    "jordan": (29.18, 33.37, 34.88, 39.3),
    "kazakhstan": (40.57, 55.44, 46.49, 87.32),
    "kenya": (-4.9, 4.62, 33.91, 41.91),
    "kuwait": (28.52, 30.1, 46.55, 49.0),
    "kyrgyzstan": (39.18, 43.27, 69.26, 80.19),
    "lao pdr": (13.91, 22.51, 100.08, 107.63),
    "laos": (13.91, 22.51, 100.08, 107.63),
    "latvia": (55.67, 58.09, 20.6, 28.24),
    "lebanon": (33.06, 34.69, 34.88, 36.62),
    "lesotho": (-30.68, -28.57, 27.01, 29.46),
    "liberia": (4.16, 8.55, -11.61, -7.37),
    "libya": (19.5, 33.35, 9.39, 25.38),
    "liechtenstein": (47.05, 47.27, 9.47, 9.64),
    "lithuania": (53.9, 56.45, 20.66, 26.84),
    "luxembourg": (49.45, 50.18, 5.74, 6.53),
    "macedonia": (40.85, 42.37, 20.45, 23.03),
    "madagascar": (-25.78, -11.73, 42.97, 50.67),
    "malawi": (-17.13, -9.37, 32.67, 35.92),
    "malaysia": (0.85, 8.38, 98.74, 119.47),
    "maldives": (-0.91, 7.31, 72.36, 73.97),
    "mali": (10.15, 25.0, -12.24, 4.27),
    "malta": (35.59, 36.28, 13.94, 14.82),
    "marshall islands": (4.37, 14.92, 160.59, 172.37),
    "mauritania": (14.72, 27.31, -17.24, -4.83),
    "mauritius": (-20.73, -10.14, 56.38, 63.72),
    "mexico": (14.38, 32.72, -118.6, -86.49),
    "micronesia": (0.82, 10.29, 137.13, 163.24),
    "moldova": (45.47, 48.49, 26.62, 30.16),
    "monaco": (43.52, 43.75, 7.41, 7.53),
    "mongolia": (41.58, 52.15, 87.73, 119.93),
    "montenegro": (41.68, 43.56, 18.42, 20.35),
    "montserrat": (16.47, 16.89, -62.45, -61.94),
    "morocco": (21.33, 36.0, -17.24, -1.0),
    "mozambique": (-26.92, -10.33, 30.21, 41.05),
    "myanmar": (9.53, 28.55, 92.17, 101.17),
    "namibia": (-28.97, -16.96, 11.53, 25.26),
    "nauru": (-0.75, -0.3, 166.71, 167.16),
    "nepal": (26.35, 30.45, 80.06, 88.2),
    "netherlands": (11.78, 53.75, -70.27, 7.23),
    "nicaragua": (10.71, 15.09, -87.9, -82.48),
    "niger": (11.69, 23.52, 0.17, 16.0),
    "nigeria": (4.07, 13.89, 2.68, 14.68),
    "niue": (-19.35, -18.75, -170.16, -169.56),
    "north korea": (37.58, 43.01, 124.13, 130.89),
    "north macedonia": (40.85, 42.37, 20.45, 23.03),
    "northern ireland": (49.67, 61.06, -14.02, 2.09),
    "norway": (-54.65, 81.03, -9.68, 34.69),
    "oman": (16.46, 26.7, 52.0, 60.05),
    "pakistan": (23.43, 37.09, 60.87, 77.12),
    "palau": (2.6, 8.41, 130.92, 134.93),
    "palestine": (31.22, 32.55, 34.22, 35.57),
    "panama": (7.0, 9.85, -83.05, -77.16),
    "papua new guinea": (-11.86, -0.56, 140.84, 159.69),
    "paraguay": (-27.61, -19.29, -62.64, -54.26),
    "peru": (-20.2, -0.04, -84.64, -68.65),
    "philippines": (4.38, 21.32, 114.1, 126.8),
    "pitcairn": (-25.28, -23.71, -130.97, -124.55),
    "poland": (49.0, 55.04, 14.07, 24.15),
    "portugal": (29.83, 42.15, -31.56, -6.19),
    "puerto rico": (-20.2, -0.04, -84.64, -68.65),
    "qatar": (24.47, 26.43, 50.57, 52.64),
    "republic of the congo": (-5.15, 3.71, 11.02, 18.64),
    "romania": (43.62, 48.27, 20.26, 30.05),
    "rwanda": (-2.84, -1.05, 28.86, 30.9),
    "samoa": (-14.28, -13.24, -173.01, -171.19),
    "san marino": (43.89, 43.99, 12.4, 12.52),
    "saudi arabia": (16.29, 32.15, 34.46, 55.67),
    "scotland": (49.67, 61.06, -14.02, 2.09),
    "senegal": (12.24, 16.69, -17.75, -11.35),
    "serbia": (42.23, 46.19, 18.81, 23.01),
    "seychelles": (-10.46, -3.51, 46.0, 56.5),
    "sierra leone": (6.75, 10.0, -13.5, -10.27),
    "singapore": (1.13, 1.51, 103.57, 104.57),
    "slovakia": (47.73, 49.61, 16.83, 22.57),
    "slovenia": (45.42, 46.88, 13.38, 16.6),
    "solomon islands": (-13.24, -4.81, 155.32, 170.4),
    "somalia": (-1.8, 12.19, 40.99, 51.62),
    "south africa": (-47.18, -22.13, 16.33, 38.29),
    "south georgia & south sandwich islands": (-59.67, -53.35, -42.36, -25.89),
    "south korea": (32.91, 38.62, 124.37, 132.12),
    "south sudan": (3.49, 12.24, 23.45, 35.95),
    "spain": (27.43, 43.99, -18.39, 4.59),
    "sri lanka": (5.72, 10.04, 79.42, 82.08),
    "st. helena": (-40.57, -7.69, -14.62, -5.42),
    "st. kitts & nevis": (16.89, 17.62, -63.05, -62.37),
    "st. lucia": (13.51, 14.27, -61.29, -60.66),
    "st. vincent & grenadines": (12.4, 13.58, -61.66, -60.91),
    "sudan": (8.69, 22.01, 21.81, 39.06),
    "suriname": (1.83, 6.22, -58.07, -53.86),
    "swaziland": (-27.32, -25.72, 30.79, 32.13),
    "sweden": (55.14, 69.06, 10.59, 24.18),
    "switzerland": (45.82, 47.81, 5.96, 10.49),
    "syria": (32.31, 37.32, 35.47, 42.37),
    "são tomé & príncipe": (-0.21, 1.93, 6.26, 7.67),
    "taiwan": (10.33, 26.44, 114.29, 122.33),
    "tajikistan": (36.67, 41.05, 67.33, 75.15),
    "tanzania": (-11.76, -0.99, 29.33, 40.66),
    "thailand": (5.61, 20.46, 97.34, 105.64),
    "timor-leste": (-9.61, -8.11, 124.06, 127.54),
    "togo": (5.93, 11.14, -0.14, 1.81),
    "tokelau": (-9.65, -8.33, -172.72, -170.98),
    "tonga": (-24.16, -15.37, -179.4, -173.53),
    "trinidad & tobago": (9.87, 11.56, -62.08, -60.29),
    "tunisia": (30.23, 37.76, 7.52, 11.88),
    "turkey": (35.81, 42.3, 25.57, 44.82),
    "turkiye": (35.81, 42.3, 25.57, 44.82),
    "turkmenistan": (35.13, 42.8, 52.26, 66.71),
    "turks & caicos islands": (20.96, 22.16, -72.68, -70.86),
    "türkiye": (35.81, 42.3, 25.57, 44.82),
    "u.k.": (49.67, 61.06, -14.02, 2.09),
    "uae": (22.63, 26.15, 51.42, 56.6),
    "uganda": (-1.48, 4.23, 29.57, 35.0),
    "uk": (49.67, 61.06, -14.02, 2.09),
    "ukraine": (44.18, 52.38, 22.14, 40.23),
    "united arab emirates": (22.63, 26.15, 51.42, 56.6),
    "united kingdom": (49.67, 61.06, -14.02, 2.09),
    "uruguay": (-35.78, -30.09, -58.49, -53.08),
    "uzbekistan": (37.18, 45.59, 56.0, 73.21),
    "vanuatu": (-20.46, -12.87, 166.34, 170.45),
    "vatican city": (41.9, 41.91, 12.45, 12.46),
    "venezuela": (0.65, 15.92, -73.35, -59.77),
    "viet nam": (7.69, 23.39, 102.14, 114.86),
    "vietnam": (7.69, 23.39, 102.14, 114.86),
    "wales": (49.67, 61.06, -14.02, 2.09),
    "western sahara": (20.67, 27.67, -17.32, -8.67),
    "yemen": (11.91, 19.0, 41.61, 54.74),
    "zaire": (-13.46, 5.39, 12.04, 31.31),
    "zambia": (-18.08, -8.27, 22.0, 33.71),
    "zimbabwe": (-22.42, -15.61, 25.24, 33.07),
}


def country_code(country):
    return COUNTRY_CODE.get((country or "").strip().lower())


def in_country(hit, country):
    """True if the coordinates plausibly sit inside the named country.
    Countries with no box pass (we can only check what we have a box for)."""
    e = COUNTRY_BOX.get((country or "").strip().lower())
    if not e or not hit:
        return True
    lat_min, lat_max, lon_min, lon_max = e
    return (lat_min <= hit["lat"] <= lat_max) and (lon_min <= hit["lon"] <= lon_max)
NOMINATIM = "https://nominatim.openstreetmap.org/search"
PHOTON = "https://photon.komoot.io/api"

# ---------------------------------------------------------------------------
# Query normalisation (pure functions — covered by --self-test)
# ---------------------------------------------------------------------------

# Abbreviations that appear in this collection's locality strings. Nominatim
# and Photon both index the expanded forms, so expanding these is the single
# biggest win available.
ABBREV = [
    (r"\bN\.?\s?P\.?\b", "National Park"),
    (r"\bN\.?\s?R\.?\b", "Nature Reserve"),
    (r"\bN\.?N\.?R\.?\b", "National Nature Reserve"),
    (r"\bW\.?\s?R\.?\b", "Wildlife Reserve"),
    (r"\bG\.?\s?R\.?\b", "Game Reserve"),
    (r"\bF\.?\s?R\.?\b", "Forest Reserve"),
    (r"\bP\.?\s?N\.?\b", "Parque Nacional"),
    (r"\bR\.?\s?N\.?\b", "Reserva Nacional"),
    (r"\bMt\.?\b", "Mount"),
    (r"\bMts\.?\b", "Mountains"),
    (r"\bSt\.?\b", "Saint"),
    (r"\bIs\.?\b", "Island"),
    (r"\bProv\.?\b", "Province"),
    (r"\bDept\.?\b", "Department"),
    (r"\bNr\.?\b", "near"),
]

# Words that describe a position rather than name a place. Keeping them in a
# query usually guarantees a miss ("East slopes of Andes near Satipo").
TAXON_PREFIX_RE = re.compile(
    r"^(?:"
    r"[A-Z][a-z]+\s+sp\.?"          # "Acraea sp" / "Euphaedra sp."
    r"|[A-Z][a-z]+(?:idae|inae)\s+sp\.?"   # "Nymphalidae sp"
    r"|[A-Z]\.\s*[a-z\-]+"          # "P. tringa"
    r"|[A-Z][a-z]+\s+spp\.?"
    r"|eggs?|larva[e]?|caterpillars?"
    r")$", re.IGNORECASE)

NOISE = [
    r"\beast slopes? of\b", r"\bwest slopes? of\b", r"\bnorth slopes? of\b",
    r"\bsouth slopes? of\b", r"\bslopes? of\b", r"\bnear\b", r"\babove\b",
    r"\bbelow\b", r"\bbetween\b", r"\broad to\b", r"\bkm from\b", r"\btrack to\b",
    r"\bvalley of\b", r"\boutskirts of\b",
]


def expand_abbreviations(text):
    """NP -> National Park, Mt -> Mount, etc."""
    out = text or ""
    for pattern, replacement in ABBREV:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip()


def strip_accents(text):
    """Chemillén -> Chemillen (tried as an alternate spelling)."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def strip_noise(text):
    """Remove positional phrasing that stops a locality matching."""
    out = text or ""
    for pattern in NOISE:
        out = re.sub(pattern, " ", out, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip(" ,-")


def clean_locality(text):
    """Strip common-name pollution that leaked in from title parsing.

    The collector mis-split some Flickr titles, leaving the butterfly's
    COMMON NAME at the front of the locality field, e.g.:
        "(Abadima Acraea, Kakum NP, Ghana"   -> "Kakum NP, Ghana"
        "(African Beak) Murchison Falls NP"  -> "Murchison Falls NP"
        "& C. myrmidone, Apuseni Hills"      -> "Apuseni Hills"
    Geocoding the common name as if it were a place is why so many of these
    failed. Strings that are already clean pass through unchanged.
    """
    s = (text or "").strip()
    if ")" in s:                       # "(Common name) Real Place"
        s = s[s.rindex(")") + 1:]
    elif s.startswith("(") or s.startswith("&"):
        parts = [p.strip() for p in s.split(",")]
        if len(parts) > 1:
            s = ", ".join(parts[1:])

    # Drop a leading term that is really a TAXON, not a place. Seen as
    # "Acraea sp, Ankasa NP", "Euphaedra sp., Kyabobo NP", "P. tringa,
    # Yanachaga...", "Nymphalidae sp, Regua". Also drops stray prefixes
    # like "eggs," that came from the title rather than the locality.
    parts = [p.strip() for p in s.split(",")]
    while len(parts) > 1 and TAXON_PREFIX_RE.match(parts[0]):
        parts = parts[1:]
    s = ", ".join(parts)

    # "Coroico - Sol y Luna" behaves like a comma-separated pair
    s = re.sub(r"\s+[-\u2013\u2014]\s+", ", ", s)
    # trailing designations that stop an exact match ("Collard Hill NT")
    s = ", ".join(
        re.sub(r"\s+\b(NT|CP|RDA|LNR)\b\s*$", "", t, flags=re.IGNORECASE).strip()
        for t in s.split(",")
    )
    # a location that is really just the album name ("Bolivia Butterflies")
    s = re.sub(r"^\s*[A-Z][a-z]+\s+Butterflies\s*$", "", s)
    # unbalanced junk: "Bwindi (high altitude", "Kibale 25.11.2017 (1"
    s = re.sub(r"\(.*$", "", s)
    s = re.sub(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b", "", s)
    return re.sub(r"\s+", " ", s).strip(" ,-\u2013\u2014")


def prefer_after_near(text):
    """"East slopes of Andes near Satipo" -> "Satipo".

    When a locality is described relative to somewhere else, the place AFTER
    "near" is the identifiable one. This affects the single largest group in
    the collection (177 photos), so it is worth handling explicitly."""
    m = re.split(r"\bnear\b", text or "", flags=re.IGNORECASE)
    if len(m) == 2 and m[1].strip():
        return m[1].strip(" ,-")
    return None


def route_endpoints(term):
    """"Yendi to Kyabobo NP" -> ["Yendi to Kyabobo NP", "Kyabobo NP", "Yendi"].

    Several localities describe a journey rather than a point. Neither end is
    wrong, so both are offered as fallbacks after the full string."""
    out = [term]
    m = re.split(r"\s+to\s+", term, flags=re.IGNORECASE)
    if len(m) == 2:
        dest = re.sub(r"\b(road|track|trail)\b", "", m[1], flags=re.IGNORECASE).strip(" ,-")
        start = m[0].strip(" ,-")
        for part in (dest, start):
            if part and part not in out:
                out.append(part)
    return out


def locality_terms(location):
    """Split a locality string into its comma-separated parts, cleaned."""
    parts = [p.strip() for p in (location or "").split(",")]
    return [p for p in parts if p]


def build_queries(location, country):
    """Ordered list of (kind, payload) attempts for one locality.

    kind is 'structured' (dict of Nominatim fields), or 'free' (a string).
    Ordered most-specific first; the caller stops at the first hit and marks
    anything after the first entry as approximate.
    """
    attempts = []
    loc_raw = clean_locality(location)   # strip common-name pollution first
    ctry = (country or "").strip()

    expanded = expand_abbreviations(loc_raw)
    cleaned = strip_noise(expanded)
    variants = []
    for v in (expanded, cleaned):
        if v and v not in variants:
            variants.append(v)
        plain = strip_accents(v)
        if plain and plain not in variants:
            variants.append(plain)

    for v in variants:
        terms = locality_terms(v)
        if not terms:
            continue
        # structured: first term is the place, last is usually the region
        structured = {"country": ctry} if ctry else {}
        # Only pass a 'state' when the last term is genuinely a region — if it
        # merely repeats the country, sending it guarantees a miss.
        if len(terms) >= 2 and terms[-1].strip().lower() != ctry.strip().lower():
            attempts.append(("structured", dict(structured, city=terms[0], state=terms[-1])))
        attempts.append(("structured", dict(structured, city=terms[0])))
        # free-form: whole thing, then progressively drop the trailing region
        full = ", ".join(terms + ([ctry] if ctry and ctry not in terms else []))
        attempts.append(("free", full))
        if len(terms) > 1:
            attempts.append(("free", ", ".join([terms[0]] + ([ctry] if ctry else []))))

    # journey-style localities: try each endpoint too
    first_terms = locality_terms(variants[0]) if variants else []
    if first_terms:
        for alt in route_endpoints(first_terms[0])[1:]:
            attempts.append(("free", ", ".join([alt] + ([ctry] if ctry else []))))

    if ctry:
        attempts.append(("free", ctry))

    # de-duplicate, preserving order
    seen, uniq = set(), []
    for kind, payload in attempts:
        key = (kind, json.dumps(payload, sort_keys=True) if isinstance(payload, dict) else payload)
        if key not in seen:
            seen.add(key)
            uniq.append((kind, payload))
    # Cap the cascade: beyond this the queries are too vague to be useful, and
    # 443 localities x 1.1s per request adds up fast. The country-only entry is
    # always kept as the final fallback, even when the cap trims the middle.
    country_only = ("free", ctry) if ctry else None
    if country_only and country_only in uniq:
        uniq = [a for a in uniq if a != country_only]
        return uniq[:MAX_ATTEMPTS - 1] + [country_only]
    return uniq[:MAX_ATTEMPTS]


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class Geocoder:
    def __init__(self, session, cache, delay=1.1):
        self._current_country = ""
        self.s = session
        self.cache = cache
        self.delay = delay
        self.stats = {"nominatim": 0, "photon": 0, "miss": 0, "throttled": 0, "cached": 0}

    def _get(self, url, params):
        for attempt in range(3):
            try:
                r = self.s.get(url, params=params, headers={"User-Agent": UA}, timeout=25)
                time.sleep(self.delay)
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (429, 403):
                    self.stats["throttled"] += 1
                    time.sleep(5 * (attempt + 1))
                    continue
                return None
            except Exception:
                time.sleep(2)
        return None

    def nominatim(self, kind, payload):
        params = {"format": "json", "limit": 1}
        cc = country_code(self._current_country)
        if cc:
            params["countrycodes"] = cc
        if kind == "structured":
            params.update({k: v for k, v in payload.items() if v})
        else:
            params["q"] = payload
        data = self._get(NOMINATIM, params)
        if isinstance(data, list) and data:
            self.stats["nominatim"] += 1
            return {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"])}
        return None

    def photon(self, text, country):
        """Photon is OSM-based, keyless, and far more tolerant of partial or
        slightly-off names than Nominatim — the safety net for places like
        national parks that Nominatim won't match as a whole string."""
        q = text if not country or country in text else (text + ", " + country)
        data = self._get(PHOTON, {"q": q, "limit": 1})
        try:
            feats = (data or {}).get("features") or []
            if feats:
                lon, lat = feats[0]["geometry"]["coordinates"][:2]
                self.stats["photon"] += 1
                return {"lat": float(lat), "lon": float(lon)}
        except Exception:
            pass
        return None

    def resolve(self, location, country):
        """Return (result_or_None, is_approx)."""
        self._current_country = country or ""
        ckey = (location or "") + "|" + (country or "")
        if ckey in self.cache:
            self.stats["cached"] += 1
            c = self.cache[ckey]
            return (c.get("hit"), c.get("approx", False)) if c else (None, False)

        attempts = build_queries(location, country)
        # The primary place name (first term of the cleaned locality). A result
        # is only "approximate" if the query that succeeded no longer contained
        # it — merely rephrasing the SAME place (structured vs free-form,
        # accents stripped, abbreviations expanded) is still an exact location.
        cleaned = clean_locality(location)
        after_near = prefer_after_near(cleaned)
        base = after_near or strip_noise(expand_abbreviations(cleaned))
        primary = (locality_terms(expand_abbreviations(base)) or [""])[0]

        # Split attempts into those that still name the actual place, and the
        # vaguer ones. Everything precise is tried FIRST (Nominatim, then
        # Photon); only if all of that fails do we accept a vague fallback.
        def is_precise(payload):
            text = json.dumps(payload) if isinstance(payload, dict) else payload
            return bool(primary) and primary.lower() in text.lower()

        precise = [(k, p) for k, p in attempts if is_precise(p)]
        vague = [(k, p) for k, p in attempts if not is_precise(p)]

        for kind, payload in precise:
            hit = self.nominatim(kind, payload)
            if hit and in_country(hit, country):
                self.cache[ckey] = {"hit": hit, "approx": False}
                return hit, False
            elif hit:
                self.stats["wrong_country"] = self.stats.get("wrong_country", 0) + 1

        # Photon before falling back — it is far better at reserves, hills and
        # parks that Nominatim will not match as a whole string.
        for text in [t for t in (primary, strip_accents(primary)) if t]:
            hit = self.photon(text, country)
            if hit and in_country(hit, country):
                self.cache[ckey] = {"hit": hit, "approx": False}
                return hit, False
            elif hit:
                self.stats["wrong_country"] = self.stats.get("wrong_country", 0) + 1

        for kind, payload in vague:
            hit = self.nominatim(kind, payload)
            if hit and in_country(hit, country):
                self.cache[ckey] = {"hit": hit, "approx": True}
                return hit, True

        # Nominatim exhausted — try Photon on the cleanest form of the name
        expanded = strip_noise(expand_abbreviations(location or ""))
        for text in [t for t in (expanded, strip_accents(expanded)) if t]:
            hit = self.photon(text, country)
            if hit:
                self.cache[ckey] = {"hit": hit, "approx": False}
                return hit, False

        self.stats["miss"] += 1
        self.cache[ckey] = {"hit": None, "approx": False}
        return None, False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def run(redo_all=False, dry_run=False):
    if requests is None:
        raise SystemExit("Install requests first:  pip install requests")
    if not os.path.exists(DATA):
        raise SystemExit("Can't find %s" % DATA)

    data = load_json(DATA)
    photos = data.get("photos", [])
    print("Loaded %d photos." % len(photos))

    cache = {}
    if os.path.exists(CACHE):
        try:
            cache = load_json(CACHE)
            print("Re-using %d cached lookups." % len(cache))
        except Exception:
            cache = {}

    # Which localities actually need work?
    targets = {}
    for p in photos:
        needs = redo_all or p.get("geoApprox") or p.get("lat") is None
        if not needs:
            continue
        # Group by the CLEANED locality so the same real place isn't looked up
        # once per differing common-name prefix (that was 443 "unique"
        # localities for far fewer actual places).
        # Some records have no country at all (several Armenian sites), which
        # leaves the query unconstrained and ambiguous. The album title names
        # the place in this collection, so use it as a fallback.
        ctry = (p.get("country") or "").strip()
        if not ctry:
            ctry = re.sub(r"\b\d{4}\b|\bButterflies\b", "", p.get("albumTitle") or "",
                          flags=re.IGNORECASE).strip(" ,-")
        key = (clean_locality(p.get("location") or ""), ctry)
        targets.setdefault(key, []).append(p)

    print("%d unique localities to resolve (covering %d photos).\n"
          % (len(targets), sum(len(v) for v in targets.values())))
    if not targets:
        print("Nothing to do.")
        return

    session = requests.Session()
    geo = Geocoder(session, cache)

    fixed = still_approx = failed = 0
    for i, ((loc, ctry), recs) in enumerate(sorted(targets.items()), 1):
        hit, approx = geo.resolve(loc, ctry)
        label = (loc or ctry or "?")[:52]
        if hit:
            status = "approx" if approx else "EXACT"
            if approx:
                still_approx += 1
            else:
                fixed += 1
            for p in recs:
                p["lat"], p["lon"] = hit["lat"], hit["lon"]
                if approx:
                    p["geoApprox"] = True
                else:
                    p.pop("geoApprox", None)
        else:
            failed += 1
            status = "MISS "
        print("[%3d/%3d] %-6s %-52s (%d photo%s)"
              % (i, len(targets), status, label, len(recs), "" if len(recs) == 1 else "s"))
        if i % 25 == 0 and not dry_run:
            save_json(CACHE, cache)

    if not dry_run:
        save_json(CACHE, cache)
        save_json(DATA, data)

    print("\n%s" % ("DRY RUN — nothing written." if dry_run else "Written to %s" % DATA))
    print("  exact matches now: %d localities" % fixed)
    print("  still approximate: %d" % still_approx)
    print("  no match at all  : %d" % failed)
    if geo.stats.get("wrong_country"):
        print("  rejected %d result(s) that fell outside the stated country"
              % geo.stats["wrong_country"])
    print("  provider: %d via Nominatim, %d via Photon, %d cached, %d throttled"
          % (geo.stats["nominatim"], geo.stats["photon"], geo.stats["cached"], geo.stats["throttled"]))
    if not dry_run:
        print("\nNow deploy the updated data file:")
        print("  cd ~/Butterfly-Atlas && git add data/butterflies.json && "
              "git commit -m 'Re-geocode localities' && git push")


def self_test():
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = got == want
        ok = ok and good
        print(("PASS" if good else "FAIL"), "|", name)
        if not good:
            print("      got :", got)
            print("      want:", want)

    check("NP expands to National Park",
          expand_abbreviations("Yanachaga Chemillen NP"), "Yanachaga Chemillen National Park")
    check("Mt expands to Mount", expand_abbreviations("Mt Kenya"), "Mount Kenya")
    check("PN expands to Parque Nacional",
          expand_abbreviations("PN Manu"), "Parque Nacional Manu")
    check("accents stripped", strip_accents("Chemillén"), "Chemillen")
    check("positional noise removed",
          strip_noise("East slopes of Andes near Satipo"), "Andes Satipo")

    qs = build_queries("Yanachaga Chemillén NP, Pasco", "Peru")
    kinds = [k for k, _ in qs]
    frees = [p for k, p in qs if k == "free"]
    print("\n  first 6 attempts for the reported failure:")
    for k, p in qs[:6]:
        print("    %-11s %s" % (k, p))
    check("structured attempted before free-form", kinds[0], "structured")
    expanded_present = any("National Park" in f for f in frees)
    check("an expanded 'National Park' query is attempted", expanded_present, True)
    country_last = qs[-1] == ("free", "Peru")
    check("country-only is the final fallback", country_last, True)

    print("\n==== %s ====" % ("ALL PASS" if ok else "FAILURES"))
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description="Re-geocode localities in butterflies.json")
    ap.add_argument("--all", action="store_true", help="re-do every locality, not just approximate ones")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--self-test", action="store_true", help="offline checks, no network")
    args, _ = ap.parse_known_args()

    if args.self_test:
        self_test()
    try:
        run(redo_all=args.all, dry_run=args.dry_run)
    except KeyboardInterrupt:
        print("\nInterrupted — cache saved, safe to re-run (it resumes).")


if __name__ == "__main__":
    main()
