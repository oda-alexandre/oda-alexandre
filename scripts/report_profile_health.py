# SPDX-FileCopyrightText: 2024-2026 ODA Alexandre
# SPDX-License-Identifier: EUPL-1.2+

"""Create/update/close one GitHub issue for Profile README automation health.

The generator writes a small JSON health report for recoverable source failures.
This script combines that report with GitHub Actions step outcomes so degraded
runs are visible to the repository owner without exposing errors in README.md.

Operational contract: keep exactly one open Profile Health incident, update it
when the incident set changes, and close it automatically after a healthy run.
See ``docs/PROFILE_README_ARCHITECTURE.md`` before changing this lifecycle.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TypeAlias, TypedDict, cast

TOKEN = os.environ.get("GH_TOKEN", "").strip()
REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "").strip()
HEALTH_FILE = Path(os.environ.get("PROFILE_HEALTH_FILE", ".profile-health.json"))
LABEL = "profile-health"
PROFILE_HEALTH_REPORT_COMPONENT = "Profile health report"
TITLE_DEGRADED = "[Profile Health] README automation degraded"
TITLE_FAILED = "[Profile Health] README publication failed"
PIPELINE_COMPONENTS = {
    "Published branch preparation",
    "README generation",
    "Published snapshot verification",
    "Published profile publication",
}

if not TOKEN:
    raise SystemExit("GH_TOKEN is required")
if "/" not in REPOSITORY:
    raise SystemExit("GITHUB_REPOSITORY is required")

OWNER, _ = REPOSITORY.split("/", 1)
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}"

JsonObject: TypeAlias = dict[str, object]
JsonArray: TypeAlias = list[object]
JsonContainer: TypeAlias = JsonObject | JsonArray | None


class Incident(TypedDict):
    component: str
    message: str


def _decode_json_container(raw: bytes | str) -> JsonContainer:
    """Decode a GitHub JSON response without leaking ``Any`` into strict typing."""
    decoded = cast(object, json.loads(raw))
    if decoded is None:
        return None
    if isinstance(decoded, dict):
        return cast(JsonObject, decoded)
    if isinstance(decoded, list):
        return cast(JsonArray, decoded)
    raise ValueError("Expected a JSON object, array, or null")


def api(
    method: str,
    path: str,
    payload: JsonObject | None = None,
    *,
    allow_status: tuple[int, ...] = (),
) -> tuple[int, JsonContainer]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        API_ROOT + path,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {TOKEN}",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "profile-readme-health",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, _decode_json_container(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if exc.code in allow_status:
            try:
                parsed = _decode_json_container(raw) if raw else None
            except ValueError:
                parsed = None
            return exc.code, parsed
        raise RuntimeError(
            f"GitHub API HTTP {exc.code} for {method} {path}: {raw[:600]}"
        ) from exc



def _health_report_incidents(generate_outcome: str) -> dict[str, str]:
    incidents: dict[str, str] = {}
    if not HEALTH_FILE.exists():
        if generate_outcome == "success":
            incidents[PROFILE_HEALTH_REPORT_COMPONENT] = (
                "Generator succeeded but did not produce its health report"
            )
        return incidents
    try:
        payload = _decode_json_container(HEALTH_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        incidents[PROFILE_HEALTH_REPORT_COMPONENT] = f"Unable to read health report: {exc}"
        return incidents
    if not isinstance(payload, dict):
        incidents[PROFILE_HEALTH_REPORT_COMPONENT] = (
            "Unable to read health report: expected a JSON object"
        )
        return incidents
    raw_items = payload.get("incidents")
    if raw_items is None:
        return incidents
    if not isinstance(raw_items, list):
        incidents[PROFILE_HEALTH_REPORT_COMPONENT] = (
            "Unable to read health report: incidents must be a JSON array"
        )
        return incidents
    for raw_item in cast(list[object], raw_items):
        if not isinstance(raw_item, dict):
            continue
        item = cast(JsonObject, raw_item)
        component = str(item.get("component") or "").strip()
        message = str(item.get("message") or "").strip()
        if component and message:
            incidents[component] = message
    return incidents


def _workflow_outcomes(generate_outcome: str) -> dict[str, str]:
    return {
        "Published branch preparation": os.environ.get("PROFILE_PREPARE_OUTCOME", ""),
        "README generation": generate_outcome,
        "Published snapshot verification": os.environ.get("PROFILE_VERIFY_OUTCOME", ""),
        "Published profile publication": os.environ.get("PROFILE_COMMIT_OUTCOME", ""),
        "GitLab repository mirroring": os.environ.get("PROFILE_MIRROR_OUTCOME", ""),
    }


def _add_failed_workflow_steps(
    incidents: dict[str, str],
    outcomes: dict[str, str],
) -> None:
    for component, outcome in outcomes.items():
        normalized = outcome.strip().lower()
        if normalized in {"failure", "cancelled"}:
            incidents[component] = f"Workflow step ended with status: {normalized}"


def load_incidents() -> list[Incident]:
    generate_outcome = os.environ.get("PROFILE_GENERATE_OUTCOME", "").strip()
    incidents = _health_report_incidents(generate_outcome)
    _add_failed_workflow_steps(incidents, _workflow_outcomes(generate_outcome))
    return [
        Incident(component=component, message=message)
        for component, message in sorted(incidents.items())
    ]



def ensure_label() -> None:
    encoded = urllib.parse.quote(LABEL, safe="")
    status, _ = api("GET", f"/labels/{encoded}", allow_status=(404,))
    if status == 404:
        status, _ = api(
            "POST",
            "/labels",
            {
                "name": LABEL,
                "color": "116466",
                "description": "Automatically managed Profile README health incident",
            },
            allow_status=(422,),
        )
        if status not in {201, 422}:
            raise RuntimeError(f"Unable to ensure {LABEL!r} label")


def open_health_issue() -> JsonObject | None:
    """Return the single managed open incident, preventing duplicate issues."""
    encoded = urllib.parse.quote(LABEL, safe="")
    _, result = api(
        "GET",
        f"/issues?state=open&labels={encoded}&per_page=100",
    )
    if not isinstance(result, list):
        return None
    for raw_issue in result:
        if not isinstance(raw_issue, dict):
            continue
        issue = cast(JsonObject, raw_issue)
        if issue.get("pull_request"):
            continue
        title = issue.get("title")
        if isinstance(title, str) and title in {TITLE_DEGRADED, TITLE_FAILED}:
            return issue
    return None


def run_url() -> str:
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    return f"https://github.com/{REPOSITORY}/actions/runs/{run_id}" if run_id else ""


def incident_title(incidents: list[Incident]) -> str:
    components = {item["component"] for item in incidents}
    return TITLE_FAILED if components & PIPELINE_COMPONENTS else TITLE_DEGRADED


def incident_fingerprint(incidents: list[Incident]) -> str:
    """Stable identity for the current incident set, used to avoid noisy comments."""
    normalized = json.dumps(incidents, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def body_fingerprint(body: object) -> str:
    if not isinstance(body, str):
        return ""
    marker = "<!-- profile-health-fingerprint:"
    start = body.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = body.find(" -->", start)
    return body[start:end].strip() if end >= 0 else ""


def build_body(incidents: list[Incident]) -> str:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    url = run_url()
    sha = os.environ.get("GITHUB_SHA", "").strip()
    ref = os.environ.get("GITHUB_REF_NAME", "").strip()
    fingerprint = incident_fingerprint(incidents)
    lines = [
        f"<!-- profile-health-fingerprint:{fingerprint} -->",
        "This issue is managed automatically by the Profile README workflow.",
        "",
        "## Detected problems",
        "",
    ]
    for item in incidents:
        component = item["component"].replace("\n", " ")
        message = item["message"].replace("\n", " ")
        lines.append(f"- **{component}** — {message}")
    lines.extend(["", "## Run", ""])
    if url:
        lines.append(f"- Workflow: [open run]({url})")
    if sha:
        lines.append(f"- Commit: `{sha[:12]}`")
    if ref:
        lines.append(f"- Ref: `{ref}`")
    lines.extend(
        [
            f"- Detected: `{now}`",
            "",
            (
                "The public README keeps the last valid component when possible; "
                "otherwise the affected card or section is hidden until recovery."
            ),
        ]
    )
    return "\n".join(lines)


def create_issue(title: str, body: str) -> None:
    payload: JsonObject = {
        "title": title,
        "body": body,
        "labels": [LABEL],
        "assignees": [OWNER],
    }
    status, _ = api("POST", "/issues", payload, allow_status=(422,))
    if status == 422:
        payload.pop("assignees", None)
        api("POST", "/issues", payload)


def _issue_number(issue: JsonObject) -> int:
    number = issue.get("number")
    if isinstance(number, bool) or not isinstance(number, int):
        raise TypeError("Managed Profile Health issue is missing a numeric number")
    return number


def recover(issue: JsonObject) -> None:
    """Mark recovery visibly, then close the previously managed incident issue."""
    number = _issue_number(issue)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    url = run_url()
    message = f"Recovered automatically on `{now}`."
    if url:
        message += f" [Workflow run]({url})"
    api("POST", f"/issues/{number}/comments", {"body": message})
    api("PATCH", f"/issues/{number}", {"state": "closed"})
    print(f"Closed recovered Profile Health issue #{number}.")


def main() -> None:
    incidents = load_incidents()
    ensure_label()
    issue = open_health_issue()

    if incidents:
        title = incident_title(incidents)
        body = build_body(incidents)
        if issue:
            number = _issue_number(issue)
            old_fingerprint = body_fingerprint(issue.get("body"))
            new_fingerprint = incident_fingerprint(incidents)
            if old_fingerprint != new_fingerprint:
                changed = ", ".join(item["component"] for item in incidents)
                message = f"Profile health incident set changed: **{changed}**."
                url = run_url()
                if url:
                    message += f" [Workflow run]({url})"
                api("POST", f"/issues/{number}/comments", {"body": message})
            api("PATCH", f"/issues/{number}", {"title": title, "body": body})
            print(f"Updated Profile Health issue #{number}.")
        else:
            create_issue(title, body)
            print("Created Profile Health issue.")
        return

    if issue:
        recover(issue)
    else:
        print("Profile health is clean; no open incident issue.")


if __name__ == "__main__":
    main()
