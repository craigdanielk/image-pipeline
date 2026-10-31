"""CS-INT: the integrated Creative Studio schema on a throwaway PostgreSQL 17.

Proves, over ALL Creative Studio migrations in filename order:
  * the full CS chain applied a second time changes nothing in the catalogue
    (policies with their expressions, function bodies + ACLs, table grants,
    constraints, columns, RLS flags, roles) and no rows;
  * re-running the CS-B1 file on its own (F1) never drops a later migration's
    policy, never changes a later migration's function privileges and never
    reverts a function body a later migration redefined.
"""
from __future__ import annotations

import json
import unittest

import psycopg2

from service import jcs
from tests.creative_studio.pg_harness import CS_B1_MIGRATION, ThrowawayCluster, cs_migrations
from tests.creative_studio.test_cs_int_unit import jcs_corpus

EXPECTED_CS_CHAIN = [
    "20261030210000_cs_b1_platform_foundation.sql",
    "20261031140100_cs_b2_brand_campaign_inputs.sql",
    "20261031140300_cs_b3_provider_execution.sql",
    "20261031140400_cs_b4_composition_exports.sql",
]

_CS_REL = "((n.nspname = 'public' and c.relname like 'cs\\_%%') or n.nspname = 'cs_private')"
_CS_FN = "((n.nspname = 'public' and p.proname like 'cs\\_%%') or n.nspname = 'cs_private')"

CATALOGUE = {
    "policies": """select schemaname, tablename, policyname, permissive, roles::text, cmd, qual, with_check
                     from pg_policies
                    where (schemaname = 'public' and tablename like 'cs\\_%%') or schemaname = 'cs_private'
                       or (schemaname = 'storage' and policyname like 'cs\\_%%')
                    order by 1, 2, 3""",
    "functions": f"""select p.oid::regprocedure::text, md5(pg_get_functiondef(p.oid)), coalesce(p.proacl::text, ''),
                            p.prosecdef, coalesce(array_to_string(p.proconfig, ','), '')
                       from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                      where {_CS_FN} and p.prokind = 'f' order by 1""",
    "tables": f"""select n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity, coalesce(c.relacl::text, ''),
                         pg_get_userbyid(c.relowner)
                    from pg_class c join pg_namespace n on n.oid = c.relnamespace
                   where c.relkind in ('r', 'v', 'S') and {_CS_REL} order by 1, 2""",
    "columns": f"""select n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
                     from pg_attribute a join pg_class c on c.oid = a.attrelid
                     join pg_namespace n on n.oid = c.relnamespace
                    where c.relkind = 'r' and a.attnum > 0 and not a.attisdropped and {_CS_REL} order by 1, 2, 3""",
    "constraints": f"""select n.nspname, c.relname, k.conname, pg_get_constraintdef(k.oid)
                         from pg_constraint k join pg_class c on c.oid = k.conrelid
                         join pg_namespace n on n.oid = c.relnamespace where {_CS_REL} order by 1, 2, 3""",
    "triggers": f"""select n.nspname, c.relname, t.tgname, pg_get_triggerdef(t.oid)
                      from pg_trigger t join pg_class c on c.oid = t.tgrelid
                      join pg_namespace n on n.oid = c.relnamespace
                     where not t.tgisinternal and {_CS_REL} order by 1, 2, 3""",
    "indexes": f"""select n.nspname, c.relname, pg_get_indexdef(c.oid)
                     from pg_class c join pg_namespace n on n.oid = c.relnamespace
                    where c.relkind = 'i' and {_CS_REL} order by 1, 2""",
    "types": """select t.typname, coalesce(string_agg(e.enumlabel, ',' order by e.enumsortorder), '')
                  from pg_type t join pg_namespace n on n.oid = t.typnamespace
                  left join pg_enum e on e.enumtypid = t.oid
                 where n.nspname = 'public' and t.typname like 'cs\\_%%' and t.typtype = 'e'
                 group by 1 order by 1""",
    "roles": """select rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolinherit
                  from pg_roles where rolname like 'cs\\_%%' order by 1""",
    "buckets": "select id, public, file_size_limit, allowed_mime_types::text from storage.buckets order by 1",
}


class CsIntegratedSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = ThrowawayCluster()
        cls.pg.init()
        cls.applied = cls.pg.install()

    @classmethod
    def tearDownClass(cls):
        cls.pg.stop()

    def q(self, sql, args=None):
        with self.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, args)
            return cur.fetchall()

    def snapshot(self) -> dict:
        snap = {k: self.q(sql) for k, sql in CATALOGUE.items()}
        tables = self.q(f"""select n.nspname, c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
                             where c.relkind = 'r' and {_CS_REL} order by 1, 2""")
        snap["rows"] = [(s, t, self.q(f'select count(*) from "{s}"."{t}"')[0][0]) for s, t in tables]
        return snap

    def assertSnapshotEqual(self, before, after):
        for key in before:
            self.assertEqual(after[key], before[key], f"catalogue section {key!r} changed")

    def test_01_cs_chain_is_ordered_and_complete(self):
        self.assertEqual(cs_migrations(), EXPECTED_CS_CHAIN)
        self.assertEqual([n for n in self.applied if n >= CS_B1_MIGRATION.name], EXPECTED_CS_CHAIN)
        self.assertEqual(self.applied, sorted(self.applied))

    def test_02_whole_cs_chain_applied_twice_is_idempotent(self):
        before = self.snapshot()
        self.assertGreater(len(before["policies"]), 0)
        self.assertEqual(self.pg.reapply_cs_b1(), EXPECTED_CS_CHAIN)   # second application of every CS file
        after = self.snapshot()
        self.assertSnapshotEqual(before, after)
        self.assertEqual(len(after["policies"]), len(before["policies"]), "policy count unchanged")

    def test_03_standalone_b1_rerun_preserves_later_migrations(self):
        claim_def = "select pg_get_functiondef('public.cs_claim_work(text,text,public.cs_work_stage[],integer)'::regprocedure)"
        mime_sql = "select cs_private.cs_extension_for_mime('image/svg+xml')"
        # B2 redefined these B1 functions; without the F1 guard a B1 re-run reverts them
        self.assertIn("operation_id", self.q(claim_def)[0][0])
        self.assertEqual(self.q(mime_sql)[0][0], "svg")
        b2_policy_on_b1_table = self.q("select count(*) from pg_policies where tablename = 'cs_work_items' "
                                       "and policyname = 'cs_b2_api_insert'")[0][0]
        self.assertEqual(b2_policy_on_b1_table, 1)
        before = self.snapshot()
        self.pg.apply_file(CS_B1_MIGRATION)
        self.pg.apply_file(CS_B1_MIGRATION)
        after = self.snapshot()
        self.assertSnapshotEqual(before, after)
        self.assertIn("operation_id", self.q(claim_def)[0][0])
        self.assertEqual(self.q(mime_sql)[0][0], "svg")

    def test_04_sql_canonical_json_equals_python(self):
        """cs_private.cs_jcs (node_input_sha256 preimage) == service/jcs.py on the shared corpus."""
        corpus = jcs_corpus()
        with self.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute("set extra_float_digits = 3")           # the function pins its own setting
            cur.execute("select cs_private.cs_jcs(d) from jsonb_array_elements(%s::jsonb) with ordinality t(d, o) "
                        "order by o", (json.dumps(corpus),))
            got = [r[0] for r in cur.fetchall()]
        self.assertEqual(len(got), len(corpus))
        for doc, sql in zip(corpus, got):
            self.assertEqual(sql, jcs.canonical_text(doc), doc)

    def test_05_sql_canonical_json_refuses_outside_its_domain(self):
        for bad in ("1e-5", "1000000000000000.5", "1.5e300", "9007199254740992", '{"\\ud83d\\ude00": 1}', '{"\\ue000": 1}'):
            with self.pg.connect() as conn, self.subTest(bad=bad):
                with self.assertRaises(psycopg2.Error) as ctx:
                    conn.cursor().execute("select cs_private.cs_jcs(%s::jsonb)", (bad,))
                self.assertEqual(ctx.exception.pgcode, "CS422")
        for role in ("anon", "authenticated", "cs_api", "cs_worker"):
            self.assertFalse(self.q("select has_function_privilege(%s, 'cs_private.cs_jcs(jsonb)', 'execute')",
                                    (role,))[0][0], role)


if __name__ == "__main__":
    unittest.main(verbosity=2)
