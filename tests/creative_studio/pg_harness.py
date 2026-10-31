"""Throwaway PostgreSQL 17 cluster for CS-B1 real-database tests.

A private ``initdb`` cluster (temp dir, free TCP port, trust auth on loopback)
is used instead of a database on a shared server because:
  * cs_api / cs_worker are cluster-global roles; a private cluster leaves no
    roles behind on the developer's server;
  * the restart acceptance test must hard-kill and restart the database
    process itself.

Supabase is stubbed only where the migration depends on it: roles
anon/authenticated/service_role, auth.users + auth.uid(), storage.buckets +
storage.objects (RLS enabled, Supabase-equivalent table grants). Every file in
supabase/migrations is applied in filename order.
"""
from __future__ import annotations

import getpass
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
CS_B1_MIGRATION = MIGRATIONS_DIR / "20261030210000_cs_b1_platform_foundation.sql"

SUPABASE_STUBS = r"""
do $$ begin
  if to_regrole('anon') is null then create role anon nologin noinherit; end if;
  if to_regrole('authenticated') is null then create role authenticated nologin noinherit; end if;
  if to_regrole('service_role') is null then create role service_role nologin noinherit bypassrls; end if;
end $$;

create extension if not exists pgcrypto;

create schema if not exists auth;
create table if not exists auth.users (id uuid primary key default gen_random_uuid(), email text);
-- Supabase semantics: claims come from the transaction-local request.jwt.claims.
create or replace function auth.uid() returns uuid language sql stable as $$
  select nullif(coalesce(nullif(current_setting('request.jwt.claim.sub', true), ''),
                         (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')), '')::uuid;
$$;
create or replace function auth.role() returns text language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claim.role', true), ''),
                  (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role'));
$$;
grant usage on schema auth to anon, authenticated, service_role;
grant execute on function auth.uid(), auth.role() to anon, authenticated, service_role;

create schema if not exists storage;
create table if not exists storage.buckets (
  id text primary key, name text not null unique, owner uuid, public boolean default false,
  file_size_limit bigint, allowed_mime_types text[], created_at timestamptz default now());
create table if not exists storage.objects (
  id uuid primary key default gen_random_uuid(),
  bucket_id text references storage.buckets(id), name text, owner uuid,
  metadata jsonb, created_at timestamptz default now(), unique (bucket_id, name));
alter table storage.objects enable row level security;
alter table storage.buckets enable row level security;
grant usage on schema storage to anon, authenticated, service_role;
grant select, insert, update, delete on storage.objects to anon, authenticated, service_role;
grant select on storage.buckets to anon, authenticated, service_role;
"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _pg_bin(name: str) -> str:
    for candidate in (os.environ.get("PG_BIN"), "/opt/homebrew/opt/postgresql@17/bin", "/usr/lib/postgresql/17/bin"):
        if candidate and Path(candidate, name).exists():
            return str(Path(candidate, name))
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"PostgreSQL 17 binary {name!r} not found; set PG_BIN")
    return found


class ThrowawayCluster:
    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cs_b1_pg_"))
        self.data = self.tmp / "data"
        self.port = _free_port()
        self.superuser = getpass.getuser()
        self.dbname = "cs_b1"
        self._started = False

    # -- lifecycle -----------------------------------------------------------
    def init(self) -> None:
        subprocess.run([_pg_bin("initdb"), "-D", str(self.data), "-U", self.superuser, "--auth=trust",
                        "--encoding=UTF8", "--no-instructions"], check=True, capture_output=True)
        version = subprocess.run([_pg_bin("postgres"), "--version"], check=True, capture_output=True, text=True).stdout
        if " 17." not in version:
            raise RuntimeError(f"PostgreSQL 17 required, found {version.strip()}")
        self.start()
        with self.connect("postgres") as conn:
            conn.cursor().execute(f"create database {self.dbname}")

    def start(self) -> None:
        subprocess.run([_pg_bin("pg_ctl"), "-D", str(self.data), "-l", str(self.tmp / "pg.log"), "-w", "-o",
                        f"-p {self.port} -k {self.tmp} -c listen_addresses=127.0.0.1 -c fsync=on",
                        "start"], check=True, capture_output=True)
        self._started = True

    def postmaster_pid(self) -> int:
        return int((self.data / "postmaster.pid").read_text().splitlines()[0])

    def crash(self) -> None:
        """SIGKILL the postmaster (no shutdown checkpoint), then reap children."""
        pid = self.postmaster_pid()
        os.kill(pid, signal.SIGKILL)
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        # orphaned backends exit once they notice the postmaster is gone
        deadline = time.time() + 15
        while time.time() < deadline:
            out = subprocess.run(["pgrep", "-f", f"postgres.*{self.data}"], capture_output=True, text=True).stdout
            if not out.strip():
                break
            time.sleep(0.2)
        pid_file = self.data / "postmaster.pid"
        if pid_file.exists():
            pid_file.unlink()   # stale lock left by SIGKILL; recovery replays WAL on start
        self._started = False

    def stop(self) -> None:
        if self._started:
            subprocess.run([_pg_bin("pg_ctl"), "-D", str(self.data), "-m", "immediate", "stop"],
                           check=False, capture_output=True)
            self._started = False
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- connections ---------------------------------------------------------
    def dsn(self, user: str | None = None, dbname: str | None = None) -> str:
        return (f"host=127.0.0.1 port={self.port} dbname={dbname or self.dbname} "
                f"user={user or self.superuser}")

    @contextmanager
    def connect(self, dbname: str | None = None, user: str | None = None, autocommit: bool = True):
        conn = psycopg2.connect(self.dsn(user=user, dbname=dbname))
        if autocommit:
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            yield conn
        finally:
            conn.close()

    # -- schema --------------------------------------------------------------
    def install(self) -> list[str]:
        applied = []
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(SUPABASE_STUBS)
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                cur.execute(path.read_text(encoding="utf-8"))
                applied.append(path.name)
            # CS-B5 provisions runtime LOGIN credentials; the test stands in for that step.
            cur.execute("alter role cs_api login; alter role cs_worker login")
            cur.execute("insert into cs_private.cs_runtime_limits (environment_id, dispatch_enabled) "
                        "values ('test', true) on conflict do nothing")
        return applied

    def reapply_cs_b1(self) -> list[str]:
        """Re-apply the CS-B1 migration and then every later migration in filename order.

        This is the supported re-run unit: B1 is the foundation and later files build on
        (and redefine some of) its objects, so the chain is replayed, never B1 alone.
        Returns the filenames re-applied, in order.
        """
        chain = [p for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.name >= CS_B1_MIGRATION.name]
        with self.connect() as conn:
            cur = conn.cursor()
            for path in chain:
                cur.execute(path.read_text(encoding="utf-8"))
        return [p.name for p in chain]

    def apply_file(self, path: Path) -> None:
        """Apply exactly one migration file (used to prove a standalone re-run is non-destructive)."""
        with self.connect() as conn:
            conn.cursor().execute(Path(path).read_text(encoding="utf-8"))


def cs_migrations() -> list[str]:
    """Creative Studio migrations (B1 onwards) in the order they apply."""
    return [p.name for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.name >= CS_B1_MIGRATION.name]


def claims_json(user_id: str, role: str = "authenticated") -> str:
    return json.dumps({"sub": user_id, "role": role, "aud": "authenticated"})
