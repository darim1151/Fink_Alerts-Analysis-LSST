# Science Plan

## Purpose

Build a local-first Fink LSST/Rubin alert-analysis platform that can grow from access reconnaissance into a broader science environment. The project should respect Fink's object, alert, classification, and context vocabulary rather than copying ANTARES locus-centered assumptions.

## Phase 0: Access And Schema Reconnaissance

- Confirm public/no-login REST access.
- Snapshot available schemas and classes.
- Run tiny endpoint probes only.
- Document endpoints that work, fail, or require later setup.
- Create generic validation and summary utilities.

## Phase 1: Data Model Stabilization

- Define internal tables for sources/alerts, objects, forced photometry, classifications, context, nightly summaries, and validation reports.
- Map Fink fields into those tables.
- Keep mappings versioned because broker schemas can evolve.
- Add fixture-based tests using small saved public payloads.

## Phase 2: Population-Level Alert Stream Analysis

- Study nightly and cumulative alert counts.
- Track class distributions and field availability.
- Monitor alert quality, coordinates, time coverage, and missingness.
- Build small reproducible summary artifacts before scaling up.

## Phase 3: Object-Evolution Analysis

- Analyze object lightcurve evolution from alert/source rows.
- Track new detections, upper limits, quality tags, and object-level summaries.
- Add cadence and band/filter summaries.
- Prepare candidate ranking features without claiming classification performance prematurely.

## Phase 4: Classification And Tag Evolution

- Track how classifications and tags evolve over object history.
- Compare class labels, confidence-like fields, contextual crossmatches, and science-module outputs.
- Separate observed alert data from Fink-added values and runtime-generated values.

## Phase 5: Candidate Prioritization Layer

- Build transparent ranking features for follow-up triage.
- Include validation status and missing-data flags.
- Keep ranking explainable and reproducible.
- Avoid automated follow-up actions in the local-first phase.

## Phase 6: ANTARES Comparison

- Compare Fink's alert/object/classification-centered view with ANTARES's locus-centered view.
- Align concepts carefully rather than forcing one broker's vocabulary onto the other.
- Compare data availability, historical completeness, object grouping, classification behavior, and nightly/cumulative summaries.

## Non-Goals For The Current Phase

- Large historical ingestion.
- Paid cloud workflows.
- Privileged broker services.
- Kafka or Spark integration.
- Production dashboards.
- Unsupported science conclusions.

