"""Structural test: verify THEMES, ACCENTS, and localStorage key references in app.html."""

import os
import pytest

APP_HTML = os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "running_coach_ai", "web", "static", "app.html",
)


@pytest.fixture(scope="module")
def app_html_source():
    with open(APP_HTML, encoding="utf-8") as f:
        return f.read()


def test_themes_has_light_and_dark_keys(app_html_source):
    assert '"light"' in app_html_source or "'light'" in app_html_source
    assert '"dark"' in app_html_source or "'dark'" in app_html_source


def test_themes_light_has_required_color_properties(app_html_source):
    for prop in ("bg", "surface", "text", "border"):
        assert prop in app_html_source, f"THEMES missing required color property: {prop}"


def test_accents_has_three_required_keys(app_html_source):
    for accent in ("sage", "terracotta", "stone"):
        assert accent in app_html_source, f"ACCENTS missing accent: {accent}"


def test_accents_have_main_color_value(app_html_source):
    assert "main" in app_html_source, "ACCENTS entries must have a 'main' color value"


def test_localstorage_coach_key_referenced(app_html_source):
    assert "rcai_coach" in app_html_source, \
        "localStorage key 'rcai_coach' not referenced in app.html"


def test_localstorage_mode_key_referenced(app_html_source):
    assert "rcai_mode" in app_html_source, \
        "localStorage key 'rcai_mode' not referenced in app.html"


def test_localstorage_accent_key_referenced(app_html_source):
    assert "rcai_accent" in app_html_source, \
        "localStorage key 'rcai_accent' not referenced in app.html"
