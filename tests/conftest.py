"""Chaque test écrit ses réglages dans un dossier temporaire, jamais dans ceux de l'utilisateur."""

import pytest


@pytest.fixture(autouse=True)
def isolated_user_data(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWXAV_DATA_DIR", str(tmp_path / "donnees"))
