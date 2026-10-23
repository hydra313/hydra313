from pathlib import Path

from dealfinder.config import load_config

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.yaml"


def test_example_config_loads_and_matches_defaults():
    # load_config silently ignores a missing file, so check existence explicitly.
    assert EXAMPLE.exists()
    assert load_config(EXAMPLE, env={}) == load_config(None, env={})
