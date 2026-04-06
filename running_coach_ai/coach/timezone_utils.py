"""Utility for deriving IANA timezone strings from GPS coordinates."""

import logging

logger = logging.getLogger(__name__)

_tf = None  # lazily initialised module-level singleton


def _get_tf():
    global _tf
    if _tf is None:
        from timezonefinder import TimezoneFinder
        _tf = TimezoneFinder()
    return _tf


def derive_timezone_from_coords(lat: float, lon: float) -> str | None:
    """Return the IANA timezone string for (lat, lon), or None if unresolvable.

    Uses a module-level TimezoneFinder singleton (thread-safe for reads).
    Catches all exceptions and returns None rather than raising, so callers
    can safely fall back to a default timezone.
    """
    try:
        return _get_tf().timezone_at(lat=lat, lng=lon)
    except Exception as e:
        logger.warning("timezonefinder failed for (%.4f, %.4f): %s", lat, lon, e)
        return None
