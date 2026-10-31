"""CS-12 (ENG-12 Part B) real PostgreSQL 17 suite: use-time kick + safety-net cadence.

All migrations in filename order on a private throwaway cluster, applied twice. pg_net, vault and pg_cron
do not exist on a stock cluster, so they are stood in for by minimal stubs with the same transaction
semantics that matter: net.http_post INSERTs a queue row (committed with the caller, discarded on
rollback), "delivery" happens only for committed rows by a separate connection.

Run: python3 -m pytest tests/creative_studio/test_cs12_real_db.py -v -s
"""
from __future__ import annotations

import io
import json
import statistics
import threading
import time
import uuid
from pathlib import Path

import psycopg2
import pytest

from service import worker as cs_worker
from service.config import Settings
from tests.creative_studio.pg_harness import MIGRATIONS_DIR, REPO_ROOT, ThrowawayCluster

MIGRATION = MIGRATIONS_DIR / "20261031190100_cs_12_use_time_kick.sql"
CRON_SCRIPT = REPO_ROOT / "scripts" / "ops" / "sql" / "cs_worker_dispatch.sql"
SECRET = "unit-test-cron-secret-not-real"
BASE = "https://cs.example.test"
EVIDENCE = Path(__file__).parent / "evidence" / "cs12_latency.md"

NET_STUB = r"""
create schema if not exists net;
create table if not exists net._http_request_queue (
  id bigserial primary key, url text, body jsonb, headers jsonb, delivered boolean not null default false,
  created_at timestamptz not null default clock_timestamp());
create table if not exists net._http_response (id bigint primary key, status_code integer, error_msg text);
create or replace function net.http_post(url text, body jsonb default '{}', params jsonb default '{}',
  headers jsonb default '{}', timeout_milliseconds integer default 5000) returns bigint language sql as $$
  insert into net._http_request_queue (url, body, headers) values (url, body, headers) returning id; $$;
"""
VAULT_STUB = r"""
create schema if not exists vault;
create table if not exists vault.decrypted_secrets (name text primary key, decrypted_secret text);
"""
CRON_STUB = r"""
create schema if not exists cron;
create table if not exists cron.job (jobid bigserial primary key, jobname text unique, schedule text, command text);
create or replace function cron.schedule(n text, s text, c text) returns bigint language sql as $$
  insert into cron.job (jobname, schedule, command) values (n, s, c) returning jobid; $$;
create or replace function cron.unschedule(j bigint) returns boolean language plpgsql as $$
  begin delete from cron.job where jobid = j; return true; end $$;
"""


@pytest.fixture(scope="module")
def pg():
    cluster = ThrowawayCluster()
    cluster.init()
    cluster.install()                     # stubs + every migration in order (first apply)
    with cluster.connect() as c:
        cur = c.cursor()
        cur.execute(NET_STUB + VAULT_STUB + CRON_STUB)
        cur.execute("insert into vault.decrypted_secrets values ('cs_worker_cron_secret', %s)", (SECRET,))
    cluster.reapply_cs_b1()               # second apply of the whole chain
    cluster.apply_file(MIGRATION)         # and the CS-12 file alone, a third time
    yield cluster
    cluster.stop()


def su(pg):
    return pg.connect()


def sql(pg, q, args=None, user=None):
    with pg.connect(user=user) as c:
        cur = c.cursor()
        cur.execute(q, args)
        return cur.fetchall() if cur.description else None


def set_setting(pg, key, value):
    sql(pg, "insert into cs_private.cs_platform_settings (key, value) values (%s, %s) "
            "on conflict (key) do update set value = excluded.value", (key, value))


@pytest.fixture(autouse=True)
def clean(pg):
    sql(pg, "truncate net._http_request_queue, net._http_response, cs_private.cs_kick_state, "
            "cs_private.cs_kick_requests, cs_private.cs_kick_failures")
    sql(pg, "alter table public.cs_work_items enable trigger cs_work_items_kick")
    sql(pg, "update public.cs_work_items set state='cancelled', lease_token=null, lease_until=null, lease_owner=null "
            "where state not in ('done','dead','cancelled')")
    for k, v in (("worker_kick_enabled", "true"), ("worker_base_url", BASE), ("worker_kick_window_seconds", "2"),
                 ("worker_sweep_grace_seconds", "20"), ("worker_safety_net_seconds", "120")):
        set_setting(pg, k, v)
    sql(pg, "insert into vault.decrypted_secrets values ('cs_worker_cron_secret', %s) "
            "on conflict (name) do update set decrypted_secret = excluded.decrypted_secret", (SECRET,))
    sql(pg, "update cs_private.cs_runtime_limits set dispatch_enabled = true where environment_id='test'")


def workspace(pg) -> str:
    uid = str(uuid.uuid4())
    sql(pg, "insert into auth.users (id, email) values (%s, %s)", (uid, f"{uid[:8]}@example-studio.test"))
    acct = sql(pg, "insert into public.cs_accounts (owner_id, name) values (%s, 'Example Home Services Co') "
                   "returning id", (uid,))[0][0]
    return str(sql(pg, "insert into public.cs_workspaces (account_id, name) values (%s, 'Example studio') "
                       "returning id", (acct,))[0][0])


def enqueue(cur, ws, stage="render_thumbnail", due="now()", tag="k"):
    """Stand-in for admission: run + node + work item (the enqueue the trigger watches)."""
    cur.execute("insert into public.cs_runs (workspace_id, environment_id, release_id) values (%s,'test','r') "
                "returning id", (ws,))
    run = cur.fetchone()[0]
    cur.execute("insert into public.cs_run_nodes (workspace_id, run_id, node_id, state) values (%s,%s,'n1','queued')",
                (ws, run))
    cur.execute(f"insert into public.cs_work_items (workspace_id, environment_id, run_id, node_id, stage, dedupe_key, "
                f"due_at) values (%s,'test',%s,'n1',%s,%s,{due}) returning id", (ws, run, stage, f"{tag}-{uuid.uuid4()}"))
    return str(cur.fetchone()[0])


def queue(pg):
    return sql(pg, "select id, url, headers, body from net._http_request_queue order by id")


def failures(pg):
    return sql(pg, "select reason, detail from cs_private.cs_kick_failures order by id")


def settings(pg) -> Settings:
    return Settings(api_database_url=pg.dsn(user="cs_api"), worker_database_url=pg.dsn(user="cs_worker"),
                    environment_id="test", release_id="test-release", jwt_issuer="x", jwt_audience="authenticated",
                    jwt_secret="x" * 40, jwks_url=None, storage_url="http://127.0.0.1:9", storage_anon_key="k",
                    signed_download_seconds=300, download_grant_seconds=60, worker_secret=SECRET,
                    runtime_principal="cs-worker:test")


def handler_factory(log):
    def handler(item, ctx):
        log.append((str(item["work_item_id"]), time.time()))
        return cs_worker.StageResult("thumbnail", {"ok": True})
    return handler


def post_worker(app, token=SECRET):
    environ = {"REQUEST_METHOD": "POST", "PATH_INFO": "/api/internal/worker", "wsgi.input": io.BytesIO(b""),
               "HTTP_AUTHORIZATION": f"Bearer {token}"}
    cap = {}
    body = b"".join(app(environ, lambda s, h: cap.update(status=int(s.split()[0]))))
    return cap["status"], json.loads(body)


# ----------------------------------------------------------------------------------------------
def test_01_migrations_applied_in_order_and_rerun_is_non_destructive(pg):
    names = [p.name for p in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    assert names.index(MIGRATION.name) == names.index("20261031190100_cs_12_use_time_kick.sql")
    assert names[names.index(MIGRATION.name):][0] == MIGRATION.name
    set_setting(pg, "worker_kick_window_seconds", "7")
    pg.apply_file(MIGRATION)
    assert sql(pg, "select value from cs_private.cs_platform_settings where key='worker_kick_window_seconds'") == [("7",)]
    assert sql(pg, "select count(*) from pg_trigger where tgname='cs_work_items_kick'") == [(1,)]


def test_02_no_grants_to_untrusted_roles(pg):
    for role in ("anon", "authenticated", "cs_api", "cs_worker"):
        for tbl in ("cs_platform_settings", "cs_kick_state", "cs_kick_requests", "cs_kick_failures"):
            assert sql(pg, "select has_table_privilege(%s, %s, 'select,insert,update,delete')",
                       (role, f"cs_private.{tbl}")) == [(False,)], (role, tbl)
        for fn in ("cs_kick_worker(text,uuid,text)", "cs_safety_net_sweep()", "cs_setting(text,text)",
                   "cs_record_kick_failure(text,uuid,text,text)"):
            assert sql(pg, "select has_function_privilege(%s, %s, 'execute')",
                       (role, f"cs_private.{fn}")) == [(False,)], (role, fn)
    with pytest.raises(psycopg2.Error):
        sql(pg, "select * from cs_private.cs_kick_failures", user="cs_worker")


def test_03_off_by_default_until_operator_enables(pg):
    set_setting(pg, "worker_kick_enabled", "false")
    ws = workspace(pg)
    with su(pg) as c:
        enqueue(c.cursor(), ws)
    assert queue(pg) == [] and failures(pg) == []


def test_04_kick_fires_on_enqueue_with_secret_and_configured_url_only_after_commit(pg):
    ws = workspace(pg)
    conn = psycopg2.connect(pg.dsn())          # explicit transaction
    cur = conn.cursor()
    enqueue(cur, ws)
    assert queue(pg) == [], "request is not visible (not sent) before commit"
    conn.commit()
    (rid, url, headers, body), = queue(pg)
    assert url == BASE + "/api/internal/worker"
    assert headers["Authorization"] == f"Bearer {SECRET}"
    assert body["source"] == "db-kick"
    conn.close()
    # rollback discards the kick with the enqueue
    sql(pg, "truncate net._http_request_queue, cs_private.cs_kick_state")
    conn = psycopg2.connect(pg.dsn())
    enqueue(conn.cursor(), ws)
    conn.rollback()
    conn.close()
    assert queue(pg) == [] and sql(pg, "select count(*) from cs_private.cs_kick_state") == [(0,)]


def test_05_runs_as_restricted_worker_role_too(pg):
    ws = workspace(pg)
    # runtime roles hold no table grants; a SECURITY DEFINER admission stand-in is what they can call
    sql(pg, "create or replace function public.cs12_test_admit(p_ws uuid) returns uuid language plpgsql security definer "
            "set search_path = public, pg_temp as $$ declare r uuid; begin "
            "insert into cs_runs (workspace_id, environment_id, release_id) values (p_ws,'test','r') returning id into r; "
            "insert into cs_run_nodes (workspace_id, run_id, node_id, state) values (p_ws, r, 'n1', 'queued'); "
            "insert into cs_work_items (workspace_id, environment_id, run_id, node_id, stage, dedupe_key) "
            "values (p_ws,'test',r,'n1','render_thumbnail', gen_random_uuid()::text); return r; end $$; "
            "revoke all on function public.cs12_test_admit(uuid) from public; "
            "grant execute on function public.cs12_test_admit(uuid) to cs_worker")
    assert sql(pg, "select public.cs12_test_admit(%s)", (ws,), user="cs_worker")
    assert len(queue(pg)) == 1 and failures(pg) == []


def test_06_burst_is_coalesced_per_workspace_per_window(pg):
    wa, wb = workspace(pg), workspace(pg)
    conn = psycopg2.connect(pg.dsn())              # one explicit transaction, 20 enqueues
    cur = conn.cursor()
    for _ in range(20):
        enqueue(cur, wa)
    conn.commit(); conn.close()
    # a second burst from other sessions inside the window
    for _ in range(5):
        with su(pg) as c:
            enqueue(c.cursor(), wa)
    assert len(queue(pg)) == 1, "25 enqueues, one workspace, one window -> one kick"
    with su(pg) as c:
        enqueue(c.cursor(), wb)
    assert len(queue(pg)) == 2, "another workspace is not suppressed"
    kicks, suppressed = sql(pg, "select kicks, suppressed from cs_private.cs_kick_state where scope = %s",
                            (f"ws:{wa}",))[0]
    assert (kicks, suppressed) == (1, 24)
    set_setting(pg, "worker_kick_window_seconds", "1")
    time.sleep(1.1)
    with su(pg) as c:
        enqueue(c.cursor(), wa)
    assert len(queue(pg)) == 3, "window elapsed -> next enqueue kicks again"


def test_07_concurrent_enqueuers_produce_one_kick(pg):
    ws = workspace(pg)
    barrier = threading.Barrier(8)

    def go():
        conn = psycopg2.connect(pg.dsn())
        cur = conn.cursor()
        enqueue(cur, ws)
        barrier.wait()
        conn.commit()
        conn.close()
    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(queue(pg)) == 1
    assert failures(pg) == []


@pytest.mark.parametrize("drill", ["pg_net_absent", "vault_secret_missing", "base_url_unset", "http_post_raises"])
def test_08_enqueue_never_fails_when_kick_cannot_be_sent(pg, drill):
    ws = workspace(pg)
    restore = None
    if drill == "pg_net_absent":
        sql(pg, "alter schema net rename to net_away"); restore = "alter schema net_away rename to net"
    elif drill == "vault_secret_missing":
        sql(pg, "delete from vault.decrypted_secrets")
    elif drill == "base_url_unset":
        sql(pg, "delete from cs_private.cs_platform_settings where key='worker_base_url'")
    else:
        sql(pg, "create or replace function net.http_post(url text, body jsonb default '{}', params jsonb default '{}', "
                "headers jsonb default '{}', timeout_milliseconds integer default 5000) returns bigint "
                "language plpgsql as $$ begin raise exception 'boom: connection refused'; end $$")
        restore = ("create or replace function net.http_post(url text, body jsonb default '{}', params jsonb default '{}', "
                   "headers jsonb default '{}', timeout_milliseconds integer default 5000) returns bigint language sql as "
                   "$$ insert into net._http_request_queue (url, body, headers) values (url, body, headers) returning id; $$")
    try:
        t0 = time.time()
        with su(pg) as c:
            wi = enqueue(c.cursor(), ws)             # must succeed
        elapsed = time.time() - t0
        assert sql(pg, "select state::text from public.cs_work_items where id=%s", (wi,)) == [("queued",)]
        assert elapsed < 2.0
        (reason, detail), = failures(pg)
        assert reason.startswith("kick_error:") and detail
        assert sql(pg, "select count(*) from cs_private.cs_kick_state") == [(0,)], "failed kick leaves no coalescing stamp"
    finally:
        if restore:
            sql(pg, restore)


def test_09_drain_endpoint_down_run_stays_queued_failure_recorded_then_picked_up(pg):
    ws = workspace(pg)
    with su(pg) as c:
        wi = enqueue(c.cursor(), ws)
    rid = queue(pg)[0][0]
    sql(pg, "update net._http_request_queue set delivered = true where id=%s", (rid,))
    sql(pg, "insert into net._http_response values (%s, 503, null)", (rid,))   # endpoint answered 503
    sql(pg, "update cs_private.cs_kick_requests set sent_at = now() - interval '1 minute'")
    assert sql(pg, "select state::text from public.cs_work_items where id=%s", (wi,)) == [("queued",)]
    res = sql(pg, "select cs_private.cs_safety_net_sweep()")[0][0]
    assert res["http_failures_recorded"] == 1
    assert failures(pg)[0][0] == "kick_http_503"
    # endpoint back: the sweep (grace elapsed) re-kicks and a drain picks the run up
    set_setting(pg, "worker_sweep_grace_seconds", "0"); set_setting(pg, "worker_kick_window_seconds", "0")
    sql(pg, "update public.cs_work_items set due_at = now() - interval '1 minute' where id=%s", (wi,))
    res = sql(pg, "select cs_private.cs_safety_net_sweep()")[0][0]
    assert res["kicked"] == {"test": "sent"}
    log = []
    out = cs_worker.run_once(settings(pg), {"render_thumbnail": handler_factory(log)})
    assert out["status"] == "finished" and log[0][0] == wi


def test_10_kick_lost_cron_only_path_picks_it_up(pg):
    ws = workspace(pg)
    with su(pg) as c:
        wi = enqueue(c.cursor(), ws)
    sql(pg, "truncate net._http_request_queue, cs_private.cs_kick_state")        # the kick vanished
    assert sql(pg, "select cs_private.cs_safety_net_sweep()")[0][0]["kicked"] == {}, "inside grace: no premature re-kick"
    sql(pg, "update public.cs_work_items set due_at = now() - interval '30 seconds' where id=%s", (wi,))
    assert sql(pg, "select cs_private.cs_safety_net_sweep()")[0][0]["kicked"] == {"test": "sent"}
    assert len(queue(pg)) == 1


def test_11_sweep_reclaims_expired_local_lease_but_never_a_paid_stage(pg):
    ws = workspace(pg)
    set_setting(pg, "worker_sweep_grace_seconds", "0"); set_setting(pg, "worker_kick_window_seconds", "0")
    with su(pg) as c:
        cur = c.cursor()
        wi = enqueue(cur, ws, stage="render_thumbnail", due="now() + interval '1 day'")
        cur.execute("update public.cs_work_items set state='leased', lease_owner='w', lease_token=gen_random_uuid(), "
                    "lease_until = now() - interval '1 minute', attempt_count=1 where id=%s", (wi,))
    assert sql(pg, "select cs_private.cs_safety_net_sweep()")[0][0]["kicked"] == {"test": "sent"}
    sql(pg, "update public.cs_work_items set state='cancelled', lease_token=null, lease_until=null, lease_owner=null")
    sql(pg, "truncate net._http_request_queue")
    with su(pg) as c:
        cur = c.cursor()
        wi = enqueue(cur, ws, stage="provider_submit", due="now() + interval '1 day'")
        cur.execute("update public.cs_work_items set state='leased', lease_owner='w', lease_token=gen_random_uuid(), "
                    "lease_until = now() - interval '1 minute', attempt_count=1 where id=%s", (wi,))
    assert sql(pg, "select cs_private.cs_safety_net_sweep()")[0][0]["kicked"] == {}, "paid stage is never auto-reclaimed"


def test_12_cadence_config_is_honoured_by_the_cron_script(pg):
    set_setting(pg, "worker_safety_net_seconds", "120")
    pg.apply_file(CRON_SCRIPT)
    assert sql(pg, "select jobname, schedule, command from cron.job") == [
        ("cs-worker-drain", "*/2 * * * *", "select cs_private.cs_safety_net_sweep()")]
    set_setting(pg, "worker_safety_net_seconds", "300")
    pg.apply_file(CRON_SCRIPT)                    # re-run replaces, never duplicates
    assert sql(pg, "select schedule from cron.job where jobname='cs-worker-drain'") == [("*/5 * * * *",)]
    assert sql(pg, "select count(*) from cron.job") == [(1,)]
    for key, bad in (("worker_safety_net_seconds", "5"), ("worker_base_url", "http://insecure.example.test"),
                     ("worker_kick_enabled", "yes"), ("unknown_key", "1")):
        with pytest.raises(psycopg2.errors.CheckViolation):
            set_setting(pg, key, bad)
    set_setting(pg, "worker_safety_net_seconds", "120")
    sql(pg, "delete from cron.job")


def test_13_concurrent_kicks_execute_each_item_exactly_once(pg):
    ws = workspace(pg)
    ids = []
    with su(pg) as c:
        for _ in range(6):
            ids.append(enqueue(c.cursor(), ws))
    log, results = [], []
    app = cs_worker.make_wsgi(lambda: settings(pg), {"render_thumbnail": handler_factory(log)})
    barrier = threading.Barrier(16)

    def kick():
        barrier.wait()
        results.append(post_worker(app))
    ts = [threading.Thread(target=kick) for _ in range(16)]       # 16 duplicate kicks vs 6 items
    [t.start() for t in ts]; [t.join() for t in ts]
    assert all(s == 200 for s, _ in results), results
    assert sorted(i for i, _ in log) == sorted(ids), "every item ran, none twice"
    assert sum(1 for _, r in results if r["status"] == "finished") == 6
    assert sum(1 for _, r in results if r["status"] == "idle") == 10
    assert sql(pg, "select count(*) from public.cs_stage_results where work_item_id = any(%s::uuid[])", (ids,)) == [(6,)]
    assert sql(pg, "select count(*) from public.cs_work_items where id = any(%s::uuid[]) and state='done'", (ids,)) == [(6,)]
    assert post_worker(app, token="wrong")[0] == 401


def test_14_replayed_dedupe_key_creates_no_second_item_and_no_second_kick(pg):
    ws = workspace(pg)
    with su(pg) as c:
        cur = c.cursor()
        wi = enqueue(cur, ws, tag="replay")
        cur.execute("select run_id from public.cs_work_items where id=%s", (wi,))
        run = cur.fetchone()[0]
        cur.execute("select dedupe_key from public.cs_work_items where id=%s", (wi,))
        key = cur.fetchone()[0]
    sql(pg, "update cs_private.cs_kick_state set last_kick_at = now() - interval '1 hour'")
    with pytest.raises(psycopg2.errors.UniqueViolation):
        sql(pg, "insert into public.cs_work_items (workspace_id, environment_id, run_id, node_id, stage, dedupe_key) "
                "values (%s,'test',%s,'n1','render_thumbnail',%s)", (ws, run, key))
    assert len(queue(pg)) == 1


# ----------------------------------------------------------------------------------------------
def _measure(pg, mode, n, cadence):
    """enqueue -> handler-start latency. mode 'kick': trigger on, delivery thread only.
    mode 'cron': trigger disabled, sweeper ticking every `cadence` s (scaled-down stand-in for the 120 s job)."""
    ws = workspace(pg)
    sql(pg, "truncate net._http_request_queue, cs_private.cs_kick_state")
    set_setting(pg, "worker_kick_window_seconds", "0"); set_setting(pg, "worker_sweep_grace_seconds", "0")
    sql(pg, "alter table public.cs_work_items %s trigger cs_work_items_kick" % ("enable" if mode == "kick" else "disable"))
    log, starts = [], {}
    app = cs_worker.make_wsgi(lambda: settings(pg), {"render_thumbnail": handler_factory(log)})
    stop = threading.Event()

    def deliver():                                   # stand-in for pg_net delivering committed requests
        conn = psycopg2.connect(pg.dsn()); conn.autocommit = True; cur = conn.cursor()
        while not stop.is_set():
            cur.execute("update net._http_request_queue set delivered=true where id = (select id from "
                        "net._http_request_queue where not delivered order by id for update skip locked limit 1) "
                        "returning id")
            if cur.fetchone():
                post_worker(app)
            else:
                time.sleep(0.005)
        conn.close()

    def sweeper():                                   # stand-in for pg_cron
        conn = psycopg2.connect(pg.dsn()); conn.autocommit = True; cur = conn.cursor()
        nxt = time.time() + cadence
        while not stop.is_set():
            if time.time() >= nxt:
                cur.execute("select cs_private.cs_safety_net_sweep()"); nxt += cadence
            time.sleep(0.01)
        conn.close()
    threads = [threading.Thread(target=deliver)] + ([threading.Thread(target=sweeper)] if mode == "cron" else [])
    [t.start() for t in threads]
    conn = psycopg2.connect(pg.dsn()); conn.autocommit = True; cur = conn.cursor()
    import random
    rnd = random.Random(12)
    for _ in range(n):
        time.sleep(rnd.uniform(0.05, cadence if mode == "cron" else 0.3))
        t0 = time.time()
        starts[enqueue(cur, ws)] = t0
        deadline = time.time() + 15
        while len(log) < len(starts) and time.time() < deadline:
            time.sleep(0.005)
    stop.set(); [t.join() for t in threads]; conn.close()
    sql(pg, "alter table public.cs_work_items enable trigger cs_work_items_kick")
    lat = sorted(t - starts[i] for i, t in log)
    assert len(lat) == n
    return lat


def pct(xs, p):
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def test_15_latency_kick_on_vs_cron_only(pg):
    n_kick, n_cron, cadence = 60, 25, 2.0
    kick = _measure(pg, "kick", n_kick, cadence)
    cron = _measure(pg, "cron", n_cron, cadence)
    rows = [("kick on (trigger -> http_post -> worker)", n_kick, kick),
            (f"cron-only, sweeper every {cadence:g}s (scaled)", n_cron, cron)]
    lines = ["# CS-12 enqueue -> work-started latency (real PG17, stubbed pg_net delivery)", "",
             "| path | n | p50 | p95 | max |", "|---|---|---|---|---|"]
    for name, n, xs in rows:
        lines.append(f"| {name} | {n} | {pct(xs, .5)*1000:.0f} ms | {pct(xs, .95)*1000:.0f} ms | {xs[-1]*1000:.0f} ms |")
    lines += ["", "Cron-only at the production safety-net cadence (120 s, uniform arrival phase): "
              "expected p50 = 60 s, p95 = 114 s, worst case 120 s + grace (analytic, not measured).",
              "Kick path excludes real network/cold-start time of the HTTP hop (stubbed delivery, local DB)."]
    EVIDENCE.write_text("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))
    assert pct(kick, .95) < 1.0
    assert statistics.median(cron) > 5 * statistics.median(kick)
