"""Playwright adapter for the public Fink LSST Data Transfer form (layer E).

Runs on the local/control side only, from a separate environment (see
docs/RANGE_ACQUISITION_ORCHESTRATOR.md); it is never installed into the Arnor
science environment. Each `open()` creates a fresh, ephemeral browser context
with service workers blocked (so request interception sees every request):
no stored profile, cookies or session state are read or written, and no
screenshots are taken. `context_id` names the live context generation; it
rotates on every open, configuration upload and document (re)load, and is
None after close, so a verification can never be reused in another state.

Selectors follow the portal's own component ids (`upload_yaml_file`,
`date-range-picker`, `field_select`, `extra_cond`, `submit_yaml_file`,
`submit_datatransfer`, `batch_id`, `topic_name`, `batch_log`) and Mantine's
static class names. They locate controls; correctness is decided by the
semantic checks in `portal.py` and `portal_config.py`, which fail closed when
something cannot be read.

`submit(authorization)` needs a one-use `SubmitAuthorization` from the
orchestrator for this context and an armed adapter. It re-reads the form and
refuses if the scientific state differs from the approved one, then clicks
"Submit job" once. Every request is classified structurally
(`portal.classify_dash_request`): the only Submit callback ever let through
is the single one sent during that `submit()` call, with `n_clicks >= 1` and
form state equal to the canonical request; the allowance is revoked when it
is used and in `finally`. The portal's mount-time Submit callback (no click
count) is always aborted and counted in `blocked_initial_submit_callbacks`;
anything else is aborted and counted in `blocked_submit_requests`.
"""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .portal import (
    PORTAL_URL,
    FinalReviewObservation,
    PortalAutomationUnavailable,
    PortalError,
    PortalFormObservation,
    PortalScientificState,
    ProducerLogObservation,
    SubmissionDisabledError,
    SubmitAuthorization,
    SubmitObservation,
    check_submit_callback_state,
    classify_dash_request,
    consume_submit_authorization,
    scientific_state_from_observation,
)


STEP_DATES, STEP_REDUCE, STEP_CONTENT, STEP_LAUNCH = 1, 2, 3, 4
_DOWNLOAD_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.ya?ml")
_UNSELECTED_VARIANT = "light"


class PlaywrightPortalAdapter:
    def __init__(
        self,
        *,
        url: str = PORTAL_URL,
        headless: bool = True,
        channel: Optional[str] = "chrome",
        live_submit_enabled: bool = False,
        timeout_ms: int = 60_000,
    ):
        if url != PORTAL_URL:
            raise PortalError(f"only the public LSST Data Transfer portal is supported, not {url!r}")
        self.url = url
        self.headless = headless
        self.channel = channel
        self.live_submit_enabled = bool(live_submit_enabled)
        self.timeout_ms = timeout_ms
        self.context_id: Optional[str] = None
        self.service_worker_policy = "block"
        self._submit_clicked = False
        self.blocked_submit_requests = 0
        self.blocked_initial_submit_callbacks = 0
        self._armed_authorization: Optional[SubmitAuthorization] = None
        self._callback_admitted = False
        self._guard_reason = ""
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    # ------------------------------------------------------------ lifecycle

    def open(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise PortalAutomationUnavailable(
                "Playwright is not installed in this environment; see docs/RANGE_ACQUISITION_ORCHESTRATOR.md (local portal environment)"
            ) from exc
        self._playwright = sync_playwright().start()
        launch: dict = {"headless": self.headless}
        if self.channel:
            launch["channel"] = self.channel
        self._browser = self._playwright.chromium.launch(**launch)
        # Ephemeral context: no storage state; service workers blocked so route() sees every request.
        self._context = self._browser.new_context(accept_downloads=True, service_workers=self.service_worker_policy)
        self._context.set_default_timeout(self.timeout_ms)
        self._context.route("**/*", self._guard_submit_requests)
        self._page = self._context.new_page()
        self._page.goto(self.url, wait_until="load")
        self._page.get_by_text("Fink Data Transfer", exact=True).first.wait_for()
        self._page.locator("#upload_yaml_file").wait_for()
        self._page.on("load", lambda *_: self._new_context_generation())
        self._new_context_generation()

    def close(self) -> None:
        self.context_id = None
        self._armed_authorization = None
        for closer in (getattr(self._context, "close", None), getattr(self._browser, "close", None), getattr(self._playwright, "stop", None)):
            if closer is not None:
                try:
                    closer()
                except Exception:  # noqa: BLE001 - best-effort teardown
                    pass
        self._page = self._context = self._browser = self._playwright = None

    def _new_context_generation(self) -> None:
        """Invalidate every verification bound to the previous state."""
        self.context_id = f"playwright-{uuid.uuid4().hex}"

    # ------------------------------------------------------------ form

    def upload_config(self, path: Path) -> None:
        page = self._require_page()
        page.locator("#upload_yaml_file input[type=file]").set_input_files(str(path))
        self._new_context_generation()  # the form state was replaced
        self._go_to_step(STEP_DATES)
        self._wait_until(lambda: bool(self._date_hidden_value()), "the uploaded configuration to populate the date range")

    def observe_form(self) -> PortalFormObservation:
        page = self._require_page()
        self._go_to_step(STEP_DATES)
        date_value = self._date_hidden_value()
        date_display = page.locator("#date-range-picker").inner_text().strip()

        self._go_to_step(STEP_REDUCE)
        catalog_label = page.locator("#gauge_catalog_number").inner_text().strip()
        page.locator("#modal-datatransfer-button-1").click()
        filters = page.locator("button.button_filter_transfer")
        blocks = page.locator("button.button_blocks_transfer")
        filters.first.wait_for()
        selected_filters = self._selected_buttons(filters)
        selected_blocks = self._selected_buttons(blocks)
        filter_count, block_count = filters.count(), blocks.count()
        self._close_modal()
        page.locator("#modal-datatransfer-button-3").click()
        extra = page.locator("#extra_cond")
        extra.wait_for()
        extra_text = extra.input_value()
        self._close_modal()

        self._go_to_step(STEP_CONTENT)
        root = page.locator("div.mantine-MultiSelect-root", has=page.locator("#field_select"))
        root.wait_for()
        pills = tuple(text.strip() for text in root.locator(".mantine-Pill-label").all_inner_texts() if text.strip())
        hidden = root.locator("xpath=..").locator("input[type=hidden]")
        hidden_values = tuple(item for item in (hidden.first.input_value().split(",") if hidden.count() else []) if item)
        if hidden.count() and hidden_values != pills:
            raise PortalError(f"packet selection is ambiguous: pills {list(pills)} vs form value {list(hidden_values)}")
        return PortalFormObservation(
            date_value_text=date_value,
            date_display_text=date_display,
            content_values=pills,
            selected_filter_buttons=selected_filters,
            selected_block_buttons=selected_blocks,
            filter_buttons_seen=filter_count,
            block_buttons_seen=block_count,
            extra_cond_text=extra_text,
            catalog_label=catalog_label,
            alert_estimate_text=page.locator("#gauge_alert_number").inner_text().strip().replace("\n", " "),
        )

    def reach_final_review(self) -> FinalReviewObservation:
        page = self._require_page()
        self._go_to_step(STEP_LAUNCH)
        submit = page.locator("#submit_datatransfer")
        download = page.locator("#submit_yaml_file")
        submit.wait_for()
        return FinalReviewObservation(
            reached=self._active_step() == STEP_LAUNCH,
            submit_visible=submit.is_visible(),
            submit_enabled=submit.is_enabled(),
            download_visible=download.is_visible(),
        )

    def download_config(self, destination_dir: Path) -> Path:
        page = self._require_page()
        self._go_to_step(STEP_LAUNCH)
        with page.expect_download() as info:
            page.locator("#submit_yaml_file").click()
        download = info.value
        name = download.suggested_filename
        if not _DOWNLOAD_NAME.fullmatch(name or ""):
            raise PortalError(f"unexpected download name {name!r}")
        target = Path(destination_dir) / name
        download.save_as(str(target))
        return target

    # ------------------------------------------------------------ submission (guarded)

    def submit(self, authorization: SubmitAuthorization) -> SubmitObservation:
        """Activate Submit once under a one-use, context-bound authorization."""
        consume_submit_authorization(authorization)
        if not self.live_submit_enabled:
            raise SubmissionDisabledError("this portal adapter was not armed for live submission")
        if self._submit_clicked:
            raise SubmissionDisabledError("Submit was already activated in this context; it is never activated twice")
        if not self.context_id or authorization.context_id != self.context_id:
            raise SubmissionDisabledError("authorization belongs to another browser context; verify again")
        page = self._require_page()
        self._go_to_step(STEP_LAUNCH)
        state = self._read_scientific_state()
        if state.digest() != authorization.approved_state_digest:
            raise PortalError(f"portal scientific state changed since approval: {state.to_dict()}")
        self._go_to_step(STEP_LAUNCH)
        if not self._submit_control_available():
            raise PortalError("Submit control is not available")
        self._submit_clicked = True
        self._callback_admitted, self._guard_reason = False, ""
        self._armed_authorization = authorization
        batch_id = topic = None
        try:
            self._click_submit()
            deadline = time.monotonic() + self.timeout_ms / 1000
            while time.monotonic() < deadline and self._armed_authorization is not None:
                page.wait_for_timeout(100)
            while self._callback_admitted and time.monotonic() < deadline:
                batch_id, topic = self._read_submission_ids()
                if batch_id and topic:
                    break
                page.wait_for_timeout(1000)
        finally:
            self._armed_authorization = None
        return SubmitObservation(
            clicked=True,
            batch_id=batch_id,
            topic=topic,
            notifications=self._read_notifications(),
            callback_admitted=self._callback_admitted,
            guard_reason=self._guard_reason or ("" if self._callback_admitted else "no Submit callback was observed"),
        )

    def _read_scientific_state(self) -> PortalScientificState:
        return scientific_state_from_observation(self.observe_form())

    def _submit_control_available(self) -> bool:
        button = self._require_page().locator("#submit_datatransfer")
        return bool(button.is_visible() and button.is_enabled())

    def _click_submit(self) -> None:
        self._require_page().locator("#submit_datatransfer").click()

    def _read_submission_ids(self) -> tuple:
        page = self._require_page()
        batch_id = (page.locator("#batch_id").text_content() or "").strip() or None
        topic = (page.locator("#topic_name").text_content() or "").strip() or None
        return batch_id, topic

    def _read_notifications(self) -> tuple:
        try:
            return tuple(text.strip() for text in self._require_page().locator(".mantine-Notification-root").all_inner_texts() if text.strip())
        except Exception:  # noqa: BLE001 - notifications are informational only
            return ()

    def read_producer_log(self) -> ProducerLogObservation:
        if self._page is None:
            return ProducerLogObservation(available=False, text="")
        try:
            return ProducerLogObservation(available=True, text=self._page.locator("#batch_log").inner_text())
        except Exception:  # noqa: BLE001 - a lost page means the log is unavailable, not complete
            return ProducerLogObservation(available=False, text="")

    # ------------------------------------------------------------ helpers

    def _guard_submit_requests(self, route: Any, request: Any) -> None:
        try:
            body = request.post_data if request.method == "POST" else None
        except Exception:  # noqa: BLE001 - an unreadable body cannot be classified
            body = "\x00unreadable"
        decision = classify_dash_request(getattr(request, "url", ""), request.method, body)
        if decision.kind == "unrelated":
            route.continue_()
            return
        if decision.kind == "submit_mount":
            self.blocked_initial_submit_callbacks += 1
            route.abort()
            return
        authorization, self._armed_authorization = self._armed_authorization, None  # one shot, used or not
        if decision.kind == "submit_click" and authorization is not None and self.live_submit_enabled and not self._callback_admitted:
            problems = check_submit_callback_state(decision.states, authorization.expected_config)
            if not problems:
                self._callback_admitted = True
                route.continue_()
                return
            self._guard_reason = "; ".join(problems)
        elif authorization is not None:
            self._guard_reason = decision.reason or f"blocked {decision.kind} callback"
        self.blocked_submit_requests += 1
        route.abort()

    def _require_page(self) -> Any:
        if self._page is None:
            raise PortalError("portal is not open")
        return self._page

    def _active_step(self) -> int:
        """Index of the step in progress, or -1 when none is (never mistaken for the final step)."""
        steps = self._require_page().locator(".mantine-Stepper-step")
        for index in range(steps.count()):
            if steps.nth(index).get_attribute("data-progress") == "true":
                return index
        return -1

    def _go_to_step(self, target: int) -> None:
        page = self._require_page()
        for _ in range(12):
            current = self._active_step()
            if current == target:
                return
            if current < 0:
                raise PortalError("the form shows no step in progress; refusing to navigate blind")
            page.locator("#next-basic-usage" if current < target else "#back-basic-usage").click()
            self._wait_until(lambda: self._active_step() != current, f"the form to leave step {current + 1}")
        raise PortalError(f"could not reach form step {target + 1}")

    def _date_hidden_value(self) -> str:
        hidden = self._require_page().locator("#date_tab input[type=hidden]")
        return hidden.first.input_value().strip() if hidden.count() else ""

    def _selected_buttons(self, buttons: Any) -> tuple:
        selected = []
        for index in range(buttons.count()):
            button = buttons.nth(index)
            if (button.get_attribute("data-variant") or "") != _UNSELECTED_VARIANT:
                selected.append(button.inner_text().strip() or f"button#{index}")
        return tuple(selected)

    def _close_modal(self) -> None:
        page = self._require_page()
        page.keyboard.press("Escape")
        page.locator(".mantine-Modal-content").first.wait_for(state="hidden")

    def _wait_until(self, condition, what: str) -> None:
        deadline = time.monotonic() + self.timeout_ms / 1000
        while time.monotonic() < deadline:
            if condition():
                return
            self._require_page().wait_for_timeout(250)
        raise PortalError(f"timed out waiting for {what}")

