# Full-Packet Ingestion Performance Note

The partial full-week full-packet ingestion attempt was interrupted because nested JSON serialization was too slow for the observed full-packet shape.

- Expensive path: `_add_json_shadow_columns` / `json_dumps_stable`
- Root issue: large nested full-packet fields across 107,504 tiny files
- Near-term solution: quick partial stress analysis, metadata scans, bounded flattened samples
- Completeness claims: blocked

Future work should add selective nested serialization, sidecar handling for cutout/heavy fields, batch file reads, and a full-packet mode that avoids JSON shadow columns for all nested fields by default.
