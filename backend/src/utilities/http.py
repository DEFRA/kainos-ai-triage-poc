"""HTTP sessions that use the operating system's certificate trust store."""

import ssl

import requests
import truststore
from requests.adapters import HTTPAdapter


class _SystemTrustAdapter(HTTPAdapter):
    def __init__(self, *args, **kwargs):
        self._ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["ssl_context"] = self._ssl_context
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        proxy_kwargs.setdefault("ssl_context", self._ssl_context)
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def create_system_trust_session() -> requests.Session:
    """Create a Requests session using native system roots for HTTPS."""
    session = requests.Session()
    session.mount("https://", _SystemTrustAdapter())
    return session