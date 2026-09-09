import pytest

from london_monitor.web_access import _is_trusted, _public_url


def test_source_urls_reject_credentials_and_private_addresses():
    for url in ("http://127.0.0.1", "http://[::1]", "http://169.254.169.254", "file:///etc/passwd"):
        with pytest.raises(ValueError):
            _public_url(url)
    with pytest.raises(ValueError, match="credentials"):
        _public_url("https://user:password@example.com")
    assert _is_trusted("https://www.cbre.co.uk/insights")
    assert not _is_trusted("https://cbre.co.uk.example.com")
