# SPDX-FileCopyrightText: 2024-2026 ODA Alexandre
# SPDX-License-Identifier: EUPL-1.2+

"""Maintain forge-specific Profile Health incidents as GitLab issues.

GitLab is the operational source of truth for this project, so both GitHub
Actions and GitLab CI report their own health into GitLab Issues. Each forge owns
one independent incident stream: at most one managed issue is open per forge,
and a healthy later run closes only that forge's issue.

The reporter also owns its project labels. Missing labels are created and
existing managed labels are reconciled to their canonical metadata.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import NamedTuple, NoReturn, TypeAlias, TypedDict, cast

JsonObject: TypeAlias = dict[str, object]
JsonArray: TypeAlias = list[object]
JsonContainer: TypeAlias = JsonObject | JsonArray | None


class Incident(TypedDict):
    component: str
    message: str


class LabelSpec(NamedTuple):
    name: str
    color: str
    description: str


FORGE = os.environ.get("PROFILE_HEALTH_FORGE", "").strip().lower()
TOKEN = os.environ.get("GITLAB_PROFILE_HEALTH_TOKEN", "").strip()
PROJECT = os.environ.get("GITLAB_PROFILE_HEALTH_PROJECT", "").strip()
API_URL = (
    os.environ.get("GITLAB_PROFILE_HEALTH_API_URL", "").strip()
    or os.environ.get("CI_API_V4_URL", "").strip()
    or "https://gitlab.com/api/v4"
).rstrip("/")
ASSIGNEE_USERNAME = os.environ.get("GITLAB_PROFILE_HEALTH_ASSIGNEE", "").strip()
HEALTH_FILE = Path(os.environ.get("PROFILE_HEALTH_FILE", ".profile-health.json"))

if FORGE not in {"github", "gitlab"}:
    raise SystemExit("PROFILE_HEALTH_FORGE must be either 'github' or 'gitlab'")
if not TOKEN:
    raise SystemExit("GITLAB_PROFILE_HEALTH_TOKEN is required")
if "/" not in PROJECT:
    raise SystemExit("GITLAB_PROFILE_HEALTH_PROJECT must use namespace/project format")
if not API_URL.startswith("https://"):
    raise SystemExit("GITLAB_PROFILE_HEALTH_API_URL must use HTTPS")

PROJECT_ID = urllib.parse.quote(PROJECT, safe="")
GLOBAL_LABEL = LabelSpec(
    "health::incident",
    "#116466",
    "Automatically managed Profile README health incident",
)
FORGE_LABELS = {
    "github": LabelSpec(
        "forge::github",
        "#24292F",
        "Profile Health incident originating from GitHub Actions",
    ),
    "gitlab": LabelSpec(
        "forge::gitlab",
        "#FC6D26",
        "Profile Health incident originating from GitLab CI",
    ),
}
PROFILE_HEALTH_REPORT_COMPONENT = "Profile health report"
PUBLISHED_PROFILE_PUBLICATION_COMPONENT = "Published profile publication"
FORGE_DISPLAY = {"github": "GitHub", "gitlab": "GitLab"}
GITHUB_PIPELINE_COMPONENTS = {
    "GitHub publication job",
    "Published branch preparation",
    "README generation",
    "Published snapshot verification",
    PUBLISHED_PROFILE_PUBLICATION_COMPONENT,
}
GITLAB_JOB_COMPONENTS = {
    "verify_signature": "Source signature verification",
    "test_secrets": "Secret scanning",
    "publish_profile": PUBLISHED_PROFILE_PUBLICATION_COMPONENT,
    "mirror_source_to_github": "GitHub source mirror",
}
GITLAB_PIPELINE_COMPONENTS = set(GITLAB_JOB_COMPONENTS.values())


def fail(message: str) -> NoReturn:
    raise RuntimeError(message)


def _decode_json_container(raw: bytes | str) -> JsonContainer:
    """Decode JSON without leaking ``Any`` into strict type checking."""
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
    """Call GitLab's API without exposing the private token in logs or URLs."""
    data = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{API_URL}{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "PRIVATE-TOKEN": TOKEN,
            "User-Agent": "oda-alexandre-profile-health",
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
            f"GitLab API HTTP {exc.code} for {method} {path}: {raw[:600]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"GitLab API request failed for {method} {path}: {exc.reason}"
        ) from exc


def project_path(suffix: str) -> str:
    return f"/projects/{PROJECT_ID}{suffix}"


def ensure_label(spec: LabelSpec) -> None:
    """Create or reconcile one automation-owned project label."""
    encoded = urllib.parse.quote(spec.name, safe="")
    status, result = api(
        "GET",
        project_path(f"/labels/{encoded}?include_ancestor_groups=false"),
        allow_status=(404,),
    )
    payload: JsonObject = {
        "color": spec.color,
        "description": spec.description,
    }
    if status == 404:
        payload["name"] = spec.name
        api("POST", project_path("/labels"), payload)
        print(f"Created managed label {spec.name!r}.")
        return
    if not isinstance(result, dict):
        fail(f"GitLab returned an invalid label payload for {spec.name!r}")
    current = result
    current_color = str(current.get("color") or "").upper()
    current_description = str(current.get("description") or "")
    if current_color != spec.color.upper() or current_description != spec.description:
        api("PUT", project_path(f"/labels/{encoded}"), payload)
        print(f"Reconciled managed label {spec.name!r}.")


def ensure_labels() -> tuple[str, str]:
    forge_label = FORGE_LABELS[FORGE]
    ensure_label(GLOBAL_LABEL)
    ensure_label(forge_label)
    return GLOBAL_LABEL.name, forge_label.name


def _health_report_incidents(*, expect_report: bool) -> dict[str, str]:
    incidents: dict[str, str] = {}
    if not HEALTH_FILE.exists():
        if expect_report:
            incidents[PROFILE_HEALTH_REPORT_COMPONENT] = (
                "Generator succeeded but did not produce its health report"
            )
        return incidents
    try:
        payload = _decode_json_container(HEALTH_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        incidents[PROFILE_HEALTH_REPORT_COMPONENT] = (
            f"Unable to read health report: {exc}"
        )
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


def _add_failed_outcomes(
    incidents: dict[str, str],
    outcomes: dict[str, str],
    *,
    noun: str,
) -> None:
    for component, outcome in outcomes.items():
        normalized = outcome.strip().lower()
        if normalized in {"failure", "failed", "cancelled", "canceled"}:
            incidents[component] = f"{noun} ended with status: {normalized}"


def _github_incidents() -> list[Incident]:
    generate_outcome = os.environ.get("PROFILE_GENERATE_OUTCOME", "").strip()
    incidents = _health_report_incidents(expect_report=generate_outcome == "success")
    outcomes = {
        "GitHub publication job": os.environ.get("PROFILE_WORKFLOW_OUTCOME", ""),
        "Published branch preparation": os.environ.get("PROFILE_PREPARE_OUTCOME", ""),
        "README generation": generate_outcome,
        "Published snapshot verification": os.environ.get("PROFILE_VERIFY_OUTCOME", ""),
        PUBLISHED_PROFILE_PUBLICATION_COMPONENT: os.environ.get(
            "PROFILE_COMMIT_OUTCOME", ""
        ),
    }
    _add_failed_outcomes(incidents, outcomes, noun="Workflow step")
    return _sorted_incidents(incidents)


GITLAB_TERMINAL_JOB_STATES = {"success", "failed", "canceled", "skipped", "manual"}
GITLAB_RELEASE_TAG_RE = re.compile(
    r"^v\d+\.\d+\.\d+([-.][0-9A-Za-z.-]+)?$",
    re.ASCII,
)


def _expected_gitlab_jobs() -> set[str]:
    source = os.environ.get("CI_PIPELINE_SOURCE", "").strip()
    tag = os.environ.get("CI_COMMIT_TAG", "").strip()
    branch = os.environ.get("CI_COMMIT_BRANCH", "").strip()
    expected = {"verify_signature"}
    if source != "schedule":
        expected.add("test_secrets")
    if not tag:
        expected.add("publish_profile")
    if source == "push" and (branch == "dev" or (tag and GITLAB_RELEASE_TAG_RE.fullmatch(tag))):
        expected.add("mirror_source_to_github")
    return expected


def _pipeline_jobs(pipeline_id: str) -> dict[str, tuple[str, int]]:
    query = urllib.parse.urlencode({"include_retried": "false", "per_page": "100"})
    _, result = api(
        "GET",
        project_path(f"/pipelines/{pipeline_id}/jobs?{query}"),
    )
    if not isinstance(result, list):
        fail("GitLab pipeline jobs endpoint returned an invalid payload")
    jobs: dict[str, tuple[str, int]] = {}
    for raw_job in result:
        if not isinstance(raw_job, dict):
            continue
        job = cast(JsonObject, raw_job)
        name = job.get("name")
        status = job.get("status")
        job_id = job.get("id")
        if (
            isinstance(name, str)
            and isinstance(status, str)
            and isinstance(job_id, int)
            and not isinstance(job_id, bool)
        ):
            jobs[name] = (status.lower(), job_id)
    return jobs


def _gitlab_jobs_finished(
    expected: set[str],
    jobs: dict[str, tuple[str, int]],
) -> bool:
    """Return whether every expected job exists and reached a terminal state."""
    return expected.issubset(jobs) and all(
        jobs[name][0] in GITLAB_TERMINAL_JOB_STATES for name in expected
    )


def _gitlab_job_outcomes(
    expected: set[str],
    jobs: dict[str, tuple[str, int]],
) -> tuple[dict[str, str], int | None]:
    """Normalize GitLab job states and retain the successful publish job id."""
    outcomes: dict[str, str] = {}
    publish_job_id: int | None = None
    for name in expected:
        component = GITLAB_JOB_COMPONENTS[name]
        job = jobs.get(name)
        if job is None:
            outcomes[component] = "missing"
            continue
        status, job_id = job
        outcomes[component] = (
            status if status in GITLAB_TERMINAL_JOB_STATES else f"stalled:{status}"
        )
        if name == "publish_profile" and status == "success":
            publish_job_id = job_id
    return outcomes, publish_job_id


def _wait_for_gitlab_jobs() -> tuple[dict[str, str], int | None]:
    pipeline_id = os.environ.get("CI_PIPELINE_ID", "").strip()
    if not pipeline_id.isdigit():
        fail("CI_PIPELINE_ID is required for GitLab Profile Health reporting")
    expected = _expected_gitlab_jobs()
    deadline = time.monotonic() + 600
    latest = _pipeline_jobs(pipeline_id)

    while not _gitlab_jobs_finished(expected, latest) and time.monotonic() < deadline:
        time.sleep(5)
        latest = _pipeline_jobs(pipeline_id)

    return _gitlab_job_outcomes(expected, latest)


def _download_gitlab_health_artifact(job_id: int) -> bool:
    artifact_path = urllib.parse.quote(".profile-health.gitlab.json", safe="")
    path = project_path(f"/jobs/{job_id}/artifacts/{artifact_path}")
    request = urllib.request.Request(
        f"{API_URL}{path}",
        method="GET",
        headers={
            "PRIVATE-TOKEN": TOKEN,
            "User-Agent": "oda-alexandre-profile-health",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            HEALTH_FILE.write_bytes(response.read())
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitLab API HTTP {exc.code} while downloading Profile Health artifact: {raw[:600]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"GitLab Profile Health artifact request failed: {exc.reason}"
        ) from exc


def _gitlab_incidents() -> list[Incident]:
    outcomes, publish_job_id = _wait_for_gitlab_jobs()
    report_available = False
    if publish_job_id is not None:
        report_available = _download_gitlab_health_artifact(publish_job_id)
    incidents = _health_report_incidents(expect_report=publish_job_id is not None)
    if publish_job_id is not None and not report_available:
        incidents[PROFILE_HEALTH_REPORT_COMPONENT] = (
            "Published profile job succeeded but its health-report artifact is missing"
        )
    for component, outcome in outcomes.items():
        if outcome.startswith("stalled:"):
            incidents[component] = (
                "CI job did not reach a terminal state within 10 minutes "
                f"(last status: {outcome.split(':', maxsplit=1)[1]})"
            )
        elif outcome == "missing":
            incidents[component] = "Expected CI job is missing from the pipeline graph"
    _add_failed_outcomes(incidents, outcomes, noun="CI job")
    return _sorted_incidents(incidents)


def _sorted_incidents(incidents: dict[str, str]) -> list[Incident]:
    return [
        Incident(component=component, message=message)
        for component, message in sorted(incidents.items())
    ]


def load_incidents() -> list[Incident]:
    return _github_incidents() if FORGE == "github" else _gitlab_incidents()


def titles() -> tuple[str, str]:
    forge = FORGE_DISPLAY[FORGE]
    return (
        f"[Profile Health][{forge}] README automation degraded",
        f"[Profile Health][{forge}] README publication failed",
    )


def incident_title(incidents: list[Incident]) -> str:
    degraded, failed = titles()
    pipeline_components = (
        GITHUB_PIPELINE_COMPONENTS if FORGE == "github" else GITLAB_PIPELINE_COMPONENTS
    )
    components = {item["component"] for item in incidents}
    return failed if components & pipeline_components else degraded


def incident_fingerprint(incidents: list[Incident]) -> str:
    normalized = json.dumps(incidents, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def managed_marker() -> str:
    return f"<!-- profile-health-managed:{FORGE} -->"


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


def run_metadata() -> tuple[str, str, str, str]:
    if FORGE == "github":
        repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
        run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
        url = f"{server}/{repository}/actions/runs/{run_id}" if repository and run_id else ""
        return (
            "Workflow",
            url,
            os.environ.get("GITHUB_SHA", "").strip(),
            os.environ.get("GITHUB_REF_NAME", "").strip(),
        )
    return (
        "Pipeline",
        os.environ.get("CI_PIPELINE_URL", "").strip(),
        os.environ.get("CI_COMMIT_SHA", "").strip(),
        os.environ.get("CI_COMMIT_REF_NAME", "").strip(),
    )


def build_body(incidents: list[Incident]) -> str:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    run_kind, url, sha, ref = run_metadata()
    fingerprint = incident_fingerprint(incidents)
    lines = [
        managed_marker(),
        f"<!-- profile-health-fingerprint:{fingerprint} -->",
        (
            "This confidential issue is managed automatically by the Profile README "
            f"health reporter for **{FORGE_DISPLAY[FORGE]}**."
        ),
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
        lines.append(f"- {run_kind}: [open {run_kind.lower()}]({url})")
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


def _issue_iid(issue: JsonObject) -> int:
    iid = issue.get("iid")
    if isinstance(iid, bool) or not isinstance(iid, int):
        raise TypeError("Managed Profile Health issue is missing a numeric iid")
    return iid


def open_health_issue(labels: tuple[str, str]) -> JsonObject | None:
    query = urllib.parse.urlencode(
        {
            "state": "opened",
            "scope": "all",
            "labels": ",".join(labels),
            "per_page": "100",
        }
    )
    _, result = api("GET", project_path(f"/issues?{query}"))
    if not isinstance(result, list):
        return None
    valid_titles = set(titles())
    for raw_issue in result:
        if not isinstance(raw_issue, dict):
            continue
        issue = cast(JsonObject, raw_issue)
        description = issue.get("description")
        title = issue.get("title")
        if isinstance(description, str) and managed_marker() in description:
            return issue
        if isinstance(title, str) and title in valid_titles:
            return issue
    return None


def resolve_assignee_id() -> int | None:
    if not ASSIGNEE_USERNAME:
        return None
    query = urllib.parse.urlencode({"username": ASSIGNEE_USERNAME, "per_page": "20"})
    _, result = api("GET", f"/users?{query}")
    if not isinstance(result, list):
        return None
    for raw_user in result:
        if not isinstance(raw_user, dict):
            continue
        user = cast(JsonObject, raw_user)
        username = user.get("username")
        user_id = user.get("id")
        if (
            isinstance(username, str)
            and username.casefold() == ASSIGNEE_USERNAME.casefold()
            and isinstance(user_id, int)
            and not isinstance(user_id, bool)
        ):
            return user_id
    print(f"WARNING: unable to resolve GitLab assignee {ASSIGNEE_USERNAME!r}.")
    return None


def create_issue(title: str, body: str, labels: tuple[str, str]) -> None:
    payload: JsonObject = {
        "title": title,
        "description": body,
        "labels": ",".join(labels),
        "confidential": True,
    }
    assignee_id = resolve_assignee_id()
    if assignee_id is not None:
        payload["assignee_id"] = assignee_id
    api("POST", project_path("/issues"), payload)


def update_issue(
    iid: int,
    *,
    title: str,
    body: str,
    labels: tuple[str, str],
) -> None:
    api(
        "PUT",
        project_path(f"/issues/{iid}"),
        {
            "title": title,
            "description": body,
            "labels": ",".join(labels),
            "confidential": True,
        },
    )


def add_note(iid: int, body: str) -> None:
    api("POST", project_path(f"/issues/{iid}/notes"), {"body": body})


def recover(issue: JsonObject) -> None:
    iid = _issue_iid(issue)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    run_kind, url, _, _ = run_metadata()
    message = f"Recovered automatically on `{now}`."
    if url:
        message += f" [{run_kind}]({url})"
    add_note(iid, message)
    api("PUT", project_path(f"/issues/{iid}"), {"state_event": "close"})
    print(f"Closed recovered {FORGE_DISPLAY[FORGE]} Profile Health issue #{iid}.")


def main() -> None:
    labels = ensure_labels()
    incidents = load_incidents()
    issue = open_health_issue(labels)

    if incidents:
        title = incident_title(incidents)
        body = build_body(incidents)
        if issue:
            iid = _issue_iid(issue)
            old_fingerprint = body_fingerprint(issue.get("description"))
            new_fingerprint = incident_fingerprint(incidents)
            if old_fingerprint != new_fingerprint:
                changed = ", ".join(item["component"] for item in incidents)
                message = f"Profile Health incident set changed: **{changed}**."
                run_kind, url, _, _ = run_metadata()
                if url:
                    message += f" [{run_kind}]({url})"
                add_note(iid, message)
            update_issue(iid, title=title, body=body, labels=labels)
            print(f"Updated {FORGE_DISPLAY[FORGE]} Profile Health issue #{iid}.")
        else:
            create_issue(title, body, labels)
            print(f"Created {FORGE_DISPLAY[FORGE]} Profile Health issue.")
        return

    if issue:
        recover(issue)
    else:
        print(f"{FORGE_DISPLAY[FORGE]} Profile Health is clean; no open incident issue.")


if __name__ == "__main__":
    main()
