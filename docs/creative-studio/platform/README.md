# CS-M1 platform packet

The selected hosting is **Vercel web/API plus bounded Python/FFmpeg worker Functions**, backed by the existing shared Supabase project for durable queue/state/storage. The existing image-pipeline compiler remains canonical. No continuously running paid worker or new hosting subscription is assumed.

- [Architecture and security](ARCHITECTURE.md)
- [Database, queue and recovery semantics](DATABASE_CONTRACT.md)
- [Deployment operations and observed evidence](OPERATIONS_AND_EVIDENCE.md)
- [Machine-readable deployment design](deployment-contract.json)
- [Machine-readable database design](database-contract.json)

These are implementable design artifacts, not migrations or deployed code. No test, build, provider generation or database mutation was run. Runtime bounds and failure behaviour remain unverified until CS-M3, after the CS-M2 assembled build.

Vercel plan/remaining quota was not exposed by the connected account metadata. Actual authorized capacity must be recorded before dispatch is enabled. Intended workspace Model API credentials and explicit media-test budget remain prerequisites for paid CS-M3 acceptance. Neither prerequisite authorizes a new subscription or broader spending.

