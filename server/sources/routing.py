"""Road routes for the rider app's map, from an OSRM server.

    api  --plan([(lat, lng), ...])-->  RoutePlanner  --HTTPS-->  OSRM
                                            |
                                            +-- unreachable / slow: straight-line estimate, labelled as such

Why the backend asks and not the phone: one cache for every phone, one place
that knows the routing service, and the app keeps a single backend to talk to
(app/js/api/client.js). Swapping OSRM for another service touches this file only.

The default server is OSRM's public demo (no key, no guarantees, ~1 request/s):
fine for a demo, not for production — run your own OSRM or use a paid service.
Answers are cached and failures are remembered briefly, so a venue with bad
Wi-Fi costs one timeout, not one per poll.
"""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.request

SOURCE_OSRM = "osrm"
SOURCE_ESTIMATE = "estimate"

CACHE_SIZE = 256
RETRY_AFTER_SEC = 30.0       # after a failure, answer with estimates for this long before asking again
DETOUR_FACTOR = 1.3          # straight line -> rough road distance
ESTIMATE_SPEED_KMH = 25.0    # urban scooter average, lights included
USER_AGENT = "Rider_Fatigue_Detection demo (hackathon project)"


def haversine_m(a, b) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371000.0 * math.asin(math.sqrt(h))


def estimate_distance_m(a, b) -> float:
    return haversine_m(a, b) * DETOUR_FACTOR


def _http_get_json(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read())


class RoutePlanner:
    def __init__(self, base_url: str, timeout: float = 4.0, fetch=_http_get_json, clock=time.time):
        self.base_url = base_url.rstrip("/")  # empty = never go online, always estimate
        self.timeout = timeout
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._cache = {}
        self._offline_until = 0.0
        self.requests = 0
        self.failures = 0

    def plan(self, points: list) -> dict:
        """points: [(lat, lng), ...], two or more, in travel order.
        -> {"source", "distance_m", "duration_s", "legs": [{"distance_m", "duration_s"}],
            "geometry": [[lat, lng], ...],
            "steps": [{"leg", "type", "modifier", "name", "exit", "distance_m", "location": [lat, lng]}]}
        steps are OSRM's manoeuvres, passed on language-neutral (the app words them:
        app/js/utils/text.js); distance_m is the stretch that FOLLOWS the manoeuvre.
        An estimate has only depart/arrive steps — there is no road to give turns for."""
        key = tuple((round(lat, 3), round(lng, 3)) for lat, lng in points)  # ~100 m: nearby phones share an answer
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            online = bool(self.base_url) and self._clock() >= self._offline_until
        if not online:
            return self._estimate(points)
        try:
            route = self._ask_osrm(points)
        except Exception:  # timeout, DNS, HTTP error, unexpected JSON — the map must still draw something
            with self._lock:
                self.failures += 1
                self._offline_until = self._clock() + RETRY_AFTER_SEC
            return self._estimate(points)
        with self._lock:
            if len(self._cache) >= CACHE_SIZE:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = route
        return route

    def status(self) -> dict:
        return {"name": "routing", "server": self.base_url or None, "requests": self.requests,
                "failures": self.failures, "cached": len(self._cache)}

    def _ask_osrm(self, points: list) -> dict:
        with self._lock:
            self.requests += 1
        coords = ";".join(f"{lng:.5f},{lat:.5f}" for lat, lng in points)  # OSRM wants lng,lat
        data = self._fetch(f"{self.base_url}/route/v1/driving/{coords}?overview=full&geometries=geojson&steps=true",
                           self.timeout)
        if data.get("code") != "Ok":
            raise ValueError(f"OSRM: {data.get('code')}")
        route = data["routes"][0]
        return {
            "source": SOURCE_OSRM,
            "distance_m": round(route["distance"]),
            "duration_s": round(route["duration"]),
            "legs": [{"distance_m": round(leg["distance"]), "duration_s": round(leg["duration"])}
                     for leg in route["legs"]],
            "geometry": [[lat, lng] for lng, lat in route["geometry"]["coordinates"]],
            "steps": [
                {"leg": index, "type": step["maneuver"]["type"], "modifier": step["maneuver"].get("modifier"),
                 "name": step.get("name") or "", "exit": step["maneuver"].get("exit"),
                 "distance_m": round(step["distance"]),
                 "location": [step["maneuver"]["location"][1], step["maneuver"]["location"][0]]}
                for index, leg in enumerate(route["legs"]) for step in leg.get("steps", [])],
        }

    def _estimate(self, points: list) -> dict:
        legs = []
        for a, b in zip(points, points[1:]):
            distance = estimate_distance_m(a, b)
            legs.append({"distance_m": round(distance),
                         "duration_s": round(distance / (ESTIMATE_SPEED_KMH / 3.6))})
        return {
            "source": SOURCE_ESTIMATE,
            "distance_m": sum(leg["distance_m"] for leg in legs),
            "duration_s": sum(leg["duration_s"] for leg in legs),
            "legs": legs,
            "geometry": [[lat, lng] for lat, lng in points],
            "steps": [step for index, (a, b) in enumerate(zip(points, points[1:])) for step in (
                {"leg": index, "type": "depart", "modifier": None, "name": "", "exit": None,
                 "distance_m": legs[index]["distance_m"], "location": list(a)},
                {"leg": index, "type": "arrive", "modifier": None, "name": "", "exit": None,
                 "distance_m": 0, "location": list(b)})],
        }
