"""Publish the Higgsfield provider catalogue (operator/deployment step, never a runtime role).

Prices each entry with the CLI's free cost quote (no job is created), then stores immutable catalogue rows.
  IP_HIGGSFIELD_DEV_CLI_LOGIN=1 DATABASE_URL=... python3 scripts/ops/publish_catalogue.py [--dry-run]
"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from service.providers import catalogue  # noqa: E402

def main() -> int:
    source = json.load(open(os.path.join(os.path.dirname(__file__), "..", "..", "config/providers/higgsfield.catalogue-source.json")))
    entries, refusals = catalogue.build_entries(source, credential=None)
    print(json.dumps({"priced": [{"entry": e.entry_id, "tier": e.tier, "unit_cost_micros": e.unit_cost_micros} for e in entries],
                      "refused": refusals}, indent=2))
    if refusals or not entries:
        return 1
    if "--dry-run" in sys.argv:
        return 0
    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn, conn.cursor() as cur:
        print("published", catalogue.publish(cur, entries))
    conn.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
