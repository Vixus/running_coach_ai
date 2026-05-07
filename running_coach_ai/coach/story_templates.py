"""Magazine-template registry — file-system loader.

Templates live under `running_coach_ai/web/static/story_templates/<key>/`.
Each template directory MUST contain:
    template.json   — display name, voice description, trigger affinities, thumbnail filename
    cover.html      — Jinja2 fragment for the cover page
    inside.html     — Jinja2 fragment for the inside spread
    template.css    — namespaced styles (under .tpl-<key>)
    thumbnail.jpg   — 320x400 thumbnail used in the swap carousel

The registry loads at Flask app startup and is cached on the app context.
Adding a new template = drop a directory + restart. No code or DB change.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Resolved at module load — points at running_coach_ai/web/static/story_templates/
_TEMPLATES_ROOT = Path(__file__).parent.parent / "web" / "static" / "story_templates"

# Required files in each template directory
_REQUIRED_FILES = ("template.json", "cover.html", "inside.html", "template.css")

# Default fallback template key when registry resolution fails
DEFAULT_TEMPLATE_KEY = "vogue"


@dataclass
class MagazineTemplate:
    key: str
    display_name: str
    voice_description: str
    trigger_affinities: list[str]
    thumbnail: str  # relative filename inside the template directory (typically "thumbnail.jpg")
    directory: Path  # absolute path to the template directory

    def cover_html_path(self) -> Path:
        return self.directory / "cover.html"

    def inside_html_path(self) -> Path:
        return self.directory / "inside.html"

    def thumbnail_url(self) -> str:
        """URL the front-end uses to fetch the thumbnail."""
        return f"/static/story_templates/{self.key}/{self.thumbnail}"

    def to_summary_dict(self) -> dict[str, Any]:
        """Serialise for `/api/admin/stories/templates` and the swap carousel."""
        return {
            "key": self.key,
            "display_name": self.display_name,
            "voice_description": self.voice_description,
            "trigger_affinities": list(self.trigger_affinities),
            "thumbnail": self.thumbnail_url(),
        }


# In-memory registry cache. Loaded once at app startup.
_registry: dict[str, MagazineTemplate] = {}
_loaded = False


def load_registry(force: bool = False) -> dict[str, MagazineTemplate]:
    """Scan the filesystem and build the registry. Idempotent unless force=True."""
    global _registry, _loaded
    if _loaded and not force:
        return _registry

    registry: dict[str, MagazineTemplate] = {}

    if not _TEMPLATES_ROOT.exists():
        logger.warning("Magazine-template root does not exist: %s", _TEMPLATES_ROOT)
        _registry = registry
        _loaded = True
        return registry

    for entry in sorted(_TEMPLATES_ROOT.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_") or entry.name.startswith("."):
            continue

        # Skip if any required file is missing
        missing = [name for name in _REQUIRED_FILES if not (entry / name).is_file()]
        if missing:
            logger.warning(
                "Skipping malformed template '%s' — missing files: %s",
                entry.name, ", ".join(missing),
            )
            continue

        try:
            with (entry / "template.json").open(encoding="utf-8") as fh:
                meta = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping template '%s' — invalid template.json: %s", entry.name, e)
            continue

        try:
            tpl = MagazineTemplate(
                key=str(meta["key"]),
                display_name=str(meta["display_name"]),
                voice_description=str(meta["voice_description"]),
                trigger_affinities=list(meta.get("trigger_affinities", [])),
                thumbnail=str(meta.get("thumbnail", "thumbnail.jpg")),
                directory=entry,
            )
        except KeyError as e:
            logger.warning("Skipping template '%s' — template.json missing field %s", entry.name, e)
            continue

        if tpl.key != entry.name:
            logger.warning(
                "Template directory '%s' declares key '%s' — using directory name as canonical key",
                entry.name, tpl.key,
            )
            tpl.key = entry.name

        registry[tpl.key] = tpl

    _registry = registry
    _loaded = True
    logger.info("Loaded %d magazine templates: %s", len(registry), sorted(registry.keys()))
    return registry


def get_template(key: str) -> MagazineTemplate:
    """Return the named template, falling back to the default if not found."""
    reg = load_registry()
    if key in reg:
        return reg[key]
    if DEFAULT_TEMPLATE_KEY in reg:
        logger.warning("Template '%s' not in registry — falling back to '%s'", key, DEFAULT_TEMPLATE_KEY)
        return reg[DEFAULT_TEMPLATE_KEY]
    # Last resort — registry is empty. Return a synthetic placeholder so the caller
    # doesn't crash; the public route will render an empty page in this case.
    raise RuntimeError(
        f"Magazine-template registry is empty (looked under {_TEMPLATES_ROOT})."
    )


def list_templates() -> list[MagazineTemplate]:
    """Return all registered templates in stable (alphabetical) order."""
    reg = load_registry()
    return [reg[k] for k in sorted(reg.keys())]


def is_registered(key: str) -> bool:
    """True if the key resolves to a real registered template."""
    return key in load_registry()


def fallback_for_trigger(trigger_kind: str) -> str:
    """Return the highest-affinity template key for a trigger, or the default."""
    reg = load_registry()
    candidates = [t for t in reg.values() if trigger_kind in t.trigger_affinities]
    if candidates:
        # Stable order — alphabetical by key for determinism
        return sorted(candidates, key=lambda t: t.key)[0].key
    return DEFAULT_TEMPLATE_KEY
