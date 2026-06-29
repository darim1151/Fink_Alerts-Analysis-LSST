# Full-Packet Ingestion Performance Note

`run_analysis.py --stage all --allow-partial` was interrupted on the partial full-week full-packet delivery because full nested JSON serialization became too slow.

The expensive path was:

- `_add_json_shadow_columns`
- `json_dumps_stable`

The root issue is the combination of full-packet nested fields and a large number of small Parquet files. The current partial delivery has more than 100,000 readable files and hundreds of thousands of rows. Serializing every nested field into JSON shadow columns is not appropriate as the default full-packet stress-test path.

Near-term solution:

- use `scripts/run_quick_full_packet_analysis.py` for metadata-only scans and bounded flattened samples,
- skip cutout/heavy fields by default,
- keep outputs labeled `partial_debug_stress_test`,
- avoid completeness claims.

Future improvement:

- selective nested serialization,
- sidecar storage for cutout/heavy fields,
- batch file reads,
- full-packet mode that avoids JSON shadow columns for all nested fields by default,
- resumable file batches with summary-only aggregation.
