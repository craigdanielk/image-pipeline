"""Database sessions for the restricted runtime logins.

Each API request is one transaction on the cs_api login with the verified
actor's claims set transaction-locally (set_config(..., true)); RLS and the
cs_* procedures read them. The worker uses the cs_worker login with no actor.
No service-role key or BYPASSRLS connection exists in the hosted runtime.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

from .auth import Actor
from .errors import ServiceError, from_db_error


def _connect(dsn: str):
    try:
        return psycopg2.connect(dsn, application_name="creative-studio", connect_timeout=10)
    except psycopg2.OperationalError:
        raise ServiceError(503, "database_unavailable") from None


@contextmanager
def actor_transaction(dsn: str, actor: Actor, mos_grant: str | None = None):
    conn = _connect(dsn)
    try:
        with conn:   # commit on success, rollback on exception
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(actor.db_claims()),))
            if mos_grant:
                # CS-21: set only after a verified Marketing OS assertion for this user on a bound workspace.
                cur.execute("select set_config('cs.mos_grant', %s, true)", (mos_grant,))
            try:
                yield cur
            except psycopg2.Error as exc:
                raise from_db_error(exc) from None
    finally:
        conn.close()


@contextmanager
def worker_transaction(dsn: str):
    conn = _connect(dsn)
    try:
        with conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                yield cur
            except psycopg2.Error as exc:
                raise from_db_error(exc) from None
    finally:
        conn.close()
