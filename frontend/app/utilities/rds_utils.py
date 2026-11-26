import os
from datetime import datetime
from typing import Any, Optional
import boto3
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from pathlib import Path


RDS_CA_BUNDLE = (
    Path(__file__).resolve().parents[3]
    / "certs"
    / "public_bundle_rds_eu-west-2.pem"
)


def create_rds_client(
	*,
	region: str = "eu-west-2",
	profile_name: Optional[str] = None,
) -> Any:
	"""Create an AWS RDS management client using the selected credentials."""

	session = boto3.Session(profile_name=profile_name, region_name=region)
	return session.client("rds")


def create_db_connection(
	*,
	host: str,
	dbname: str,
	user: str,
	password: str,
	port: int = 5432,
	sslrootcert: Optional[str] = None,
) -> Any:
	"""Connect to PostgreSQL, verifying the server certificate and hostname.

	The CA bundle path is selected from sslrootcert, then PGSSLROOTCERT, then the
	London-region bundle in the project certs directory. The bundle must exist
	where the application runs, including inside the Fargate container.
	Use a with statement to commit or roll back and close the connection.

	Example connection:
	    import os

	    with create_db_connection(
	        host=os.environ["DB_HOST"],
	        dbname=os.environ["DB_NAME"],
	        user=os.environ["DB_USER"],
	        password=os.environ["DB_PASSWORD"],
	    ) as connection:
	        rows = fetch_rows(connection, "SELECT * FROM cases WHERE id = %s", (1,))
	"""

	options: dict[str, Any] = {
		"host": host,
		"port": port,
		"dbname": dbname,
		"user": user,
		"password": password,
		"sslmode": "verify-full",
		"sslrootcert": sslrootcert or os.environ.get("PGSSLROOTCERT") or str(RDS_CA_BUNDLE),
		"row_factory": dict_row,
	}
	return psycopg.connect(**options)


def fetch_rows(
	connection: Any,
	query: str,
	params: Optional[tuple[Any, ...]] = None,
) -> list[dict[str, Any]]:
	"""Execute a parameterized query and return its rows as dictionaries."""

	with connection.cursor() as cursor:
		cursor.execute(query, params)
		return cursor.fetchall()


def execute_query(
	connection: Any,
	query: str,
	params: Optional[tuple[Any, ...]] = None,
) -> int:
	"""Execute a statement and return its row count; the caller commits it."""

	with connection.cursor() as cursor:
		cursor.execute(query, params)
		return cursor.rowcount


def create_table(
	connection: Any,
	*,
	table_name: str,
	columns: dict[str, str],
	constraints: tuple[str, ...] = (),
	if_not_exists: bool = False,
) -> None:
	"""Create a table; commit or roll back using the connection.

	Column definitions and constraints are trusted SQL, not user input. For
	related tables, create the referenced table first and pass a constraint such
	as 'FOREIGN KEY (consultation_id) REFERENCES consultations (id)'. When
	if_not_exists is true, an existing table's schema is not checked or changed.

	Example: create an unrelated (standalone) table:
	    import os

	    with create_db_connection(
	        host=os.environ["DB_HOST"],
	        dbname=os.environ["DB_NAME"],
	        user=os.environ["DB_USER"],
	        password=os.environ["DB_PASSWORD"],
	    ) as connection:
	        create_table(
	            connection,
	            table_name="authentication",
	            columns={
	                "username": "TEXT PRIMARY KEY",
	                "password": "TEXT NOT NULL",
	            },
	        )

	Example: create related tables (the referenced table must exist first):
	    import os

	    with create_db_connection(
	        host=os.environ["DB_HOST"],
	        dbname=os.environ["DB_NAME"],
	        user=os.environ["DB_USER"],
	        password=os.environ["DB_PASSWORD"],
	    ) as connection:
	        create_table(
	            connection,
	            table_name="consultations",
	            columns={"id": "TEXT PRIMARY KEY"},
	        )
	        create_table(
	            connection,
	            table_name="reviews",
	            columns={
	                "id": "BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY",
	                "consultation_id": "TEXT NOT NULL",
	                "review_datetime": "TIMESTAMPTZ NOT NULL",
	                "reviewer_name": "TEXT NOT NULL",
	                "agree_or_disagree": "TEXT NOT NULL",
	                "comments": "TEXT NOT NULL",
	            },
	            constraints=(
	                "FOREIGN KEY (consultation_id) REFERENCES consultations (id)",
	            ),
	        )

	The authentication password column must contain password hashes, not plaintext.
	Each connection context commits its creations, or rolls them back if an error
	occurs. Use only definitions that match your actual schema.
	"""

	if not columns:
		raise ValueError("At least one column is required")
	definitions = [
		sql.SQL("{} {}").format(sql.Identifier(name), sql.SQL(definition))
		for name, definition in columns.items()
	]
	definitions.extend(sql.SQL(constraint) for constraint in constraints)
	query = sql.SQL("CREATE TABLE {}{} ({})").format(
		sql.SQL("IF NOT EXISTS ") if if_not_exists else sql.SQL(""),
		sql.Identifier(table_name),
		sql.SQL(", ").join(definitions),
	)
	with connection.cursor() as cursor:
		cursor.execute(query)


############################################################
# When creating the 'reviews' table, define the column as:
# id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY
############################################################
def insert_review(
	connection: Any,
	*,
	table_name: str,
	consultation_id: str,
	review_datetime: datetime,
	reviewer_name: str,
	agree_or_disagree: str,
	comments: str,
) -> int:
	"""Insert a review and return its generated ID; the caller commits it.

	The table must exist with an auto-generated integer id. Pass an aware
	review_datetime when the column uses a time zone. Use a connection context
	manager to commit or roll back.
	"""

	query = sql.SQL(
		"INSERT INTO {} (consultation_id, review_datetime, reviewer_name, "
		"agree_or_disagree, comments) VALUES (%s, %s, %s, %s, %s) RETURNING id"
	).format(sql.Identifier(table_name))
	with connection.cursor(row_factory=dict_row) as cursor:
		cursor.execute(
			query,
			(consultation_id, review_datetime, reviewer_name, agree_or_disagree, comments),
		)
		row = cursor.fetchone()
		if row is None:
			raise RuntimeError("The review insert did not return an ID")
		return row["id"]
