"""
db.py
-----
Database layer: Postgres when DATABASE_URL is set, parquet files otherwise.

Pipeline modules call:
    db.read_cache(table, parquet_path, fallback_cols)
    db.write_cache(table, parquet_path, df)

Production (Railway): set DATABASE_URL → all reads and writes go to Postgres.
  Run the pipeline once with DATABASE_URL pointing at Railway's Postgres and the
  data populates there directly — no migration step needed.

Local dev: leave DATABASE_URL unset → parquet files are used as before.
"""

import os
import pandas as pd
import sqlalchemy
from sqlalchemy import inspect, text

_ENGINE = None  # module-level singleton


def _engine() -> sqlalchemy.Engine | None:
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return None
    # Railway issues postgres:// URIs; SQLAlchemy 2.x requires postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    try:
        engine = sqlalchemy.create_engine(url, pool_pre_ping=True)
        _init_schema(engine)
        _ENGINE = engine
        return _ENGINE
    except Exception as e:
        print(f"[db] Could not connect to Postgres: {e}")
        return None


def _init_schema(engine: sqlalchemy.Engine) -> None:
    """Run schema.sql once per process startup — idempotent (IF NOT EXISTS)."""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    if not os.path.exists(schema_path):
        return
    with open(schema_path) as f:
        sql = f.read()
    with engine.begin() as conn:
        conn.execute(text(sql))


def _pg_type(dtype) -> str:
    """Map a pandas dtype to a Postgres column type for ALTER TABLE."""
    if pd.api.types.is_bool_dtype(dtype):
        return "BOOLEAN"
    if pd.api.types.is_integer_dtype(dtype):
        return "BIGINT"
    if pd.api.types.is_float_dtype(dtype):
        return "DOUBLE PRECISION"
    return "TEXT"


def engine():
    """Public accessor for the singleton engine (None if no DATABASE_URL)."""
    return _engine()


def read_cache(
    table: str,
    parquet_path: str,
    fallback_cols: list,
    geoids: list[str] | None = None,
) -> pd.DataFrame:
    """
    Read a cache table. If geoids is provided, only fetch those rows (keyed
    lookup via WHERE geoid = ANY(:ids)) instead of reading the whole table.
    """
    eng = _engine()
    if eng is not None:
        try:
            if inspect(eng).has_table(table):
                with eng.connect() as conn:
                    if geoids is not None:
                        result = conn.execute(
                            text(f'SELECT * FROM "{table}" WHERE geoid = ANY(:ids)'),
                            {"ids": geoids},
                        )
                    else:
                        result = conn.execute(text(f'SELECT * FROM "{table}"'))
                    rows = result.fetchall()
                    return pd.DataFrame(rows, columns=list(result.keys()))
        except Exception as e:
            print(f"[db] read_cache({table}) failed: {e}")
        return pd.DataFrame(columns=fallback_cols)
    if os.path.exists(parquet_path):
        df = pd.read_parquet(parquet_path)
        if geoids is not None:
            df = df[df["geoid"].isin(geoids)]
        return df
    return pd.DataFrame(columns=fallback_cols)


def write_cache(table: str, parquet_path: str, df: pd.DataFrame) -> None:
    engine = _engine()
    if engine is not None:
        try:
            insp = inspect(engine)
            if insp.has_table(table):
                # Add any new columns the DataFrame has that the table doesn't yet
                existing = {c["name"] for c in insp.get_columns(table)}
                with engine.begin() as conn:
                    for col in df.columns:
                        if col not in existing:
                            pg_t = _pg_type(df[col].dtype)
                            conn.execute(text(
                                f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{col}" {pg_t}'
                            ))
                    # Upsert: delete the rows we're about to write, then insert
                    if "geoid" in df.columns:
                        conn.execute(
                            text(f'DELETE FROM "{table}" WHERE geoid = ANY(:ids)'),
                            {"ids": df["geoid"].tolist()},
                        )
                    df.to_sql(table, conn, if_exists="append", index=False)
            else:
                # First write: let pandas create the table, then add a geoid index
                df.to_sql(table, engine, if_exists="replace", index=False)
                if "geoid" in df.columns:
                    with engine.begin() as conn:
                        conn.execute(text(
                            f'CREATE INDEX IF NOT EXISTS "{table}_geoid_idx" ON "{table}" (geoid)'
                        ))
        except Exception as e:
            print(f"[db] write_cache({table}) failed: {e}")
            raise
    else:
        os.makedirs(os.path.dirname(parquet_path), exist_ok=True)
        df.to_parquet(parquet_path, index=False)
