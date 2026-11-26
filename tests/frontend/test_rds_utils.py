import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from psycopg import sql

from frontend.app.utilities import rds_utils


class TestCreateDbConnection(unittest.TestCase):
    def setUp(self):
        self.connection_args = {
            "host": "db.example",
            "dbname": "cases",
            "user": "user",
            "password": "password",
        }

    def test_explicit_certificate_takes_precedence(self):
        with patch.dict(os.environ, {"PGSSLROOTCERT": "environment.pem"}):
            with patch.object(rds_utils.psycopg, "connect") as connect:
                rds_utils.create_db_connection(**self.connection_args, sslrootcert="explicit.pem")

        self.assertEqual(connect.call_args.kwargs["sslrootcert"], "explicit.pem")
        self.assertEqual(connect.call_args.kwargs["sslmode"], "verify-full")

    def test_environment_certificate_overrides_bundle(self):
        with patch.dict(os.environ, {"PGSSLROOTCERT": "environment.pem"}):
            with patch.object(rds_utils.psycopg, "connect") as connect:
                rds_utils.create_db_connection(**self.connection_args)

        self.assertEqual(connect.call_args.kwargs["sslrootcert"], "environment.pem")

    def test_bundled_certificate_is_default(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(rds_utils.psycopg, "connect") as connect:
                rds_utils.create_db_connection(**self.connection_args)

        self.assertEqual(connect.call_args.kwargs["sslrootcert"], str(rds_utils.RDS_CA_BUNDLE))


class TestCreateTable(unittest.TestCase):
    def test_create_standalone_table(self):
        connection = MagicMock()
        rds_utils.create_table(
            connection,
            table_name="authentication",
            columns={"username": "TEXT PRIMARY KEY", "password": "TEXT NOT NULL"},
        )
        cursor = connection.cursor.return_value.__enter__.return_value
        self.assertEqual(
            cursor.execute.call_args.args[0].as_string(),
            'CREATE TABLE "authentication" ("username" TEXT PRIMARY KEY, "password" TEXT NOT NULL)',
        )
        connection.commit.assert_not_called()

    def test_create_related_table_quotes_identifiers(self):
        connection = MagicMock()
        rds_utils.create_table(
            connection,
            table_name='review"table',
            columns={"id": "BIGINT PRIMARY KEY", "consultation_id": "BIGINT NOT NULL"},
            constraints=("FOREIGN KEY (consultation_id) REFERENCES consultations (id)",),
            if_not_exists=True,
        )
        cursor = connection.cursor.return_value.__enter__.return_value
        self.assertEqual(
            cursor.execute.call_args.args[0].as_string(),
            'CREATE TABLE IF NOT EXISTS "review""table" ("id" BIGINT PRIMARY KEY, '
            '"consultation_id" BIGINT NOT NULL, FOREIGN KEY (consultation_id) REFERENCES consultations (id))',
        )

    def test_create_requires_columns(self):
        connection = MagicMock()
        with self.assertRaises(ValueError):
            rds_utils.create_table(connection, table_name="empty", columns={})
        connection.cursor.assert_not_called()


class TestInsertReview(unittest.TestCase):
    def test_insert_uses_identifier_and_parameters_without_committing(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"id": 123}
        review_datetime = datetime(2026, 11, 24, tzinfo=timezone.utc)
        table_name = 'reviews; DROP TABLE "cases"'

        result = rds_utils.insert_review(
            connection,
            table_name=table_name,
            consultation_id="consultation-1",
            review_datetime=review_datetime,
            reviewer_name="Reviewer",
            agree_or_disagree="agree",
            comments="Looks good",
        )

        query, params = cursor.execute.call_args.args
        self.assertEqual(
            query,
            sql.SQL(
                "INSERT INTO {} (consultation_id, review_datetime, reviewer_name, "
                "agree_or_disagree, comments) VALUES (%s, %s, %s, %s, %s) RETURNING id"
            ).format(sql.Identifier(table_name)),
        )
        self.assertEqual(params, ("consultation-1", review_datetime, "Reviewer", "agree", "Looks good"))
        self.assertEqual(result, 123)
        connection.cursor.assert_called_once_with(row_factory=rds_utils.dict_row)
        connection.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()