"""Shared pytest fixtures keeping offline unit tests isolated from live CDNs."""

import pytest

from linkread_desktop import covers


@pytest.fixture(autouse=True)
def _block_implicit_cover_network(monkeypatch):
    """Prevent unit tests from making real HTTP requests for thumbnails unless mocked."""
    original = covers.fetch_cover_bytes

    def guarded(url, *, session=None):
        if session is None:
            return None
        return original(url, session=session)

    monkeypatch.setattr(covers, "fetch_cover_bytes", guarded)
