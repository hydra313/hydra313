from dataclasses import fields
from pathlib import Path

import yaml

from dealfinder.config import _SECRET_ENV, Config, load_config

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.yaml"


def test_example_config_loads_and_matches_defaults():
    # load_config silently ignores a missing file, so check existence explicitly.
    assert EXAMPLE.exists()
    assert load_config(EXAMPLE, env={}) == load_config(None, env={})


def test_example_config_documents_every_non_secret_field():
    documented = set(yaml.safe_load(EXAMPLE.read_text()))
    expected = {f.name for f in fields(Config)} - set(_SECRET_ENV)
    assert documented == expected
