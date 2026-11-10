import ssl
import unittest

import truststore

from backend.src.utilities.http import create_system_trust_session


class SystemTrustSessionTests(unittest.TestCase):
    def test_https_adapter_uses_system_trust_and_keeps_verification_enabled(self):
        with create_system_trust_session() as session:
            adapter = session.get_adapter("https://example.org")

            self.assertIsInstance(
                adapter.poolmanager.connection_pool_kw["ssl_context"],
                truststore.SSLContext,
            )
            self.assertEqual(
                adapter.poolmanager.connection_pool_kw["ssl_context"].protocol,
                ssl.PROTOCOL_TLS_CLIENT,
            )
            self.assertTrue(session.verify)


if __name__ == "__main__":
    unittest.main()