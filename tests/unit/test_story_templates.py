"""Unit tests for the magazine-template registry loader (T004)."""

import json
from pathlib import Path

import pytest

from running_coach_ai.coach import story_templates


@pytest.fixture
def fresh_registry(monkeypatch, tmp_path):
    """Point the registry at a temporary directory and reset cache."""
    monkeypatch.setattr(story_templates, "_TEMPLATES_ROOT", tmp_path)
    monkeypatch.setattr(story_templates, "_registry", {})
    monkeypatch.setattr(story_templates, "_loaded", False)
    return tmp_path


def _make_template(root: Path, key: str, *, voice="test voice",
                   affinities=None, missing_files=()) -> Path:
    d = root / key
    d.mkdir()
    files = {
        "template.json": json.dumps({
            "key": key,
            "display_name": f"Display {key}",
            "voice_description": voice,
            "trigger_affinities": affinities or [],
            "thumbnail": "thumbnail.jpg",
        }),
        "cover.html": "<div>cover</div>",
        "inside.html": "<div>inside</div>",
        "template.css": ".tpl-{} {{}}".format(key),
    }
    for name, content in files.items():
        if name in missing_files:
            continue
        (d / name).write_text(content, encoding="utf-8")
    # thumbnail isn't strictly required by _REQUIRED_FILES — skip
    return d


def test_loads_valid_templates(fresh_registry):
    _make_template(fresh_registry, "vogue", affinities=["race_complete"])
    _make_template(fresh_registry, "outside", affinities=["difficult_week"])
    reg = story_templates.load_registry(force=True)
    assert sorted(reg.keys()) == ["outside", "vogue"]
    assert reg["vogue"].trigger_affinities == ["race_complete"]


def test_skips_missing_required_file(fresh_registry, caplog):
    _make_template(fresh_registry, "good", affinities=["pr_set"])
    _make_template(fresh_registry, "broken", missing_files=("template.json",))
    reg = story_templates.load_registry(force=True)
    assert "good" in reg
    assert "broken" not in reg


def test_skips_invalid_json(fresh_registry, monkeypatch):
    d = fresh_registry / "bad"
    d.mkdir()
    (d / "template.json").write_text("not json{", encoding="utf-8")
    (d / "cover.html").write_text("c", encoding="utf-8")
    (d / "inside.html").write_text("i", encoding="utf-8")
    (d / "template.css").write_text("css", encoding="utf-8")

    reg = story_templates.load_registry(force=True)
    assert "bad" not in reg


def test_get_template_falls_back_to_default(fresh_registry):
    _make_template(fresh_registry, "vogue")
    reg = story_templates.load_registry(force=True)
    assert "nonexistent" not in reg

    tpl = story_templates.get_template("nonexistent")
    assert tpl.key == "vogue"


def test_get_template_raises_when_registry_empty(fresh_registry):
    story_templates.load_registry(force=True)
    with pytest.raises(RuntimeError):
        story_templates.get_template("anything")


def test_fallback_for_trigger_uses_affinity(fresh_registry):
    _make_template(fresh_registry, "vogue", affinities=["race_complete"])
    _make_template(fresh_registry, "outside", affinities=["difficult_week"])
    story_templates.load_registry(force=True)

    assert story_templates.fallback_for_trigger("difficult_week") == "outside"
    assert story_templates.fallback_for_trigger("race_complete") == "vogue"
    # Unknown trigger -> default
    assert story_templates.fallback_for_trigger("nonexistent") == story_templates.DEFAULT_TEMPLATE_KEY


def test_is_registered(fresh_registry):
    _make_template(fresh_registry, "vogue")
    story_templates.load_registry(force=True)
    assert story_templates.is_registered("vogue") is True
    assert story_templates.is_registered("nonexistent") is False
