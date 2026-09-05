"""
Imports REAL EV charging stations (from Open Charge Map) into ChargeEase's
own database, so users can search, filter, and book genuine nearby stations
instead of the old fictional demo data.

HOW IT WORKS:
- You give it a location (city/area name) and a search radius.
- It fetches real charger locations from Open Charge Map around that point.
- It clears out any existing stations and replaces them with the real ones,
  assigning each a fresh sequential station_id.
- Charger type / connector type are derived from each station's real
  connection data. Slot counts start with everyone available, since ChargeEase
  itself hasn't taken any bookings against them yet - availability from then
  on is tracked for real by this app (bookings/cancellations).

USAGE:
    python seed_stations.py
(edit SEARCH_LOCATION / SEARCH_RADIUS_KM / MAX_STATIONS below first)
"""

import os
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# ---- Edit these before running ----
SEARCH_LOCATION = "Mumbai, India"
SEARCH_RADIUS_KM = 40
MAX_STATIONS = 150
# ------------------------------------

uri = os.getenv("MONGO_URI")
if not uri:
    raise SystemExit("MONGO_URI is missing. Create .env first.")

OCM_API_KEY = os.getenv("OCM_API_KEY", "")

client = MongoClient(uri, serverSelectionTimeoutMS=10000)
client.admin.command("ping")
db = client["ChargeEase"]
col = db["stations"]


def geocode(query):
    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "json", "limit": 1},
        headers={"User-Agent": "ChargeEase-EV-App/1.0"},
        timeout=8,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise SystemExit(f"Could not find location: {query}")
    return float(results[0]["lat"]), float(results[0]["lon"])


def fetch_real_stations(lat, lng, radius_km, max_results):
    params = {
        "output": "json",
        "latitude": lat,
        "longitude": lng,
        "distance": radius_km,
        "maxresults": max_results,
        "verbose": "false",
    }
    if OCM_API_KEY:
        params["key"] = OCM_API_KEY

    resp = requests.get(
        "https://api.openchargemap.io/v3/poi/",
        params=params,
        headers={"User-Agent": "ChargeEase-EV-App/1.0 (student project)"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


CONNECTOR_MAP = {
    "ccs": "CCS2",
    "type 2": "Type 2",
    "type2": "Type 2",
    "chademo": "CHAdeMO",
    "gb/t": "GB/T",
    "gbt": "GB/T",
}


def derive_connector_and_type(connections):
    connector_type = "Type 2"
    max_kw = 0

    for c in connections or []:
        title = ((c.get("ConnectionType") or {}).get("Title") or "").lower()
        for key, mapped in CONNECTOR_MAP.items():
            if key in title:
                connector_type = mapped
                break
        power = c.get("PowerKW") or 0
        if power > max_kw:
            max_kw = power

    if max_kw >= 43:
        charger_type = "DC Fast Charger"
    elif max_kw >= 22:
        charger_type = "Fast Charger"
    else:
        charger_type = "AC Charger"

    return connector_type, charger_type


def build_station_doc(poi, station_id, ref_lat, ref_lng):
    import math

    addr = poi.get("AddressInfo") or {}
    lat, lng = addr.get("Latitude"), addr.get("Longitude")
    connections = poi.get("Connections") or []
    connector_type, charger_type = derive_connector_and_type(connections)

    total_slots = poi.get("NumberOfPoints") or 2
    total_slots = max(1, int(total_slots))

    # Haversine distance from the search center, for the default (non-geolocated) sort.
    R = 6371.0
    p1, p2 = math.radians(ref_lat), math.radians(lat)
    dphi = math.radians(lat - ref_lat)
    dlambda = math.radians(lng - ref_lng)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    distance = round(R * 2 * math.atan2(a ** 0.5, (1 - a) ** 0.5), 1)

    town = addr.get("Town", "")
    location = f"{addr.get('AddressLine1', '')}, {town}".strip(", ")

    return {
        "station_id": station_id,
        "name": addr.get("Title", "Charging Station"),
        "location": location or "Unknown location",
        "charger_type": charger_type,
        "connector_type": connector_type,
        "total_slots": total_slots,
        "available_slots": total_slots,
        "waiting_vehicles": 0,
        "distance": distance,
        "latitude": lat,
        "longitude": lng,
    }


def main():
    print(f"Looking up '{SEARCH_LOCATION}'...")
    ref_lat, ref_lng = geocode(SEARCH_LOCATION)

    print(f"Fetching real stations within {SEARCH_RADIUS_KM}km...")
    pois = fetch_real_stations(ref_lat, ref_lng, SEARCH_RADIUS_KM, MAX_STATIONS)

    docs = []
    next_id = 101
    for poi in pois:
        addr = poi.get("AddressInfo") or {}
        if addr.get("Latitude") is None or addr.get("Longitude") is None:
            continue
        docs.append(build_station_doc(poi, next_id, ref_lat, ref_lng))
        next_id += 1

    if not docs:
        raise SystemExit("No real stations found for that location/radius. Try a bigger radius.")

    col.delete_many({})
    col.insert_many(docs)

    print(f"Imported {len(docs)} real charging stations around {SEARCH_LOCATION}.")
    for d in docs[:5]:
        print(f"  #{d['station_id']} {d['name']} - {d['location']} ({d['distance']} km)")
    if len(docs) > 5:
        print(f"  ...and {len(docs) - 5} more.")


if __name__ == "__main__":
    main()