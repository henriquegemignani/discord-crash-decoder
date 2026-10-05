import pytest

from crash_decoder.bundle import load
from crash_decoder.decoder import Decoder


@pytest.fixture(scope="session")
def bundle():
    return load()


@pytest.fixture(scope="session")
def decoder(bundle):
    return Decoder(bundle)
