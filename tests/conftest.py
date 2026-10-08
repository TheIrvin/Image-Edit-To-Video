import pytest

from app import core, jobs, render, speech


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    directory = tmp_path / "data"
    for module in (core, jobs, render, speech):
        if hasattr(module, "DATA"):
            monkeypatch.setattr(module, "DATA", directory)
    core.ensure_data()
    return directory
