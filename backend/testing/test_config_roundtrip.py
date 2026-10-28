"""
Regression tests for backend/config.json <-> models.Config.

Guards the bug where `Config(**json)` silently dropped `fixture_required_tasks`
(not a declared field), so /schedule/generate ran with the §E.15 hard-exclusion
policy OFF, and `PUT /config` erased the key from config.json.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Config

CONFIG_FILE = Path(__file__).parent.parent / "config.json"
DOCUMENTED_DEFAULT = ["VB02", "VB03", "VB04", "VB05", "VB06", "VB07"]


def test_live_config_file_survives_load_and_dump():
    data = json.loads(CONFIG_FILE.read_text())
    dumped = Config(**data).model_dump()
    missing = set(data) - set(dumped)
    assert not missing, f"keys in config.json dropped by Config: {sorted(missing)}"
    assert dumped["fixture_required_tasks"] == data["fixture_required_tasks"]


def test_fixture_required_tasks_defaults_to_the_documented_list():
    assert Config().model_dump()["fixture_required_tasks"] == DOCUMENTED_DEFAULT


def test_unknown_keys_round_trip_instead_of_being_dropped():
    cfg = Config(**{"some_future_knob": 7})
    assert cfg.model_dump()["some_future_knob"] == 7
    assert Config(**cfg.model_dump()).model_dump()["some_future_knob"] == 7


def test_a_put_style_save_keeps_the_key():
    # What PUT /config does: Config in -> model_dump() -> json file.
    saved = json.loads(json.dumps(Config(**json.loads(CONFIG_FILE.read_text())).model_dump()))
    assert saved["fixture_required_tasks"] == json.loads(CONFIG_FILE.read_text())["fixture_required_tasks"]


def test_pipeline_receives_the_key_from_the_api_path():
    # main.generate_schedule passes config.model_dump() to the pipeline, which indexes the key strictly.
    assert isinstance(Config().model_dump()["fixture_required_tasks"], list)
