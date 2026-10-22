from __future__ import annotations

import os
import ssl
from typing import Optional
import aiohttp
import certifi
import structlog

logger = structlog.get_logger(__name__)

_SSL_CONTEXT: Optional[ssl.SSLContext] = None


def get_trusted_ssl_context() -> ssl.SSLContext:
    """Return an SSLContext configured with a verified CA bundle.

    Respects SSL_CERT_FILE and REQUESTS_CA_BUNDLE if set in the environment,
    falling back to certifi's Mozilla CA bundle.
    TLS verification and hostname checking are strictly enforced (never disabled).
    """
    global _SSL_CONTEXT
    if _SSL_CONTEXT is not None:
        return _SSL_CONTEXT

    ca_file: Optional[str] = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if not ca_file or not os.path.isfile(ca_file):
        ca_file = certifi.where()

    try:
        ctx = ssl.create_default_context(cafile=ca_file)
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.check_hostname = True
        _SSL_CONTEXT = ctx
        logger.debug("ssl.context_initialized", ca_file=ca_file)
        return ctx
    except Exception as exc:
        logger.error("ssl.context_initialization_failed", ca_file=ca_file, error=str(exc))
        # Fall back to default context with verification still required
        ctx = ssl.create_default_context()
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.check_hostname = True
        return ctx


def create_aiohttp_connector() -> aiohttp.TCPConnector:
    """Create an aiohttp TCPConnector enforcing trusted CA certificate verification."""
    return aiohttp.TCPConnector(ssl=get_trusted_ssl_context())
