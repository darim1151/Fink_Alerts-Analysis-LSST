# Archive Recommendations

No files were moved or deleted in this cleanup pass. The paths below are candidates for a future, user-approved archive pass only.

## Recommended Archive Structure

```text
outputs/archive/
  bounded_extraction/
  data_transfer/
  full_night_feasibility/
  minimal_ingestion/
  smoke_test/
```

## Candidate Paths

| Source path | Proposed archive path | Reason | Safe? | Approval required? |
| --- | --- | --- | --- | --- |
| `outputs/bounded_extraction/20260623T192258Z/` | `outputs/archive/bounded_extraction/20260623T192258Z/` | Older bounded REST feasibility run superseded by later equivalent runs. | Reversible move only. | Yes |
| `outputs/bounded_extraction/20260623T192537Z/` | `outputs/archive/bounded_extraction/20260623T192537Z/` | Older bounded REST feasibility run. | Reversible move only. | Yes |
| `outputs/bounded_extraction/20260623T192632Z/` | `outputs/archive/bounded_extraction/20260623T192632Z/` | Older bounded REST feasibility run. | Reversible move only. | Yes |
| `outputs/full_night_feasibility/20260624T050717Z/` | `outputs/archive/full_night_feasibility/20260624T050717Z/` | Earlier REST full-night feasibility run superseded by later run. | Reversible move only. | Yes |
| `outputs/data_transfer/smoke_delivery/20260627T205321Z/` | `outputs/archive/data_transfer/smoke_delivery/20260627T205321Z/` | Earlier smoke hardening run superseded by `20260627T210030Z` and `20260627T211648Z`. | Reversible move only. | Yes |
| `outputs/data_transfer/smoke_delivery/20260627T211556Z/` | `outputs/archive/data_transfer/smoke_delivery/20260627T211556Z/` | Intermediate generalized pipeline verification superseded by `20260627T211648Z`. | Reversible move only. | Yes |
| `outputs/data_transfer/request_drafts/` | `outputs/archive/data_transfer/request_drafts/` | Historical request drafts may be useful but clutter active output tree. | Reversible move only. | Yes |

## Keep In Place

- `outputs/data_transfer/smoke_delivery/20260627T210030Z/`
- `outputs/data_transfer/smoke_delivery/20260627T211648Z/`
- `data/raw/data_transfer/smoke_delivery/ftransfer_lsst_2026-06-24_657339/`
- `data/processed/data_transfer/smoke_delivery/20260627T210030Z/`

These are current provenance anchors or explicitly protected paths.
