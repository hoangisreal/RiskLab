"""Provision local logins from host-only files; never chmod secrets for a container UID."""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url


def main() -> None:
    root = Path(__file__).resolve().parents[1] / ".secrets"
    owner = make_url((root / "local_owner_url").read_text().strip())
    if owner.host != "127.0.0.1" or owner.port != 15432 or owner.database != "risklab":
        raise SystemExit("Refusing to provision anything other than the dedicated local database")
    try:
        with psycopg.connect(
            owner.set(drivername="postgresql").render_as_string(hide_password=False),
            autocommit=True,
        ) as connection:
            for kind, group in (("api", "risklab_runtime"), ("worker", "risklab_worker")):
                login = f"risklab_{kind}_login"
                password = (root / f"local_{kind}_password").read_text().strip()
                if not connection.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname=%s", (login,)
                ).fetchone():
                    connection.execute(
                        sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(login))
                    )
                connection.execute(
                    sql.SQL(
                        "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS PASSWORD {}"
                    ).format(sql.Identifier(login), sql.Literal(password))
                )
                connection.execute(
                    sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(login))
                )
    except psycopg.Error:
        raise SystemExit(
            "Local provisioning failed; check the dedicated local database and secret files"
        ) from None
    print("Local runtime/worker login roles provisioned; no credentials printed.")


if __name__ == "__main__":
    main()
