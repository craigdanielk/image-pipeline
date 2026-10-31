"""Guard: no two migration files share a version (shared schema_migrations table)."""
from collections import Counter
from pathlib import Path

MIG = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def test_no_duplicate_versions():
    versions = [p.name.split("_", 1)[0] for p in MIG.glob("*.sql")]
    dupes = [v for v, n in Counter(versions).items() if n > 1]
    assert not dupes, dupes


def test_version_ranges_doc_exists():
    assert (MIG / "VERSION_RANGES.md").exists()


def test_cs17b_sorts_after_cs16():
    names = sorted(p.name for p in MIG.glob("*.sql"))
    assert names.index("20261031240100_cs_17b_post_run_pipeline.sql") > names.index(
        "20261031220100_cs_16_execution_plans.sql")
