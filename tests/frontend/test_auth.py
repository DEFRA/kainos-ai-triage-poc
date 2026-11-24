import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "frontend" / "app"))
from components import auth


class TestAuthentication(unittest.TestCase):
    def test_password_hashes_are_salted_and_verified(self):
        first = auth.hash_password("correct password")
        second = auth.hash_password("correct password")
        self.assertNotEqual(first, second)
        self.assertTrue(auth.verify_password("correct password", first))
        self.assertFalse(auth.verify_password("wrong password", first))
        self.assertFalse(auth.verify_password("correct password", "plaintext"))

    def test_authentication_uses_parameterized_lookup(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"password": auth.hash_password("correct password")}
        environment = {"DB_HOST": "host", "DB_NAME": "db", "DB_USER": "user", "DB_PASSWORD": "secret"}
        with patch.dict(auth.os.environ, environment, clear=True):
            with patch.object(auth, "create_db_connection") as create_connection:
                create_connection.return_value.__enter__.return_value = connection
                self.assertTrue(auth.authenticate_user("alice", "correct password"))
                self.assertFalse(auth.authenticate_user("alice", "wrong password"))
                cursor.fetchone.return_value = None
                self.assertFalse(auth.authenticate_user("missing", "correct password"))
        cursor.execute.assert_called_with(
            "SELECT password FROM authentication WHERE username = %s", ("missing",)
        )

    def test_login_sets_authenticated_session_after_success(self):
        streamlit = MagicMock()
        streamlit.session_state = {}
        streamlit.form.return_value.__enter__.return_value = None
        streamlit.text_input.side_effect = ["alice", "correct password"]
        streamlit.form_submit_button.return_value = True
        with patch.object(auth, "st", streamlit), patch.object(auth, "authenticate_user", return_value=True):
            auth.require_login()
        self.assertEqual(streamlit.session_state, {"authenticated": True, "username": "alice"})
        streamlit.rerun.assert_called_once()

    def test_login_does_not_authenticate_on_database_error(self):
        streamlit = MagicMock()
        streamlit.session_state = {}
        streamlit.form.return_value.__enter__.return_value = None
        streamlit.text_input.side_effect = ["alice", "password"]
        streamlit.form_submit_button.return_value = True
        streamlit.stop.side_effect = RuntimeError("stop")
        with patch.object(auth, "st", streamlit), patch.object(auth, "authenticate_user", side_effect=KeyError("DB_HOST")):
            with self.assertRaisesRegex(RuntimeError, "stop"):
                auth.require_login()
        self.assertNotIn("authenticated", streamlit.session_state)
        streamlit.error.assert_called_once()


if __name__ == "__main__":
    unittest.main()