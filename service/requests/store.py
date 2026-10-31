"""SQL calls for CS-B2 documents (all as cs_api inside the caller's actor transaction)."""
from __future__ import annotations

import json

from . import documents


def _row(cur):
    row = cur.fetchone()
    return dict(row) if row is not None else None


def save_request(cur, workspace_id: str, doc: dict, *, if_match: str | None, key: str, digest: str) -> dict:
    cur.execute("select * from public.cs_save_creative_request(%s,%s,%s,%s,%s,%s,%s)",
                (workspace_id, json.dumps(doc), documents.canonical_for_db(doc), documents.request_semantic(doc),
                 if_match, key, digest))
    return _row(cur)


def save_treatment(cur, workspace_id: str, doc: dict, *, if_match: str | None, key: str, digest: str) -> dict:
    cur.execute("select * from public.cs_save_treatment(%s,%s,%s,%s,%s,%s,%s)",
                (workspace_id, json.dumps(doc), documents.canonical_for_db(doc), documents.treatment_semantic(doc),
                 if_match, key, digest))
    return _row(cur)


def accept_treatment(cur, workspace_id: str, doc: dict, *, if_match: str, key: str, digest: str) -> dict:
    cur.execute("select * from public.cs_accept_treatment(%s,%s,%s,%s,%s,%s,%s)",
                (workspace_id, json.dumps(doc), documents.canonical_for_db(doc), documents.treatment_semantic(doc),
                 if_match, key, digest))
    return _row(cur)


def save_draft(cur, workspace_id: str, doc: dict, expected_revision: int) -> dict:
    cur.execute("select * from public.cs_save_authoring_draft(%s,%s,%s,%s)",
                (workspace_id, json.dumps(doc), documents.canonical_for_db(doc), expected_revision))
    return _row(cur)


def get_version(cur, table: str, workspace_id: str, object_id: str, version_id: str | None = None):
    assert table in ("cs_creative_request_versions", "cs_treatment_versions")
    if version_id:
        cur.execute(f"select payload, semantic_sha256 from public.{table} where workspace_id = %s and object_id = %s "
                    "and version_id = %s", (workspace_id, object_id, version_id))
    else:
        cur.execute(f"select payload, semantic_sha256 from public.{table} where workspace_id = %s and object_id = %s "
                    "order by revision desc limit 1", (workspace_id, object_id))
    return _row(cur)


def get_package(cur, workspace_id: str, version_id: str):
    cur.execute("""select payload, source_version_id, binding_id from public.cs_source_imports
                    where workspace_id = %s and version_id = %s""", (workspace_id, version_id))
    return _row(cur)


def package_for_source(cur, workspace_id: str, binding_id: str, source_artifact_id: str):
    cur.execute("""select payload, source_version_id from public.cs_source_imports
                    where workspace_id = %s and binding_id = %s and source_kind = 'BrandKit'
                      and source_version_id = %s""", (workspace_id, binding_id, source_artifact_id))
    return _row(cur)


def eligibility(cur, workspace_id: str, version_id: str, action: str, channel: str | None = None) -> dict:
    cur.execute("select public.cs_brand_package_eligibility(%s,%s,%s,%s) as e", (workspace_id, version_id, action,
                                                                                 channel))
    return cur.fetchone()["e"]
