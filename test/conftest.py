# test/conftest.py
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-embedding",
        action="store_true",
        default=False,
        help="Run tests marked with @pytest.mark.embedding (requires live embedding endpoint)",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-embedding"):
        skip = pytest.mark.skip(reason="Pass --run-embedding to run this test")
        for item in items:
            if "embedding" in item.keywords:
                item.add_marker(skip)
