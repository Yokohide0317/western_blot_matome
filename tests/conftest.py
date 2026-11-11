import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTRUCTIONS = REPO / "instructions"


@pytest.fixture(autouse=True)
def isolated_user_dirs(tmp_path_factory, monkeypatch):
    """Keep the server state, project list and render cache out of the real home folder."""
    base = tmp_path_factory.mktemp("user")
    monkeypatch.setenv("WBM_HOME", str(base / "home"))
    monkeypatch.setenv("WBM_CACHE", str(base / "cache"))
    monkeypatch.setenv("WBM_APPLICATIONS", str(base / 'applications'))
    monkeypatch.setenv("WBM_HEADLESS", '1')


@pytest.fixture(scope="session")
def input_zip(tmp_path_factory) -> Path:
    # Real exports are private: point WB_INPUT_ZIP at one outside the repository.
    path = Path(os.environ.get("WB_INPUT_ZIP", REPO / "private" / "input.zip"))
    if not path.exists():
        pytest.skip(f"input archive not found: {path}")
    target = tmp_path_factory.mktemp("archives") / path.name
    target.symlink_to(path)
    return target


def _private_json(name: str) -> dict:
    # instructions/ holds real-data references and is kept out of the repository.
    path = INSTRUCTIONS / name
    if not path.exists():
        pytest.skip(f"private test data not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def audit() -> dict:
    return _private_json("western_blot_input_audit.json")


@pytest.fixture(scope="session")
def seed() -> dict:
    return _private_json("western_blot_project_seed.json")


@pytest.fixture(scope="session")
def input_zip_second(tmp_path_factory) -> Path:
    path = Path(os.environ.get("WB_INPUT_ZIP_2", REPO / "private" / "input_2.zip"))
    if not path.exists():
        pytest.skip(f"input archive not found: {path}")
    target = tmp_path_factory.mktemp("archives") / path.name
    target.symlink_to(path)
    return target
