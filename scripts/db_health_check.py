"""Dockhive PostgreSQL/PostGIS validation runbook.

Provider checklist:
1. Confirm the hostname, port, database, username, and SSL mode in Dockhive.
2. Confirm public access or identify the required VPN, tunnel, or private link.
3. Allowlist the client environment and confirm PostgreSQL is listening.
4. Confirm the database user can connect and PostGIS is installed.
5. Rotate any database password that may have been exposed.

After the provider confirms the endpoint is reachable, run from the project root:
    python scripts/db_health_check.py

The script reads DATABASE_URL from the project's .env (or process environment),
prints only PASS/FAIL stage labels, and exits nonzero unless every check passes.
It does not run migrations or modify the database.
"""

import os
import socket
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def report(label, passed):
    print(f"{'PASS' if passed else 'FAIL'}: {label}")


def main():
    raw_url = os.getenv("DATABASE_URL")
    if not raw_url:
        report("DATABASE_URL configured", False)
        return 1
    report("DATABASE_URL configured", True)

    try:
        url = make_url(raw_url)
        if url.get_backend_name() != "postgresql" or not url.host:
            report("PostgreSQL URL parsed", False)
            return 1
        host = url.host
        port = url.port or 5432
        dsn = url.set(drivername="postgresql").update_query_dict({"sslmode": "require"})
        dsn = dsn.render_as_string(hide_password=False)
    except Exception:
        report("PostgreSQL URL parsed", False)
        return 1
    report("PostgreSQL URL parsed", True)

    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        report("DNS resolution", False)
        report("TCP connection", False)
        report("PostgreSQL query", False)
        report("PostGIS", False)
        return 1
    report("DNS resolution", True)

    try:
        with socket.create_connection((host, port), timeout=5):
            pass
    except OSError:
        report("TCP connection", False)
        report("PostgreSQL query", False)
        report("PostGIS", False)
        return 1
    report("TCP connection", True)

    try:
        connection = psycopg2.connect(dsn, connect_timeout=5)
    except Exception:
        report("PostgreSQL query", False)
        report("PostGIS", False)
        return 1

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            postgres_ok = cursor.fetchone() == (1,)
    except Exception:
        connection.close()
        report("PostgreSQL query", False)
        report("PostGIS", False)
        return 1
    report("PostgreSQL query", postgres_ok)

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT PostGIS_Version()")
            postgis_ok = bool(cursor.fetchone()[0])
    except Exception:
        postgis_ok = False
    finally:
        connection.close()

    report("PostGIS", postgis_ok)
    if not postgres_ok or not postgis_ok:
        return 1
    report("Overall", True)
    return 0


if __name__ == "__main__":
    sys.exit(main())