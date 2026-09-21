"""Create every table defined in models.py.

    uv run python -m app.database.create_tables
    uv run python -m app.database.create_tables --drop    (wipes everything first)

Existing tables are left alone, so running this twice is safe. It does NOT change
tables whose columns you have edited; that needs migrations, which come later.
"""

import argparse
import sys

from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from app.database.models import Base
from app.database.session import engine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the database tables")
    parser.add_argument("--drop", action="store_true", help="drop all tables first (deletes your data)")
    args = parser.parse_args(argv)

    try:
        if args.drop:
            confirm = input("This deletes every row in the database. Type 'yes' to continue: ")
            if confirm.strip().lower() != "yes":
                print("Cancelled.")
                return 1
            Base.metadata.drop_all(engine)
            print("Dropped all tables.")

        Base.metadata.create_all(engine)
        tables = sorted(inspect(engine).get_table_names())
    except SQLAlchemyError as exc:
        print(f"[FAILED] {exc}", file=sys.stderr)
        return 1

    print(f"Tables in the database: {', '.join(tables)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
