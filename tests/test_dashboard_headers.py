"""Dashboard security headers."""
from monitor.dashboard import SECURITY_HEADERS


def test_csp_forbids_framing():
    csp = SECURITY_HEADERS["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    assert "default-src 'self'" in csp
