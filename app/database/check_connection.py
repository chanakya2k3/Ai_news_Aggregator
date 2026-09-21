"""Check that the app can reach PostgreSQL.

    uv run python -m app.database.check_connection
"""

import sys

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database.session import engine


def main() -> int:
    safe_url = settings.database_url.replace(settings.postgres_password, "***")
    print(f"Connecting to {safe_url}")

    try:
        with engine.connect() as connection:
            version = connection.execute(text("SELECT version()")).scalar_one()
            database = connection.execute(text("SELECT current_database()")).scalar_one()
    except SQLAlchemyError as exc:
        print(f"\n[FAILED] {exc.__class__.__name__}: {exc}", file=sys.stderr)
        print(
            "\nThings to check:\n"
            "  1. Docker Desktop is running\n"
            "  2. docker compose -f docker/compose.yaml up -d\n"
            "  3. .env exists (copy it from .env.example) and its values match the container",
            file=sys.stderr,
        )
        return 1

    print(f"\n[OK] connected to database '{database}'")
    print(f"     {version.split(' on ')[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
