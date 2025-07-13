import pytest
from src.ghgen import main


@pytest.fixture
def config_file(repo):
    return lambda content=None: repo.file("gh-gen.yml", content)


@pytest.fixture
def lock_file(repo):
    return lambda content=None: repo.file("gh-gen.lock", content)
