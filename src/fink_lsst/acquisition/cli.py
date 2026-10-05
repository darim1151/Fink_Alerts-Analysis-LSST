"""`fink-lsst` command line: guarded Fink LSST range acquisition.

    fink-lsst acquire --start 2026-02-25 --stop 2026-03-25             review only, writes nothing
    fink-lsst acquire --start ... --stop ... --record                   register PLANNED -> PORTAL_PREPARED
    fink-lsst acquire --start ... --stop ... --portal-check             + real portal dry run, stops before Submit
    fink-lsst acquire --start ... --stop ... --portal-check --submit    refused unless FINK_LSST_LIVE_SUBMISSION=1; typed approval
    fink-lsst acquire ... --portal-check --submit --wait-producer       + keep the browser and poll the Fink producer log
    fink-lsst status [--id ACQUISITION_ID]                              (marks a SUBMITTING left by a dead process SUBMISSION_UNCERTAIN)
    fink-lsst reconcile --id ID --resolution job_found|no_job_created --statement TEXT [--batch-id N] [--topic T]
    fink-lsst unblock --id ID --statement TEXT                          re-verify a request BLOCKED before any submission
    fink-lsst handoff --id ID                                           print the Arnor transfer plan; runs nothing

`--start`/`--stop` are a half-open UTC window: `--stop` is the first night
not requested. The science profile (LSST, Light static packet, no filters,
blocks, catalogue or SQL) is fixed; there is deliberately no option to change
it, to point at another registry, or to point at another submission
authority (the host's $XDG_STATE_HOME/fink-lsst/submission_authority.sqlite3,
default ~/.local/state/...). Review, status and portal checks only read the
authority; only an approved --submit writes to it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Mapping, Optional, Sequence, TextIO

from fink_lsst.bulk_transfer.topic_registry import DEFAULT_TOPIC_REGISTRY_PATH
from fink_lsst.data_root import REPO_ROOT, DataRootError, PathConfinementError, resolve_data_root

from .handoff import HandoffError, build_run_manifest_for_acquisition, build_topic_entry_for_acquisition, build_transfer_plan
from .authority import SubmissionAuthority, SubmissionAuthorityError, default_authority_path
from .orchestrator import AcquisitionOrchestrator, Approval, OrchestrationError, PortalVerificationError
from .planner import PlanningError
from .portal import PortalAutomationUnavailable
from .registry import DEFAULT_REGISTRY_ROOT, AcquisitionRegistry, RegistryError, registry_durability_problems
from .states import AcquisitionState


# G3B.1A: live Fink submission is off unless the operator sets exactly
# FINK_LSST_LIVE_SUBMISSION=1 for the one run, on the single authorized
# submission host. The opt-in only arms the existing guarded path (durable
# record, portal verification, typed approval, re-verification, authority
# claim, one-use SubmitAuthorization); it skips none of it.
LIVE_SUBMISSION_ENV = "FINK_LSST_LIVE_SUBMISSION"
# Bounded producer-log polling for --wait-producer.
PRODUCER_POLL_SECONDS = 30.0
DEFAULT_PRODUCER_TIMEOUT_HOURS = 12.0
MAX_PRODUCER_TIMEOUT_HOURS = 48.0
# The fixed Arnor data root (same value as scripts/arnor_production_preflight.py).
ARNOR_DATA_ROOT = "/astro/store/shire/FINK"


def live_submission_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True only when FINK_LSST_LIVE_SUBMISSION is exactly "1"."""
    env = os.environ if environ is None else environ
    return env.get(LIVE_SUBMISSION_ENV) == "1"


class InteractiveApprover:
    """Human approval: the operator must type the exact acquisition id at a terminal."""

    def __init__(self, stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout

    def approve(self, record, review_text: str) -> Optional[Approval]:
        if not self.stdin.isatty():
            print("Not approved: submission must be confirmed at an interactive terminal.", file=self.stdout)
            return None
        print(review_text, file=self.stdout)
        print("Approving activates 'Submit job' on the Fink portal once, creating a Fink job and Kafka topic.", file=self.stdout)
        print(f"Type the acquisition id to approve: {record.acquisition_id}", file=self.stdout)
        typed = self.stdin.readline().strip()
        if typed != record.acquisition_id:
            print("Not approved: the typed text does not match.", file=self.stdout)
            return None
        return Approval(method="interactive_tty", approved_fingerprint=record.fingerprint, statement=f"operator typed {record.acquisition_id} at an interactive terminal")


def _make_playwright_portal(**kwargs):
    if importlib.util.find_spec("playwright") is None:
        raise PortalAutomationUnavailable("Playwright is not installed in this environment; see docs/RANGE_ACQUISITION_ORCHESTRATOR.md")
    from .portal_playwright import PlaywrightPortalAdapter

    return PlaywrightPortalAdapter(**kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fink-lsst", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    acquire = commands.add_parser("acquire", help="plan, record, portal-check (and, when enabled, submit) one acquisition")
    acquire.add_argument("--start", required=True, help="first requested night, YYYY-MM-DD (inclusive)")
    acquire.add_argument("--stop", required=True, help="first night NOT requested, YYYY-MM-DD (exclusive)")
    acquire.add_argument("--record", action="store_true", help="register the request (PLANNED -> PORTAL_PREPARED)")
    acquire.add_argument("--portal-check", action="store_true", help="real portal dry run: upload, verify, download config, stop before Submit")
    acquire.add_argument("--submit", action="store_true", help=f"submit after typed confirmation (refused unless {LIVE_SUBMISSION_ENV}=1)")
    acquire.add_argument("--wait-producer", action="store_true", help="after --submit, keep the browser open and poll the Fink producer log")
    acquire.add_argument(
        "--producer-timeout-hours", type=float, default=DEFAULT_PRODUCER_TIMEOUT_HOURS,
        help=f"stop polling after this many hours (default {DEFAULT_PRODUCER_TIMEOUT_HOURS:g}, at most {MAX_PRODUCER_TIMEOUT_HOURS:g})",
    )
    acquire.add_argument("--headed", action="store_true", help="show the browser window during --portal-check")

    status = commands.add_parser("status", help="show registered acquisitions")
    status.add_argument("--id", dest="acquisition_id")

    reconcile = commands.add_parser("reconcile", help="explicitly resolve SUBMISSION_UNCERTAIN or TOPIC_TIMEOUT")
    reconcile.add_argument("--id", dest="acquisition_id", required=True)
    reconcile.add_argument("--resolution", required=True, choices=["job_found", "no_job_created"])
    reconcile.add_argument("--statement", required=True, help="what was checked (portal batch list, Fink support reply, ...)")
    reconcile.add_argument("--batch-id")
    reconcile.add_argument("--topic")

    unblock = commands.add_parser("unblock", help="return a request BLOCKED before any submission to PORTAL_PREPARED")
    unblock.add_argument("--id", dest="acquisition_id", required=True)
    unblock.add_argument("--statement", required=True, help="what was resolved")

    handoff = commands.add_parser("handoff", help="print the Arnor transfer plan for an identified topic; runs nothing")
    handoff.add_argument("--id", dest="acquisition_id", required=True)
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    registry_root: Path = REPO_ROOT / DEFAULT_REGISTRY_ROOT,
    topic_registry_path: Path = REPO_ROOT / DEFAULT_TOPIC_REGISTRY_PATH,
    as_of: Optional[date] = None,
    authority_path: Optional[Path] = None,
) -> int:
    """CLI entry point. Keyword arguments exist only for tests; the command line cannot set them."""
    args = build_parser().parse_args(argv)
    authority = SubmissionAuthority(authority_path or default_authority_path())
    orchestrator = AcquisitionOrchestrator(AcquisitionRegistry(registry_root), authority=authority, topic_registry_path=topic_registry_path)
    try:
        if args.command == "acquire":
            return _acquire(orchestrator, args, as_of)
        if args.command == "status":
            return _status(orchestrator, args)
        if args.command == "reconcile":
            record = orchestrator.reconcile_submission(
                orchestrator.registry.load(args.acquisition_id), resolution=args.resolution, statement=args.statement, batch_id=args.batch_id, topic=args.topic
            )
            print(f"{record.acquisition_id}: {record.state.value}")
            return 0
        if args.command == "unblock":
            record = orchestrator.unblock_before_submission(orchestrator.registry.load(args.acquisition_id), statement=args.statement)
            print(f"{record.acquisition_id}: {record.state.value}")
            return 0
        if args.command == "handoff":
            return _handoff(orchestrator, args)
    except (PlanningError, RegistryError, HandoffError, DataRootError, PathConfinementError, SubmissionAuthorityError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OrchestrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


def _acquire(orchestrator: AcquisitionOrchestrator, args: argparse.Namespace, as_of: Optional[date]) -> int:
    if args.submit and not args.portal_check:
        print("error: --submit requires --portal-check (verification and submission happen in one browser session)", file=sys.stderr)
        return 2
    if args.wait_producer and not args.submit:
        print("error: --wait-producer requires --submit (the producer log is read in the submitting browser session)", file=sys.stderr)
        return 2
    if not 0 < args.producer_timeout_hours <= MAX_PRODUCER_TIMEOUT_HOURS:
        print(f"error: --producer-timeout-hours must be in (0, {MAX_PRODUCER_TIMEOUT_HOURS:g}]", file=sys.stderr)
        return 2
    live = bool(args.submit and live_submission_enabled())
    mode = "submit" if args.submit else "portal_check" if args.portal_check else "record" if args.record else "dry_run"
    plan = orchestrator.plan(args.start, args.stop, as_of=as_of)
    print(orchestrator.render_review(plan, mode=mode), end="")
    if args.submit and not live:
        print(f"error: live submission is not enabled (set {LIVE_SUBMISSION_ENV}=1 for this run on the authorized host); nothing was recorded, claimed or opened", file=sys.stderr)
        return 2
    if mode == "dry_run":
        print("Nothing was written. Add --record to register this request.")
        return 0

    portal = None
    if mode in {"portal_check", "submit"}:
        try:
            portal = _make_playwright_portal(headless=not args.headed, live_submit_enabled=live)
        except PortalAutomationUnavailable as exc:
            print(f"BROWSER_DRY_RUN_NOT_EXECUTED: {exc}", file=sys.stderr)
            return 3
    record = orchestrator.recover_interrupted_submission(orchestrator.record(plan))
    print(f"Recorded:           {record.acquisition_id} ({record.state.value})")
    print(f"Registry:           {record.directory}")
    if portal is None:
        return 0
    if args.submit:
        problems = registry_durability_problems(record.directory)
        if problems:
            print("error: refusing to submit until the registry record is durable: " + "; ".join(problems), file=sys.stderr)
            return 2
    try:
        try:
            record = orchestrator.verify_portal(record, portal)
        except PortalVerificationError as exc:
            state = orchestrator.registry.load(record.acquisition_id).state.value
            print(f"Portal verification: FAILED ({state}). Submit was NOT activated.\n{exc}", file=sys.stderr)
            return 1
        evidence = record.last_entry(AcquisitionState.PORTAL_VERIFIED).evidence
        print(f"Portal verification: {record.state.value}")
        print(f"  code revision: {evidence.get('code_revision')}")
        print(f"  browser context: {evidence['context_id']} (service workers: {evidence.get('service_workers')})")
        print(f"  downloaded config sha256: {evidence['downloaded_config_sha256']} ({evidence['downloaded_config_ref']['path']})")
        print(f"  form dates: {evidence['form']['date_value_text'] or evidence['form']['date_display_text']}")
        print(f"  form content: {evidence['form']['content_values']}; filters selected: {evidence['form']['selected_filter_buttons']}; blocks selected: {evidence['form']['selected_block_buttons']}")
        print(f"  portal estimate (statistical gauge, not an expected count): {evidence['portal_estimated_alerts_text']}")
        print(f"  Submit callbacks blocked by the guard: mount-time {evidence['initial_submit_callbacks_blocked']}, click-like {evidence['submit_requests_blocked']}")
        if not args.submit:
            print("Final review reached. Submit was NOT activated.")
            return 0
        record = orchestrator.submit(record, portal, InteractiveApprover())
        print(f"Submission recorded: {record.acquisition_id} ({record.state.value}) batch={record.batch_id} topic={record.topic}")
        code = 0
        if args.wait_producer:
            code = _wait_producer(orchestrator, record, portal, timeout_hours=args.producer_timeout_hours)
            record = orchestrator.registry.load(record.acquisition_id)
        _print_arnor_handoff(record)
        return code
    finally:
        portal.close()


def _wait_producer(orchestrator: AcquisitionOrchestrator, record, portal, *, timeout_hours: float) -> int:
    """Poll the producer log in the submitting browser session; completion needs the canonical markers."""
    if record.state != AcquisitionState.TOPIC_IDENTIFIED:
        print(f"Producer wait skipped: {record.state.value} has no identified topic; reconcile the topic first.", file=sys.stderr)
        return 4
    print(f"Waiting for the Fink producer log (every {PRODUCER_POLL_SECONDS:g} s, at most {timeout_hours:g} h); Kafka is not consulted.")
    record = orchestrator.wait_for_producer(
        record, portal,
        timeout_seconds=timeout_hours * 3600,
        poll_seconds=PRODUCER_POLL_SECONDS,
        sleep=getattr(portal, "wait", None) or time.sleep,
        monotonic=time.monotonic,
    )
    if record.state == AcquisitionState.PRODUCER_COMPLETE:
        print("Producer: PRODUCER_COMPLETE ('Data available at topic' and 'End.' observed for this topic)")
        return 0
    if record.state == AcquisitionState.BLOCKED:
        print("Producer: BLOCKED (the portal job log reports a failure); Control review needed", file=sys.stderr)
        return 1
    print(f"Producer: completion NOT observed; the record stays {record.state.value}", file=sys.stderr)
    return 4


def _print_arnor_handoff(record) -> None:
    """Identifiers and the intended Arnor location; runs nothing and prints no endpoint or credential."""
    raw = f"{ARNOR_DATA_ROOT}/{record.request.expected_raw_dir(record.topic)}" if record.topic else "unknown until a topic is identified"
    print("Arnor handoff (nothing was transferred; no SSH, finkctl or Kafka was run):")
    print(f"  acquisition ID:    {record.acquisition_id}")
    print(f"  batch ID:          {record.batch_id}")
    print(f"  Kafka topic:       {record.topic}")
    print(f"  state:             {record.state.value}")
    print(f"  Arnor raw path:    {raw}")
    print(f"  transfer command:  not generated at {record.state.value}; the transfer plan becomes ready only at TOPIC_VERIFIED (Kafka topic metadata pre-check)")
    if record.topic:
        print(f"  handoff plan:      FINK_LSST_DATA_ROOT={ARNOR_DATA_ROOT} fink-lsst handoff --id {record.acquisition_id}")
    print(f"  next:              commit and push the registry record {record.directory}")


def _status(orchestrator: AcquisitionOrchestrator, args: argparse.Namespace) -> int:
    if args.acquisition_id:
        record = orchestrator.recover_interrupted_submission(orchestrator.registry.load(args.acquisition_id))
        request = record.request
        print(f"{record.acquisition_id}: {record.state.value}")
        print(f"  window [{request.start}, {request.stop}) portal {request.portal_startdate} to {request.portal_stopdate} inclusive; fingerprint {record.fingerprint}")
        print(f"  batch {record.batch_id} topic {record.topic}")
        attempt = orchestrator.authority.get(record.fingerprint)
        print(f"  submission authority: {f'attempt {attempt.attempt_id} ({attempt.status})' if attempt else 'no attempt'}")
        for entry in record.entries:
            print(f"  {entry.seq:>3} {entry.at_utc} {entry.from_state.value if entry.from_state else '-':>22} -> {entry.to_state.value:<22} {entry.actor}: {entry.reason}")
        return 0
    records = orchestrator.registry.list_records()
    if not records:
        print("No acquisitions registered.")
    for record in records:
        print(f"{record.acquisition_id}  {record.state.value:<22} [{record.request.start}, {record.request.stop})  topic={record.topic}")
    return 0


def _handoff(orchestrator: AcquisitionOrchestrator, args: argparse.Namespace) -> int:
    record = orchestrator.registry.load(args.acquisition_id)
    data_root = resolve_data_root(repo_root=REPO_ROOT)
    plan = build_transfer_plan(record, data_root)
    payload = plan.to_dict()
    payload["data_root"] = str(data_root)
    payload["run_manifest"] = json.loads(json.dumps(_manifest_dict(record)))
    payload["topic_entry"] = build_topic_entry_for_acquisition(record)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def _manifest_dict(record) -> dict:
    from fink_lsst.bulk_transfer.run_manifest import manifest_to_dict

    return manifest_to_dict(build_run_manifest_for_acquisition(record))


if __name__ == "__main__":
    raise SystemExit(main())
