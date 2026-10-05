"""Guarded Fink LSST range acquisition (FINK-G3B.0).

Layers: `profile` (fixed science profile), `planner` (half-open window and
canonical request), `portal_config` (portal YAML compiler and semantic
comparison), `states`/`registry` (state machine and durable registry),
`portal`/`portal_playwright` (portal adapter), `handoff` (Kafka/Arnor transfer
plan), `evidence` (reconciliation and integrity evidence), `orchestrator` and
`cli`. See docs/RANGE_ACQUISITION_ORCHESTRATOR.md.
"""
