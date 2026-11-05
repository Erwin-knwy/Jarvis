"""Weather via Open-Meteo (free, no API key)."""
from __future__ import annotations

import httpx
from anthropic import beta_tool

from .. import config

RISK: dict = {}

WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers",
    81: "showers", 82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}


def _geocode(name: str) -> dict | None:
    r = httpx.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": name, "count": 1, "format": "json"},
        timeout=5,
    )
    res = r.json().get("results") or []
    return res[0] if res else None


@beta_tool(eager_input_streaming=True)
def get_weather(location: str = "", days: int = 1) -> str:
    """Current weather and forecast for a city. Leave location empty for the user's home city.

    Args:
        location: City name, e.g. "Tehran" or "London". Empty = home city.
        days: Forecast days, 1-7.
    """
    location = location or config.get().get("city") or ""
    if not location:
        return "Error: no location given and no home city configured (set `city` in config.yaml)."
    try:
        place = _geocode(location)
        if not place:
            return f"Couldn't find a place called {location}."
        r = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": max(1, min(days, 7)),
            },
            timeout=5,
        )
        d = r.json()
    except httpx.HTTPError as e:
        return f"Error: weather service unreachable ({e.__class__.__name__})."
    cur = d["current"]
    lines = [
        f"{place['name']}, {place.get('country', '')}: now {cur['temperature_2m']}°C "
        f"(feels {cur['apparent_temperature']}°C), {WMO.get(cur['weather_code'], 'unknown')}, "
        f"humidity {cur['relative_humidity_2m']}%, wind {cur['wind_speed_10m']} km/h."
    ]
    daily = d["daily"]
    for i, day in enumerate(daily["time"]):
        lines.append(
            f"{day}: {WMO.get(daily['weather_code'][i], '?')}, {daily['temperature_2m_min'][i]}–"
            f"{daily['temperature_2m_max'][i]}°C, rain chance {daily['precipitation_probability_max'][i]}%"
        )
    return "\n".join(lines)


TOOLS = [get_weather]
