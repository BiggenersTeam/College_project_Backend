"""
Geocoding Service — converts location strings to lat/lng.
Uses Nominatim (OpenStreetMap) free API — no API key needed.
Results are cached in-process to avoid repeat calls.
"""
import re
import time
import logging
import requests
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# In-process cache: location_string -> (lat, lng)
_GEOCODE_CACHE: dict = {}

# Extensive Indian city/state lookup for instant offline resolution
INDIA_COORDS = {
    "delhi": (28.6139, 77.2090), "new delhi": (28.6139, 77.2090),
    "mumbai": (19.0760, 72.8777), "bombay": (19.0760, 72.8777),
    "pune": (18.5204, 73.8567), "kolkata": (22.5726, 88.3639),
    "calcutta": (22.5726, 88.3639), "chennai": (13.0827, 80.2707),
    "madras": (13.0827, 80.2707), "bengaluru": (12.9716, 77.5946),
    "bangalore": (12.9716, 77.5946), "hyderabad": (17.3850, 78.4867),
    "jaipur": (26.9124, 75.7873), "lucknow": (26.8467, 80.9462),
    "ahmedabad": (23.0225, 72.5714), "bhopal": (23.2599, 77.4126),
    "patna": (25.5941, 85.1376), "chandigarh": (30.7333, 76.7794),
    "varanasi": (25.3176, 82.9739), "surat": (21.1702, 72.8311),
    "nagpur": (21.1458, 79.0882), "indore": (22.7196, 75.8577),
    "thane": (19.2183, 72.9781), "bhubaneswar": (20.2961, 85.8245),
    "visakhapatnam": (17.6868, 83.2185), "vizag": (17.6868, 83.2185),
    "coimbatore": (11.0168, 76.9558), "kochi": (9.9312, 76.2673),
    "cochin": (9.9312, 76.2673), "thiruvananthapuram": (8.5241, 76.9366),
    "trivandrum": (8.5241, 76.9366), "guwahati": (26.1445, 91.7362),
    "ranchi": (23.3441, 85.3096), "raipur": (21.2514, 81.6296),
    "amritsar": (31.6340, 74.8723), "ludhiana": (30.9010, 75.8573),
    "agra": (27.1767, 78.0081), "meerut": (28.9845, 77.7064),
    "rajkot": (22.3039, 70.8022), "jodhpur": (26.2389, 73.0243),
    "madurai": (9.9252, 78.1198), "vijayawada": (16.5062, 80.6480),
    "gwalior": (26.2183, 78.1828), "nashik": (19.9975, 73.7898),
    "faridabad": (28.4089, 77.3178), "mira-bhayandar": (19.2952, 72.8544),
    "aurangabad": (19.8762, 75.3433), "solapur": (17.6599, 75.9064),
    "jammu": (32.7266, 74.8570), "srinagar": (34.0837, 74.7973),
    "shimla": (31.1048, 77.1734), "dehradun": (30.3165, 78.0322),
    "haridwar": (29.9457, 78.1642), "allahabad": (25.4358, 81.8463),
    "prayagraj": (25.4358, 81.8463), "kanpur": (26.4499, 80.3319),
    "gorakhpur": (26.7606, 83.3732), "aligarh": (27.8974, 78.0880),
    "bareilly": (28.3670, 79.4304), "moradabad": (28.8389, 78.7768),
    "maharashtra": (19.7515, 75.7139), "gujarat": (22.2587, 71.1924),
    "rajasthan": (27.0238, 74.2179), "madhya pradesh": (22.9734, 78.6569),
    "uttar pradesh": (26.8467, 80.9462), "bihar": (25.0961, 85.3131),
    "west bengal": (22.9868, 87.8550), "karnataka": (15.3173, 75.7139),
    "tamil nadu": (11.1271, 78.6569), "kerala": (10.8505, 76.2711),
    "andhra pradesh": (15.9129, 79.7400), "telangana": (18.1124, 79.0193),
    "odisha": (20.9517, 85.0985), "jharkhand": (23.6102, 85.2799),
    "chhattisgarh": (21.2787, 81.8661), "punjab": (31.1471, 75.3412),
    "haryana": (29.0588, 76.0856), "assam": (26.2006, 92.9376),
    "manipur": (24.6637, 93.9063), "meghalaya": (25.4670, 91.3662),
    "tripura": (23.9408, 91.9882), "mizoram": (23.1645, 92.9376),
    "nagaland": (26.1584, 94.5624), "arunachal pradesh": (28.2180, 94.7278),
    "sikkim": (27.5330, 88.5122), "goa": (15.2993, 74.1240),
    "himachal pradesh": (31.1048, 77.1734), "uttarakhand": (30.0668, 79.0193),
    "india": (20.5937, 78.9629),
}


def geocode_location(location_string: str) -> Optional[Tuple[float, float]]:
    """
    Convert a location string to (latitude, longitude).
    1. Check in-process cache
    2. Check offline India dictionary (instant)
    3. Call Nominatim API (with India bias)
    Returns None if geocoding fails.
    """
    if not location_string or not location_string.strip():
        return None

    cache_key = location_string.strip().lower()

    # Cache hit
    if cache_key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[cache_key]

    # Offline dictionary — fast
    for city_name, coords in INDIA_COORDS.items():
        if city_name in cache_key:
            _GEOCODE_CACHE[cache_key] = coords
            return coords

    # Nominatim API fallback
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": f"{location_string}, India",
            "format": "json",
            "limit": 1,
            "countrycodes": "in",
        }
        headers = {
            "User-Agent": "FaceTracer-MissingPersonsPortal/1.0 (support@facetracer.gov.in)"
        }
        resp = requests.get(url, params=params, headers=headers, timeout=5)
        if resp.status_code == 200:
            results = resp.json()
            if results:
                lat = float(results[0]["lat"])
                lng = float(results[0]["lon"])
                coords = (lat, lng)
                _GEOCODE_CACHE[cache_key] = coords
                logger.info(f"Geocoded '{location_string}' -> {coords}")
                return coords
    except Exception as e:
        logger.warning(f"Nominatim geocoding failed for '{location_string}': {e}")

    # Could not geocode — return None (caller will handle)
    _GEOCODE_CACHE[cache_key] = None
    return None
