"""CS-INT2 real PostgreSQL suite: cs_register_runtime_principal (definer, service-only) and the spend-switch gate.
The kick -> free work runs -> paid stage refused flow is test_b3_real_db.py::test_03b_* (needs B3's admission fixtures)."""
from __future__ import annotations

import json
import uuid

import psycopg2
import pytest

from tests.creative_studio.pg_harness import MIGRATIONS_DIR, ThrowawayCluster

REGISTER = MIGRATIONS_DIR / "20261031190200_cs_int2_register_runtime_principal.sql"
GATE = MIGRATIONS_DIR / "20261031190300_cs_int2_paid_provider_gate.sql"
FN = "select cs_private.cs_register_runtime_principal(%s, %s)"


@pytest.fixture(scope="module")
def pg():
    cluster = ThrowawayCluster()
    cluster.init()
    cluster.install()
    cluster.reapply_cs_b1()          # whole chain a second time
    cluster.apply_file(REGISTER)
    cluster.apply_file(GATE)
    yield cluster
    cluster.stop()


def run(pg, q, args=None, user=None):
    with pg.connect(user=user) as c:
        cur = c.cursor()
        cur.execute(q, args)
        return cur.fetchall() if cur.description else None


def new_user(pg):
    uid = str(uuid.uuid4())
    run(pg, "insert into auth.users (id, email) values (%s, %s)", (uid, f"{uid[:8]}@example-studio.test"))
    return uid


def test_01_owner_registers_idempotently_and_reactivates(pg):
    uid = new_user(pg)
    run(pg, FN, (uid, "test"))
    run(pg, FN, (uid, "test"))
    assert run(pg, "select count(*), min(status) from cs_private.cs_runtime_principals where user_id=%s", (uid,)) == [(1, "active")]
    run(pg, "update cs_private.cs_runtime_principals set status='revoked' where user_id=%s", (uid,))
    run(pg, FN, (uid, "test"))
    assert run(pg, "select status from cs_private.cs_runtime_principals where user_id=%s", (uid,)) == [("active",)]
    assert run(pg, "select cs_private.cs_storage_allowed('creative-studio', %s, 'select')" , (f"test/x/{uuid.uuid4()}.bin",)) is not None


@pytest.mark.parametrize("who", ["cs_api", "cs_worker", "authenticated", "anon", "service_role", "public_via_stranger"])
def test_03_refused_for_non_service_callers(pg, who):
    uid = new_user(pg)
    with pg.connect() as c:
        cur = c.cursor()
        if who in ("cs_api", "cs_worker"):
            c2 = psycopg2.connect(pg.dsn(user=who)); cur = c2.cursor()
        elif who == "public_via_stranger":
            cur.execute("set role authenticated")
            cur.execute("select set_config('request.jwt.claims', %s, false)", (json.dumps({"sub": uid, "role": "authenticated"}),))
        else:
            cur.execute(f"set role {who}")
        with pytest.raises(psycopg2.Error) as err:
            cur.execute(FN, (uid, "test"))
        assert err.value.pgcode == "42501"
    assert run(pg, "select count(*) from cs_private.cs_runtime_principals where user_id=%s", (uid,)) == [(0,)]


def test_04_no_untrusted_grants_and_fixed_search_path(pg):
    rows = run(pg, """select p.prosecdef, p.proconfig::text, has_function_privilege('anon', p.oid, 'execute'),
                      has_function_privilege('authenticated', p.oid, 'execute'),
                      has_function_privilege('cs_api', p.oid, 'execute'), has_function_privilege('cs_worker', p.oid, 'execute'),
                      has_function_privilege('public', p.oid, 'execute')
                 from pg_proc p where p.oid = 'cs_private.cs_register_runtime_principal(uuid,text)'::regprocedure""")
    secdef, config, *grants = rows[0]
    assert secdef and "search_path=public, pg_temp" in config and grants == [False] * 5


def test_05_unknown_user_or_environment_refused(pg):
    with pytest.raises(psycopg2.Error) as err:
        run(pg, FN, (str(uuid.uuid4()), "test"))
    assert err.value.pgcode == "CS422"
    with pytest.raises(psycopg2.Error) as err:
        run(pg, FN, (new_user(pg), "no-such-env"))
    assert err.value.pgcode == "CS503"


def test_06_gate_migrations_idempotent_and_policy_count_unchanged(pg):
    pol = run(pg, "select count(*) from pg_policies where schemaname in ('public','cs_private')")
    trg = run(pg, "select count(*) from pg_trigger where tgname = 'cs_attempts_paid_gate'")
    cluster_files = [p for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.name >= "20261030210000"]
    assert [p.name for p in cluster_files][-3:-1] == [REGISTER.name, GATE.name]
    pg.reapply_cs_b1()
    assert run(pg, "select count(*) from pg_policies where schemaname in ('public','cs_private')") == pol
    assert run(pg, "select count(*) from pg_trigger where tgname = 'cs_attempts_paid_gate'") == trg == [(1,)]


def test_07_production_seed_has_one_spend_switch_off(pg):
    sql = (MIGRATIONS_DIR.parent / "provisioning" / "02_storage_bucket.sql").read_text()
    assert "true,\n      true,\n      false" in sql          # admission, dispatch true; paid false
