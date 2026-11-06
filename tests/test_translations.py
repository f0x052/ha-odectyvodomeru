"""Verify custom integration translation files ship complete UI text."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1] / "custom_components" / "odectyvodomeru"


def translation_keys(value: dict, prefix: str = "") -> set[str]:
    """Collect leaf paths to compare translated UI surfaces."""
    return {
        path
        for key, item in value.items()
        for path in (
            translation_keys(item, f"{prefix}{key}.")
            if isinstance(item, dict)
            else {f"{prefix}{key}"}
        )
    }


@pytest.mark.parametrize("language", ["en", "cs"])
def test_translation_completeness(language: str) -> None:
    """Include selection, options, retry errors, and meter labels in both locales."""
    source = json.loads((ROOT / "strings.json").read_text())
    translated = json.loads((ROOT / "translations" / f"{language}.json").read_text())
    assert translation_keys(source) == translation_keys(translated)
    assert set(translated) == {"title", "config", "options", "selector", "entity"}
    assert set(translated["selector"]["meter_type"]["options"]) == {
        "cold",
        "hot",
        "cal",
    }
    assert "meters" in translated["config"]["step"]
    assert "init" in translated["options"]["step"]
    if language == "en":
        assert translated == source
