from pathlib import Path

import pytest

from sortie.config import load_profile, load_search

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def profile():
    return load_profile(FIXTURES / "profile.yaml")


@pytest.fixture(scope="session")
def search():
    return load_search(FIXTURES / "search.yaml")
