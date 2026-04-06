"""Open-Meteo weather API client."""

import logging
from datetime import date, timedelta

import requests

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather code → human-readable condition string
WMO_CONDITIONS = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

# WMO codes that trigger indoor/rest adjustment
DANGEROUS_CODES = {71, 73, 75, 77, 85, 86, 95, 96, 99}
HEAVY_RAIN_CODES = {65, 81, 82}


def get_forecast(lat: float, lon: float) -> dict:
    """Fetch 7-day hourly forecast from Open-Meteo (no API key required)."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,precipitation_probability,windspeed_10m,weathercode,relativehumidity_2m",
        "forecast_days": 7,
        "timezone": "auto",
    }
    response = requests.get(OPEN_METEO_URL, params=params, timeout=10)
    response.raise_for_status()
    return response.json()


def summarise_forecast(data: dict) -> list[dict]:
    """Extract a daily summary for today + next 3 days from hourly data.

    Returns a list of dicts with keys:
      date, condition, temp_min, temp_max, wind_max, precip_prob, wmo_code, adjustment
    """
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    precip_probs = hourly.get("precipitation_probability", [])
    winds = hourly.get("windspeed_10m", [])
    codes = hourly.get("weathercode", [])

    today = date.today()
    daily: dict[str, dict] = {}

    for i, time_str in enumerate(times):
        day_str = time_str[:10]
        day = date.fromisoformat(day_str)
        if day < today or day > today + timedelta(days=3):
            continue

        if day_str not in daily:
            daily[day_str] = {
                "date": day_str,
                "temps": [],
                "precip_probs": [],
                "winds": [],
                "codes": [],
            }

        if i < len(temps) and temps[i] is not None:
            daily[day_str]["temps"].append(temps[i])
        if i < len(precip_probs) and precip_probs[i] is not None:
            daily[day_str]["precip_probs"].append(precip_probs[i])
        if i < len(winds) and winds[i] is not None:
            daily[day_str]["winds"].append(winds[i])
        if i < len(codes) and codes[i] is not None:
            daily[day_str]["codes"].append(codes[i])

    summaries = []
    for day_str in sorted(daily.keys()):
        d = daily[day_str]
        temp_min = round(min(d["temps"]), 1) if d["temps"] else None
        temp_max = round(max(d["temps"]), 1) if d["temps"] else None
        precip_prob = max(d["precip_probs"]) if d["precip_probs"] else 0
        wind_max = round(max(d["winds"]), 1) if d["winds"] else 0
        # Most frequent WMO code for the day (from daytime hours 6–20)
        wmo_code = max(set(d["codes"]), key=d["codes"].count) if d["codes"] else 0
        condition = WMO_CONDITIONS.get(wmo_code, f"WMO {wmo_code}")

        adjustment = _get_adjustment(temp_max, precip_prob, wind_max, wmo_code)

        summaries.append({
            "date": day_str,
            "condition": condition,
            "temp_min": temp_min,
            "temp_max": temp_max,
            "wind_max": wind_max,
            "precip_prob": precip_prob,
            "wmo_code": wmo_code,
            "adjustment": adjustment,
        })

    return summaries


def _get_adjustment(temp_max: float | None, precip_prob: int, wind_max: float, wmo_code: int) -> str:
    """Return a coaching adjustment note based on weather conditions."""
    if wmo_code in DANGEROUS_CODES:
        return "move_to_rest_or_indoor"
    if wmo_code in HEAVY_RAIN_CODES and wind_max > 30:
        return "swap_quality_for_treadmill"
    if temp_max and temp_max > 28:
        return "reduce_pace_targets_15_20s_per_km"
    if temp_max and 10 <= temp_max <= 18 and wmo_code <= 3 and wind_max < 15:
        return "optimal_conditions"
    return "no_change"
