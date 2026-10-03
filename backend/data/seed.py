"""Write the synthetic world to the seed database.

Usage (from backend/):  .venv/Scripts/python.exe -m data.seed [--profile A] [--seed 42]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app.config import get_settings
from data.generator import generate


def sqlite_path(database_url: str) -> Path | None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


def write_seed_db(database_url: str, profile: str = "A", seed: int = 42, scale: float = 1.0) -> dict[str, int]:
    tables = generate(profile=profile, seed=seed, scale=scale)
    engine = create_engine(database_url)
    with engine.begin() as conn:
        for name, df in tables.items():
            df.to_sql(name, conn, if_exists="replace", index=False)
    engine.dispose()
    return {name: len(df) for name, df in tables.items()}


def ensure_seed_db(database_url: str, seed: int, scale: float = 1.0) -> bool:
    """Build the seed database if its SQLite file is missing. Returns True if it was built."""
    path = sqlite_path(database_url)
    if path is None or path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    write_seed_db(database_url, seed=seed, scale=scale)
    return True


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="A")
    parser.add_argument("--seed", type=int, default=settings.seed)
    parser.add_argument("--url", default=settings.database_url)
    args = parser.parse_args()
    counts = write_seed_db(args.url, profile=args.profile, seed=args.seed)
    for name, n in counts.items():
        print(f"{name:18s} {n:>8,d}")


if __name__ == "__main__":
    main()
