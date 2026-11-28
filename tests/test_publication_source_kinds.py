import pytest

from townlight_comms.store import InvalidInput, RepositoryBase


def _ref(kind):
    return {
        "kind": kind,
        "id": "fictional",
        "revision": "a" * 64,
        "title": "Fictional",
        "url": f"/desktop/v1/{kind}/items/fictional/source",
        "sha256": "a" * 64,
        "eligible": True,
    }

@pytest.mark.parametrize("kind", ["library", "elections"])
def test_accept(kind):
    obj = RepositoryBase.__new__(RepositoryBase)
    ref = _ref(kind)
    assert obj._public_sources([ref]) == [ref]

@pytest.mark.parametrize("kind", ["utilities", "private"])
def test_reject(kind):
    obj = RepositoryBase.__new__(RepositoryBase)
    with pytest.raises(InvalidInput):
        obj._public_sources([_ref(kind)])