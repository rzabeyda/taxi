import asyncio
import math
import re

import aiohttp

UA = {"User-Agent": "taxi-bot/1.0 (memstroy.app)"}


def full_query(text: str) -> str:
    t = text.strip()
    low = t.lower()
    if "tallinn" in low or "таллин" in low:
        return t
    return f"{t}, Tallinn, Estonia"


def haversine_m(lat1, lng1, lat2, lng2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def _nominatim(session, query):
    params = {"q": query, "format": "json", "limit": 1, "countrycodes": "ee"}
    async with session.get(
        "https://nominatim.openstreetmap.org/search",
        params=params,
        headers=UA,
        timeout=aiohttp.ClientTimeout(total=8),
    ) as r:
        data = await r.json()
    if data:
        return float(data[0]["lat"]), float(data[0]["lon"])
    return None


async def geocode(session, text):
    try:
        res = await _nominatim(session, full_query(text))
        if res:
            return res
        # fallback: drop house number
        stripped = re.sub(r"\s+\d+\s*[a-zA-Z]?\b", " ", text).strip()
        if stripped and stripped != text:
            await asyncio.sleep(1.1)
            return await _nominatim(session, full_query(stripped))
    except Exception:
        return None
    return None


async def route_km(session, a, b):
    """Road distance in km between (lat,lng) points; falls back to straight line * 1.3."""
    try:
        url = f"https://router.project-osrm.org/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}?overview=false"
        async with session.get(
            url, headers=UA, timeout=aiohttp.ClientTimeout(total=8)
        ) as r:
            data = await r.json()
        return round(data["routes"][0]["distance"] / 1000, 1)
    except Exception:
        return round(haversine_m(a[0], a[1], b[0], b[1]) * 1.3 / 1000, 1)
