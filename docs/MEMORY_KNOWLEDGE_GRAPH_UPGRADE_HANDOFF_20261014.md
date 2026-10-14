# Memory, knowledge, and relation upgrade handoff

Status: isolated development candidate. This branch has not been deployed or accepted for production.

## Scope included in this branch

- Owner-scoped sourced knowledge search with separate knowledge-use consent.
- Candidate relation paths and causal claims. A candidate claim is not a verified causal finding.
- Short-lived, room-bound voice context carrying bounded sourced notes without giving the voice worker database credentials.
- Synthetic SQLite backup/restore and vector comparison probes, plus an opt-in PostgreSQL/pgvector probe that requires an isolated disposable database.

## Architecture decision

Keep original events and explicit human confirmation/withdrawal as the authority. Memory, lexical, vector, and graph structures are derived indexes that must retain source IDs and be rebuildable. Start with SQLite plus Chinese normalization and lexical search; compare sqlite-vec using synthetic data. Evaluate pgvector only in an isolated, privately networked trial if measured quality, capacity, or operational needs justify it. Evaluate a temporal graph framework only after concrete multi-hop or time-conflict cases exceed the existing relation query.

## Open gaps before deployment

1. A unified cross-database source, consent, revision, and withdrawal contract is still missing. Legacy explicit memories may lack a source event ID.
2. The causal-claim workflow lacks a human-supported state and structured counterevidence review.
3. The existing vector probe is small (16 synthetic records, 10 queries); PostgreSQL/pgvector has not been executed. This is not product-quality evidence.
4. Voice context is bounded at call start; in-call topic changes do not trigger another retrieval. Receiving a signed context does not prove useful memories were included.
5. Production volume-path, cross-deployment readback, backup restore, authenticated endpoint, multi-account production, and real-device acceptance remain unverified.

## Verification gates

Use at least two synthetic owners and a labeled Traditional/Simplified Chinese dataset covering name collisions, corrections, withdrawals, no-answer cases, and cross-owner bait. Fail if any other owner's event or index is returned, a withdrawn item reappears, a citation does not resolve to a valid original event, or a model candidate is presented as confirmed fact. Compare lexical, normalized lexical, sqlite-vec, and optional pgvector with identical source and consent filters. Record source top-1/top-5, citation accuracy, abstention, p50/p95 latency, memory, build and restore time, and owner-filtered approximate-index recall.

For release, record separately: local test results, provider deployment status, authenticated endpoint responses, cross-deployment readback, and human voice acceptance. No production data migration, paid service creation, or traffic cutover is implied by this branch.
