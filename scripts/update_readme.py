#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024-2026 ODA Alexandre
# SPDX-License-Identifier: EUPL-1.2+

"""Refresh dynamic profile README data and generate local SVG cards.

No third-party Python packages are required. Forge-specific data is supplied by
the selected provider; the current GitHub publication uses the workflow's
short-lived GITHUB_TOKEN. Reviewed external-platform adapters receive credentials,
such as HTB and HackerOne tokens, only through repository Actions secrets.

Architecture contract
---------------------
Before changing layout, typography, card types, conditional rendering or
fallback behaviour, read ``docs/PROFILE_README_ARCHITECTURE.md``. That file is
the source of truth for the visual/component rules intentionally shared by all
current and future sections. Keep implementation comments focused on *why* a
rule exists rather than restating the SVG/HTML syntax.
"""

from __future__ import annotations

import base64
import datetime as dt
from dataclasses import dataclass
import html
from html.parser import HTMLParser
import hashlib
import json
import math
import os
import re
import tomllib
import textwrap
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlencode, urlparse
from typing import Any, Callable

from providers import load_provider
from providers.base import (
    ContributionDay,
    ContributionSnapshot,
    ForgeStatsSnapshot,
    JsonArray,
    JsonContainer,
    JsonObject,
    ProfileSnapshot,
    is_json_array,
    is_json_object,
)

TEMPLATE = Path(os.environ.get("README_TEMPLATE", "README.template"))
README = Path("README.md")
CONFIG = Path(os.environ.get("PROFILE_CONFIG", "profile.config.toml"))
HEALTH_FILE = Path(os.environ.get("PROFILE_HEALTH_FILE", ".profile-health.json"))
ASSET_DIR = Path("assets/generated")
PROFILE_COLOR = "116466"  # Brand accent color, intentionally static.
SVG_WIDTH = 720
STANDARD_CARD_HEIGHT = 230
ACTIVITY_CARD_HEIGHT = 190
STATS_CARD_HEIGHT = 150
FEATURED_PROJECT_CARD_WIDTH = 340
FEATURED_PROJECT_CARD_HEIGHT = 156

JSON_MEDIA_TYPE = "application/json"
STATS_PERIOD_LABEL = "· 365d"
SLUG_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")
SECURITY_RESEARCH_CONFIG_COMPONENT = "Security Research configuration"
CERTIFICATIONS_CONFIG_COMPONENT = "Certifications configuration"

# Shared SVG design-system tokens. These are visual invariants: cards may
# change their content/layout, but must reuse this frame, padding and spacing
# unless docs/PROFILE_README_ARCHITECTURE.md explicitly defines an exception.
CARD_RADIUS = 12
CARD_GLOW_INSET = 5
CARD_PADDING_X = 30
CARD_PADDING_Y = 30
CARD_DIVIDER_INSET_RATIO = 0.14
SPACE_SM = 10

# Workflow-specific layout tokens. These stay separate from the shared design
# system because they describe the diagram geometry rather than card styling.
WORKFLOW_MAIN_CARD_WIDTH = 340
WORKFLOW_SECONDARY_CARD_WIDTH = 220
WORKFLOW_CARD_HEIGHT = 92
WORKFLOW_TOP = 12
WORKFLOW_TRANSITION_GAP = 40
CONNECTOR_LABEL_HEIGHT = 28
CONNECTOR_LABEL_PADDING_X = 14
CONNECTOR_LABEL_FONT_SIZE = 10.0
CONNECTOR_LABEL_LETTER_SPACING = 0.50
PUBLISH_LABEL_LINE_GAP = 12


@dataclass(frozen=True)
class SvgStyle:
    """Shared colors and visual intensities for generated SVGs."""

    bg_color: str
    text_color: str
    muted_color: str
    track_color: str
    surface_opacity: float
    border_opacity: float
    glow_opacity: float
    secondary_opacity: float
    divider_opacity: float
    muted_opacity: float


@dataclass(frozen=True)
class HackTheBoxSnapshot:
    """Minimal professional HTB Labs status used by Security Practice.

    Security Practice cards deliberately expose only one status signal and one
    progression signal. Extra gamification metrics returned by the platform are
    intentionally ignored so the README remains concise.
    """

    rank: str
    next_rank: str
    progress: float


@dataclass(frozen=True)
class CyberDefendersSnapshot:
    """Minimal CyberDefenders status used by Security Practice.

    ``rank`` comes from CyberDefenders' official public profile-badge endpoint.
    ``progress`` is a README-derived arithmetic mean of the seven public skill
    ``progress`` values returned by ``/api/user/<name>/overview/skills/``. It is
    deliberately labelled as average skill progress so it cannot be mistaken
    for an official CyberDefenders overall score.
    """

    rank: str
    progress: float


@dataclass(frozen=True)
class HackerOneDisclosure:
    """One explicitly public HackerOne disclosure suitable for a linked evidence card."""

    report_id: str
    url: str
    program: str
    title: str
    severity: str
    cwe: str
    cve: str


@dataclass(frozen=True)
class HackerOneSnapshot:
    """Minimal HackerOne research signals suitable for a public profile README.

    Reputation, Signal and Impact come from the authenticated Hacker API report
    reporter relationship. Public disclosures come only from Hacktivity entries
    explicitly marked disclosed. Financial fields and private report content are
    intentionally ignored.
    """

    reputation: float | None
    signal: float | None
    impact: float | None
    disclosures: tuple[HackerOneDisclosure, ...]

    @property
    def meaningful(self) -> bool:
        """Only expose HackerOne once it proves real research activity.

        A fresh HackerOne account starts with reputation but no earned Signal or
        Impact, so reputation alone must never make SECURITY RESEARCH visible.
        One public disclosure is independently sufficient evidence.
        """
        return (
            self.signal is not None
            or self.impact is not None
            or bool(self.disclosures)
        )


@dataclass(frozen=True)
class Section:
    """One independently renderable README section.

    ``parts`` are optional child components/cards. Empty parts are filtered by
    ``content``; if every part is empty the entire section (title, whitespace and
    separator) disappears. This same rule applies to static and dynamic sections.
    """

    key: str
    section_title: str | None
    parts: tuple[str, ...]

    @property
    def content(self) -> str:
        return "\n\n".join(
            part.strip() for part in self.parts if part and part.strip()
        )


class HealthReport:
    """Collect non-fatal incidents for the workflow's profile-health issue."""

    def __init__(self) -> None:
        self._incidents: dict[str, str] = {}

    def add(self, component: str, message: object) -> None:
        text = re.sub(r"\s+", " ", str(message)).strip()
        self._incidents[component] = text[:800]
        print(f"::warning::{component}: {text}")

    @property
    def incidents(self) -> list[dict[str, str]]:
        return [
            {"component": component, "message": message}
            for component, message in sorted(self._incidents.items())
        ]

    def write(self) -> None:
        HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": "degraded" if self._incidents else "healthy",
            "incidents": self.incidents,
        }
        HEALTH_FILE.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )


# Shared SVG visual tokens. The transparent surface and balanced text colors keep
# generated assets readable on supported repository surfaces.
SVG_STYLE = SvgStyle(
    bg_color="737a82",
    text_color="747b83",
    muted_color="747b83",
    track_color="747b83",
    surface_opacity=0.0,
    border_opacity=0.90,
    glow_opacity=0.20,
    secondary_opacity=0.58,
    divider_opacity=0.50,
    muted_opacity=0.94,
)
MAX_AVATAR_BYTES = 8 * 1024 * 1024

SECURITY_PRACTICE_CATALOG = {
    "hack_the_box": ("HACK THE BOX", "Offensive Security · Pentest"),
    "cyberdefenders": ("CYBERDEFENDERS", "Blue Team · DFIR"),
}
SECURITY_RESEARCH_CATALOG = {
    "hackerone": ("HACKERONE", "Security Research · Disclosures"),
}

# Intentional CTA URL: clicking the LinkedIn badge opens LinkedIn's public-profile
# join/connect flow instead of the ordinary profile page. Keep this static.
LINKEDIN_URL = (
    "https://www.linkedin.com/signup/public-profile-join"
    "?vieweeVanityName=oda-alexandre"
    "&trk=public_profile_top-card-primary-button-join-to-connect"
)

HTB_TOKEN = os.environ.get("HTB_TOKEN", "").strip()
HTB_API_BASE_URL = os.environ.get(
    "HTB_API_BASE_URL", "https://labs.hackthebox.com/api/v4"
).rstrip("/")
HTB_INVALID_USER_ID_ERROR = "HTB user/info response does not contain a valid user id"
HACKERONE_API_TOKEN = os.environ.get("HACKERONE_API_TOKEN", "").strip()
HACKERONE_API_BASE_URL = os.environ.get(
    "HACKERONE_API_BASE_URL", "https://api.hackerone.com/v1"
).rstrip("/")

PROVIDER = load_provider()
USERNAME = PROVIDER.username

def htb_json(path: str) -> JsonContainer:
    """Call the HTB Labs API without ever exposing the App Token in output.

    The Labs v4 API is not treated as a stable public contract, so callers must
    route failures through Profile Health and preserve last-good public assets.
    """
    if not HTB_TOKEN:
        raise RuntimeError("HTB_TOKEN repository secret is not configured")
    url = f"{HTB_API_BASE_URL}/{path.lstrip('/')}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": JSON_MEDIA_TYPE,
            "Authorization": f"Bearer {HTB_TOKEN}",
            "User-Agent": f"{USERNAME}-profile-readme",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"Hack The Box Labs API HTTP {exc.code} for {path}: {body}"
        ) from exc


def fetch_hack_the_box_snapshot(health: HealthReport) -> HackTheBoxSnapshot | None:
    """Fetch the authenticated HTB Labs rank and next-rank progress.

    ``GET /user/info`` resolves the token owner so no numeric HTB user id is
    stored in repository configuration. ``GET /user/profile/basic/{id}`` then
    supplies the rank fields. The API is intentionally isolated here because it
    is less stable than GitHub's documented API surface.
    """
    try:
        user_payload = htb_json("user/info")
        if not is_json_object(user_payload):
            raise RuntimeError("Unexpected HTB user/info response")
        info = user_payload.get("info")
        if not is_json_object(info):
            raise RuntimeError("HTB user/info response does not contain info")
        raw_user_id: Any = info.get("id")
        if raw_user_id is None:
            raise RuntimeError(HTB_INVALID_USER_ID_ERROR)
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                HTB_INVALID_USER_ID_ERROR
            ) from exc
        if user_id <= 0:
            raise RuntimeError(HTB_INVALID_USER_ID_ERROR)

        profile_payload = htb_json(f"user/profile/basic/{user_id}")
        if not is_json_object(profile_payload):
            raise RuntimeError("Unexpected HTB basic profile response")
        profile = profile_payload.get("profile")
        if not is_json_object(profile):
            raise RuntimeError("HTB basic profile response does not contain profile")

        rank = str(profile.get("rank") or "").strip()
        if not rank:
            raise RuntimeError("HTB basic profile response does not contain rank")
        next_rank = str(profile.get("next_rank") or "").strip()
        raw_progress: Any = profile.get("current_rank_progress")
        if raw_progress is None:
            raise RuntimeError("HTB basic profile response has invalid rank progress")
        try:
            progress = float(raw_progress)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("HTB basic profile response has invalid rank progress") from exc
        progress = max(0.0, min(100.0, progress))
        return HackTheBoxSnapshot(rank=rank, next_rank=next_rank, progress=progress)
    except Exception as exc:
        health.add("Hack The Box Labs", exc)
        return None


def _hackerone_username(profile_url: str) -> str:
    """Extract the public HackerOne handle used as the Hacker API username."""
    parsed = urlparse(profile_url)
    if parsed.netloc.casefold() not in {"hackerone.com", "www.hackerone.com"}:
        raise RuntimeError("HackerOne profile URL must use hackerone.com")
    username = parsed.path.strip("/").split("/", 1)[0].strip()
    if not username or not re.fullmatch(r"[A-Za-z0-9_-]+", username):
        raise RuntimeError("HackerOne profile URL does not contain a valid username")
    return username


def hackerone_json(
    path: str,
    *,
    username: str,
    query: dict[str, object] | None = None,
) -> JsonContainer:
    """Call the official Hacker API using username + Personal API Token Basic auth.

    The token is read only from the workflow environment. URLs and exceptions
    must never contain it, and callers intentionally request only read endpoints.
    """
    if not HACKERONE_API_TOKEN:
        raise RuntimeError("HACKERONE_API_TOKEN repository secret is not configured")

    url = f"{HACKERONE_API_BASE_URL}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{urlencode(query)}"
    credentials = base64.b64encode(
        f"{username}:{HACKERONE_API_TOKEN}".encode("utf-8")
    ).decode("ascii")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": JSON_MEDIA_TYPE,
            "Authorization": f"Basic {credentials}",
            "User-Agent": f"{USERNAME}-profile-readme",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Error bodies can contain platform-side context. Keep Profile Health
        # useful without mirroring arbitrary API content into a public issue.
        raise RuntimeError(
            f"HackerOne API HTTP {exc.code} for /{path.lstrip('/')}"
        ) from exc


def _hackerone_metric(value: object, name: str) -> float | None:
    """Validate an optional numeric HackerOne researcher metric."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"HackerOne reporter {name} is not numeric")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise RuntimeError(f"HackerOne reporter {name} is not finite")
    if name == "signal" and not -10.0 <= numeric <= 7.0:
        raise RuntimeError("HackerOne reporter signal is outside -10..7")
    if name == "impact" and not 0.0 <= numeric <= 50.0:
        raise RuntimeError("HackerOne reporter impact is outside 0..50")
    return numeric


def _extract_hackerone_metrics(
    report: JsonObject,
    *,
    expected_username: str,
) -> tuple[float | None, float | None, float | None]:
    """Read public researcher metrics from one authenticated report object."""
    relationships = report.get("relationships")
    if not is_json_object(relationships):
        return None, None, None
    reporter = relationships.get("reporter")
    data = reporter.get("data") if is_json_object(reporter) else None
    attributes = data.get("attributes") if is_json_object(data) else None
    if not is_json_object(attributes):
        return None, None, None

    reporter_username = str(attributes.get("username") or "").strip()
    if (
        reporter_username
        and reporter_username.casefold() != expected_username.casefold()
    ):
        raise RuntimeError("HackerOne report reporter does not match configured profile")

    return (
        _hackerone_metric(attributes.get("reputation"), "reputation"),
        _hackerone_metric(attributes.get("signal"), "signal"),
        _hackerone_metric(attributes.get("impact"), "impact"),
    )


def _fetch_hackerone_metrics(
    username: str,
) -> tuple[float | None, float | None, float | None]:
    """Fetch current Reputation/Signal/Impact without exposing private report data."""
    payload = hackerone_json(
        "hackers/me/reports",
        username=username,
        query={"page[size]": 1},
    )
    reports = payload.get("data") if is_json_object(payload) else None
    if not is_json_array(reports):
        raise RuntimeError("Unexpected HackerOne reports response")
    if not reports:
        # An empty account is healthy. It simply has no research signal to show.
        return None, None, None

    first = reports[0]
    if not is_json_object(first):
        raise RuntimeError("HackerOne reports response contains an invalid report")
    metrics = _extract_hackerone_metrics(first, expected_username=username)
    if any(value is not None for value in metrics):
        return metrics

    report_id = str(first.get("id") or "").strip()
    if not report_id.isdigit():
        raise RuntimeError("HackerOne report does not contain a valid id")
    detail_payload = hackerone_json(
        f"hackers/reports/{report_id}",
        username=username,
    )
    report = detail_payload.get("data") if is_json_object(detail_payload) else None
    if not is_json_object(report):
        raise RuntimeError("Unexpected HackerOne report detail response")
    return _extract_hackerone_metrics(report, expected_username=username)


def _hackerone_first_text(value: object) -> str:
    """Return the first useful textual label from one optional HackerOne field."""
    if isinstance(value, str):
        return value.strip()
    if is_json_array(value):
        for item in value:
            text = _hackerone_first_text(item)
            if text:
                return text
        return ""
    if is_json_object(value):
        for key in ("id", "name", "value"):
            text = _hackerone_first_text(value.get(key))
            if text:
                return text
    return ""



def _hackerone_relationship_attributes(
    relationships: JsonObject,
    relation: str,
) -> JsonObject:
    relationship = relationships.get(relation)
    data = relationship.get("data") if is_json_object(relationship) else None
    attributes = data.get("attributes") if is_json_object(data) else None
    return attributes if is_json_object(attributes) else {}


def _validate_hackerone_reporter(relationships: JsonObject, username: str) -> None:
    reporter_attrs = _hackerone_relationship_attributes(relationships, "reporter")
    reporter_username = str(reporter_attrs.get("username") or "").strip()
    if reporter_username.casefold() != username.casefold():
        raise RuntimeError(
            "HackerOne Hacktivity reporter does not match configured profile"
        )


def _hackerone_public_url(attributes: JsonObject) -> str:
    url = valid_external_url(attributes.get("url"))
    allowed_hosts = {"hackerone.com", "www.hackerone.com"}
    if not url or urlparse(url).netloc.casefold() not in allowed_hosts:
        raise RuntimeError(
            "HackerOne public disclosure does not contain a valid URL"
        )
    return url


def _hackerone_disclosure_from_item(
    item: object,
    username: str,
) -> HackerOneDisclosure:
    if not is_json_object(item):
        raise RuntimeError("HackerOne Hacktivity response contains an invalid item")
    attributes = item.get("attributes")
    relationships = item.get("relationships")
    if not is_json_object(attributes) or not is_json_object(relationships):
        raise RuntimeError("HackerOne disclosure is missing attributes or relationships")
    if attributes.get("disclosed") is not True:
        raise RuntimeError("HackerOne Hacktivity returned a non-public item")

    _validate_hackerone_reporter(relationships, username)
    url = _hackerone_public_url(attributes)
    report_id = str(item.get("id") or "").strip()
    if not report_id:
        raise RuntimeError("HackerOne public disclosure is missing its report id")

    program_attrs = _hackerone_relationship_attributes(relationships, "program")
    program_name = str(
        program_attrs.get("name") or program_attrs.get("handle") or ""
    ).strip()
    title = str(attributes.get("title") or "").strip()
    if not program_name or not title:
        raise RuntimeError(
            "HackerOne public disclosure is missing its program or title"
        )

    return HackerOneDisclosure(
        report_id=report_id,
        url=url,
        program=program_name,
        title=title,
        severity=str(attributes.get("severity_rating") or "").strip().upper(),
        cwe=_hackerone_first_text(attributes.get("cwe")),
        cve=_hackerone_first_text(attributes.get("cve_ids")),
    )


def _fetch_hackerone_disclosures(
    username: str,
    *,
    limit: int = 6,
) -> tuple[HackerOneDisclosure, ...]:
    """Fetch recent explicitly public disclosures for the configured researcher."""
    payload = hackerone_json(
        "hackers/hacktivity",
        username=username,
        query={
            "queryString": f"reporter:{username} AND disclosed:true",
            "sort": "-disclosed_at",
            "page[size]": max(1, min(limit, 6)),
        },
    )
    items = payload.get("data") if is_json_object(payload) else None
    if not is_json_array(items):
        raise RuntimeError("Unexpected HackerOne Hacktivity response")
    return tuple(
        _hackerone_disclosure_from_item(item, username)
        for item in items[:limit]
    )



def fetch_hackerone_snapshot(
    profile_url: str,
    health: HealthReport,
) -> HackerOneSnapshot | None:
    """Fetch HackerOne research signals with clean empty-account semantics.

    Successful empty responses are not incidents: the section remains hidden
    until Signal/Impact exists or a public disclosure is available. Any API,
    authentication or schema failure goes to Profile Health and returns ``None``
    so main() can preserve the complete last-good SECURITY RESEARCH section.
    """
    try:
        username = _hackerone_username(profile_url)
    except Exception as exc:
        health.add("HackerOne profile", exc)
        return None

    try:
        reputation, signal, impact = _fetch_hackerone_metrics(username)
    except Exception as exc:
        health.add("HackerOne reports", exc)
        return None

    try:
        disclosures = _fetch_hackerone_disclosures(username)
    except Exception as exc:
        health.add("HackerOne Hacktivity", exc)
        return None

    return HackerOneSnapshot(
        reputation=reputation,
        signal=signal,
        impact=impact,
        disclosures=disclosures,
    )


class _VisibleTextParser(HTMLParser):
    """Extract text nodes while suppressing non-visible script/style payloads."""

    _SUPPRESSED_ELEMENTS = frozenset({"script", "style"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._suppressed_depth = 0
        self.text_chunks: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs  # Required by HTMLParser's override contract; attributes are irrelevant here.
        if tag.casefold() in self._SUPPRESSED_ELEMENTS:
            self._suppressed_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if (
            tag.casefold() in self._SUPPRESSED_ELEMENTS
            and self._suppressed_depth > 0
        ):
            self._suppressed_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._suppressed_depth == 0:
            self.text_chunks.append(data)


def _visible_html_lines(document: str) -> list[str]:
    """Return visible-ish HTML text lines without regex-based HTML filtering.

    HTML is parsed with the standard library so script/style payloads are never
    treated as visible profile text. This helper is intentionally an extractor,
    not a sanitizer: callers receive normalized text only, never rewritten HTML.
    """
    parser = _VisibleTextParser()
    parser.feed(document)
    parser.close()

    lines: list[str] = []
    for chunk in parser.text_chunks:
        for raw_line in chunk.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if line:
                lines.append(line)
    return lines


CYBERDEFENDERS_SKILL_NAMES = (
    "Endpoint Forensics",
    "Threat Hunting",
    "Network Forensics",
    "Malware Analysis",
    "Threat Intel",
    "Cloud Forensics",
    "Detection Engineering",
)


def _cyberdefenders_username(profile_url: str) -> str:
    """Extract and validate the public profile username used in API paths."""
    parsed = urlparse(profile_url)
    username = parsed.path.rstrip("/").split("/")[-1].strip()
    if not username or not re.fullmatch(r"[A-Za-z0-9_.-]+", username):
        raise RuntimeError("CyberDefenders profile URL does not contain a valid username")
    return username


def _fetch_text(url: str, *, accept: str) -> str:
    """Fetch one public UTF-8 resource with the profile generator user agent."""
    request = urllib.request.Request(
        url,
        headers={"Accept": accept, "User-Agent": f"{USERNAME}-profile-readme"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"CyberDefenders HTTP {exc.code}: {body}") from exc


def _fetch_cyberdefenders_rank(profile_url: str) -> str:
    """Read the public skill rank from CyberDefenders' share-badge endpoint."""
    username = _cyberdefenders_username(profile_url)
    badge_url = f"https://cyberdefenders.org/p/{username}/badge"
    script = _fetch_text(badge_url, accept="application/javascript,text/javascript,*/*")
    encoded_match = re.search(r'window\.atob\("([A-Za-z0-9+/=]+)"\)', script)
    if not encoded_match:
        raise RuntimeError("CyberDefenders badge payload was not found")
    try:
        document = base64.b64decode(encoded_match.group(1), validate=True).decode(
            "utf-8", errors="replace"
        )
    except Exception as exc:
        raise RuntimeError("CyberDefenders badge payload is not valid Base64") from exc

    lines = _visible_html_lines(document)
    username_index = next(
        (i for i, line in enumerate(lines) if line.casefold() == username.casefold()),
        None,
    )
    if username_index is None:
        raise RuntimeError("CyberDefenders badge username was not found")

    for line in lines[username_index + 1 : username_index + 6]:
        if re.fullmatch(r"[A-Za-z][A-Za-z ._-]{1,31}", line):
            return line.strip()
    raise RuntimeError("CyberDefenders public skill rank was not found")


def _fetch_cyberdefenders_progress(profile_url: str) -> float:
    """Return mean public skill progress across the seven radar dimensions.

    CyberDefenders exposes per-domain ``progress`` and ``accuracy`` values, not
    one official overall percentage. The profile README intentionally averages
    only ``progress``; accuracy is not mixed into the score or displayed.
    Requiring the known seven dimensions prevents a partial API response from
    silently changing the denominator and producing a misleading percentage.
    """
    username = _cyberdefenders_username(profile_url)
    endpoint = f"https://cyberdefenders.org/api/user/{username}/overview/skills/"
    payload = json.loads(_fetch_text(endpoint, accept=JSON_MEDIA_TYPE))
    skills = payload.get("skills") if is_json_object(payload) else None
    if not is_json_object(skills):
        raise RuntimeError("CyberDefenders skills payload has no skills object")

    progress_values: list[float] = []
    for skill_name in CYBERDEFENDERS_SKILL_NAMES:
        skill = skills.get(skill_name)
        if not is_json_object(skill):
            raise RuntimeError(f"CyberDefenders skills payload is missing {skill_name}")
        value = skill.get("progress")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError(
                f"CyberDefenders progress for {skill_name} is not numeric"
            )
        numeric = float(value)
        if not 0.0 <= numeric <= 100.0:
            raise RuntimeError(
                f"CyberDefenders progress for {skill_name} is outside 0..100"
            )
        progress_values.append(numeric)

    return sum(progress_values) / len(progress_values)


def _cached_cyberdefenders_value(kind: str) -> str | float | None:
    """Recover one last-good field from the previously published SVG card.

    ``main`` is checked out as the publication worktree before generation, so
    the previous generated SVG remains available. This lets rank and progress
    refresh independently: if one CyberDefenders endpoint fails, the healthy
    field can still update while the failed field retains its last valid value.
    """
    path = generated_asset_path("security-practice-cyberdefenders")
    if not path.exists():
        return None
    try:
        document = html.unescape(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    if kind == "progress":
        match = re.search(r">(\d{1,3}(?:\.\d)?)% avg\. skill progress</text>", document)
        if match:
            return float(match.group(1))
    elif kind == "rank":
        match = re.search(
            r'<text class="status-value"[^>]*>([^<]+)</text>', document
        )
        if match:
            return match.group(1).strip()
    return None


def fetch_cyberdefenders_snapshot(
    profile_url: str, health: HealthReport
) -> CyberDefendersSnapshot | None:
    """Fetch CyberDefenders rank + public skill progress with field-level fallback.

    The rank comes from the official share badge and progress from the public
    JSON endpoint used by the profile's ``Progress & Accuracy`` radar. Each field
    has its own last-good fallback. A source failure still reaches Profile Health
    even when cached data keeps the public card valid.
    """
    rank: str | None = None
    progress: float | None = None

    try:
        rank = _fetch_cyberdefenders_rank(profile_url)
    except Exception as exc:
        health.add("CyberDefenders rank", exc)
        cached_rank = _cached_cyberdefenders_value("rank")
        if isinstance(cached_rank, str) and cached_rank:
            rank = cached_rank

    try:
        progress = _fetch_cyberdefenders_progress(profile_url)
    except Exception as exc:
        health.add("CyberDefenders progress", exc)
        cached_progress = _cached_cyberdefenders_value("progress")
        if isinstance(cached_progress, (int, float)):
            progress = float(cached_progress)

    if rank is None or progress is None:
        return None
    return CyberDefendersSnapshot(rank=rank, progress=progress)


def replace_block(text: str, start: str, end: str, body: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    replacement = f"{start}\n{body}\n{end}"
    updated, count = pattern.subn(lambda _: replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"Missing or duplicated marker pair: {start} / {end}")
    return updated


def block_body(text: str, start: str, end: str) -> str:
    match = re.search(re.escape(start) + r"(.*?)" + re.escape(end), text, re.S)
    if not match:
        return ""
    return match.group(1).strip("\n")


def asset_version(path: Path) -> str:
    """Return a short content hash for deterministic cache busting."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def versioned_asset_url(path: Path) -> str:
    """Return a repository-relative asset URL versioned by its file contents."""
    return f"./{path.as_posix()}?v={asset_version(path)}"


def replace_reference(text: str, name: str, url: str) -> str:
    """Replace one Markdown reference definition while preserving the template."""
    pattern = re.compile(rf"^\[{re.escape(name)}\]:\s+\S+\s*$", re.M)
    updated, count = pattern.subn(f"[{name}]: {url}", text, count=1)
    if count != 1:
        raise RuntimeError(f"Missing or duplicated Markdown reference: {name}")
    return updated


def download_public_image(url: str) -> tuple[bytes, str]:
    """Download a small public image without forwarding the GitHub token."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,*/*;q=0.8",
            "User-Agent": f"{USERNAME}-profile-readme",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        content_type = (response.headers.get_content_type() or "").lower()
        data = response.read(MAX_AVATAR_BYTES + 1)

    if len(data) > MAX_AVATAR_BYTES:
        raise RuntimeError("GitHub avatar exceeds the configured size limit")
    if not data:
        raise RuntimeError("GitHub avatar download returned an empty body")

    allowed_types = {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/avif",
    }
    if content_type not in allowed_types:
        raise RuntimeError(
            f"Unexpected GitHub avatar content type: {content_type or 'unknown'}"
        )
    return data, content_type


def write_avatar_svg(avatar_url: str) -> Path:
    """Embed the current GitHub avatar in a transparent SVG."""
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    avatar_bytes, mime_type = download_public_image(avatar_url)
    encoded = base64.b64encode(avatar_bytes).decode("ascii")

    def build_svg() -> str:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" '
            'viewBox="0 0 200 200" role="img" aria-label="GitHub avatar">\n'
            f'  <image x="0" y="0" width="200" height="200" '
            f'preserveAspectRatio="xMidYMid meet" href="data:{mime_type};base64,{encoded}"/>\n'
            '</svg>\n'
        )

    return write_svg(
        "avatar",
        lambda _theme: build_svg(),
    )


def build_avatar_block(
    avatar_url: str,
    display_name: object,
    *,
    health: HealthReport,
    active_assets: set[str],
) -> str:
    """Generate the avatar, preserving the last valid local SVG on failure."""
    avatar_alt = html.escape(f"{display_name} GitHub avatar", quote=True)
    avatar_title = html.escape(str(display_name), quote=True)
    path = generated_asset_path("avatar")

    try:
        path = write_avatar_svg(avatar_url)
    except Exception as exc:
        health.add("GitHub avatar", exc)
        if not path.exists():
            escaped_url = html.escape(str(avatar_url), quote=True)
            return (
                f'<img src="{escaped_url}" width="200" height="200" '
                f'alt="{avatar_alt}" title="{avatar_title}">'
            )

    active_assets.add(path.name)
    asset_url = versioned_asset_url(path)
    return (
        f'<img src="{asset_url}" width="200" height="200" '
        f'alt="{avatar_alt}" title="{avatar_title}">'
    )

def normalize_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if not parsed.scheme:
        value = "https://" + value
    return value.rstrip("/")


def current_reference(text: str, name: str) -> str:
    match = re.search(rf"^\[{re.escape(name)}\]:\s+(\S+)", text, re.M)
    return match.group(1) if match else ""


def derive_gitlab_username(text: str, social_accounts: list[JsonObject]) -> str:
    # Prefer a GitLab social link exposed by GitHub, if configured.
    candidates = [normalize_url(a.get("url") or "") for a in social_accounts]
    # Fall back to the last known GitLab link in the README.
    candidates.append(current_reference(text, "gitlab_url"))

    for candidate in candidates:
        if not candidate:
            continue
        parsed = urlparse(candidate)
        if parsed.netloc.lower() not in {"gitlab.com", "www.gitlab.com"}:
            continue
        segments = [segment for segment in parsed.path.split("/") if segment]
        if segments:
            return segments[0]

    # Both public profiles currently use the same username. This also makes a
    # fresh repository work before the first generated README exists.
    return USERNAME


def svg_escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def svg_typography_css(style: SvgStyle) -> str:
    """Return semantic typography roles shared by every generated SVG card.

    Choose a role by meaning, never by whichever size happens to look right:
    ``card-title`` / ``card-description`` are narrative card copy;
    ``metric-value`` / ``metric-label`` are centered numeric KPI pairs;
    ``status-value`` is a prominent textual state such as a platform rank;
    ``data-label`` / ``data-meta`` belong to charts where alignment follows data;
    ``meta`` is tertiary context; ``action-label`` is a compact CTA/status.

    README-level ``section-title`` and ``section-lead`` are rendered by GitHub's
    Markdown/HTML rather than inside SVGs; their rules live in the architecture
    document and ``render_section_lead``.
    """
    return (
        'text { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", '
        'Roboto, Helvetica, Arial, sans-serif; }\n'
        f'.card-title {{ fill: #{style.text_color}; font-size: 15px; font-weight: 600; }}\n'
        f'.card-description {{ fill: #{style.muted_color}; font-size: 13px; font-weight: 400; }}\n'
        f'.metric-value {{ fill: #{style.text_color}; font-size: 30px; font-weight: 700; }}\n'
        f'.metric-label {{ fill: #{style.muted_color}; font-size: 13px; font-weight: 400; }}\n'
        f'.status-value {{ fill: #{style.text_color}; font-size: 17px; font-weight: 600; }}\n'
        f'.data-label {{ fill: #{style.text_color}; font-size: 15px; font-weight: 600; }}\n'
        f'.data-meta {{ fill: #{style.muted_color}; font-size: 13px; font-weight: 400; }}\n'
        f'.meta {{ fill: #{style.muted_color}; font-size: 11px; font-weight: 400; }}\n'
        f'.action-label {{ fill: #{PROFILE_COLOR}; font-size: 10px; font-weight: 700; '
        'letter-spacing: 0.65px; }\n'
        f'.connector-label {{ fill: #{PROFILE_COLOR}; font-size: {CONNECTOR_LABEL_FONT_SIZE:.1f}px; '
        f'font-weight: 700; letter-spacing: {CONNECTOR_LABEL_LETTER_SPACING:.2f}px; '
        'font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }'
    )


def svg_soft_glow_filter() -> str:
    """Return the shared glow filter definition used by profile card frames."""
    return (
        '<filter id="softGlow" x="-20%" y="-20%" width="140%" height="140%">'
        '<feGaussianBlur stdDeviation="3.6"/></filter>'
    )


def svg_card_frame(
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    style: SvgStyle,
) -> str:
    """Render the shared card frame used across profile SVGs.

    Do not create card-specific border/glow variants to compensate for layout
    issues. Fix the card geometry instead so every visible and future card keeps
    the same visual identity.
    """
    glow_x = x + CARD_GLOW_INSET
    glow_y = y + CARD_GLOW_INSET
    glow_width = max(0.0, width - (2 * CARD_GLOW_INSET))
    glow_height = max(0.0, height - (2 * CARD_GLOW_INSET))
    glow_radius = max(0.0, CARD_RADIUS - CARD_GLOW_INSET)
    return (
        f'<rect x="{glow_x}" y="{glow_y}" width="{glow_width}" height="{glow_height}" '
        f'rx="{glow_radius}" fill="none" stroke="#{PROFILE_COLOR}" stroke-width="4" '
        f'stroke-opacity="{style.glow_opacity:.2f}" filter="url(#softGlow)"/>'
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
        f'rx="{CARD_RADIUS}" fill="#{style.bg_color}" fill-opacity="{style.surface_opacity:.2f}" '
        f'stroke="#{PROFILE_COLOR}" stroke-width="1.6" '
        f'stroke-opacity="{style.border_opacity:.2f}"/>'
    )


def svg_horizontal_divider(
    x: float,
    y: float,
    width: float,
    *,
    opacity: float,
    inset_ratio: float = CARD_DIVIDER_INSET_RATIO,
) -> str:
    """Render a centered horizontal divider using the shared card inset."""
    inset = width * inset_ratio
    return (
        f'<line x1="{x + inset:.1f}" y1="{y}" '
        f'x2="{x + width - inset:.1f}" y2="{y}" '
        f'stroke="#{PROFILE_COLOR}" stroke-opacity="{opacity:.2f}"/>'
    )


def svg_card_document(
    *,
    width: int,
    height: int,
    aria_label: str,
    style: SvgStyle,
    content: str,
    include_frame: bool = True,
    defs_extra: str = "",
) -> str:
    """Wrap generated content in the shared SVG document/card scaffold."""
    frame = svg_card_frame(0, 0, width, height, style=style) if include_frame else ""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{svg_escape(aria_label)}">
  <defs>{svg_soft_glow_filter()}{defs_extra}</defs>
  <style>{svg_typography_css(style)}</style>
  {frame}
  {content}
</svg>\n'''


def generated_asset_path(stem: str) -> Path:
    """Return the path for a generated SVG asset."""
    return ASSET_DIR / f"{stem}.svg"


def write_svg(
    stem: str,
    builder: Callable[[SvgStyle], str],
) -> Path:
    """Generate an SVG atomically without replacing the last-good file on failure."""
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    path = generated_asset_path(stem)
    # Build the full document before touching the previous public asset.
    document = builder(SVG_STYLE)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temp_path.write_text(document, encoding="utf-8")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)
    return path


def write_svg_with_fallback(
    stem: str,
    builder: Callable[[SvgStyle], str],
    *,
    source_available: bool,
    health: HealthReport,
    component: str,
    active_assets: set[str],
) -> Path | None:
    """Generate a fresh SVG, preserve last-good content, or omit the card.

    A transient source/build failure must never replace valid public content
    with an error placeholder.
    """
    path = generated_asset_path(stem)
    if not source_available:
        if not path.exists():
            return None
        active_assets.add(path.name)
        return path

    try:
        path = write_svg(stem, builder)
    except Exception as exc:
        health.add(component, exc)
        if not path.exists():
            return None

    active_assets.add(path.name)
    return path


def safe_svg_card(
    stem: str,
    builder: Callable[[SvgStyle], str],
    *,
    health: HealthReport,
    component: str,
    active_assets: set[str],
    source_available: bool = True,
) -> Path | None:
    """Shared SVG generation path for static and dynamic components."""
    return write_svg_with_fallback(
        stem,
        builder,
        source_available=source_available,
        health=health,
        component=component,
        active_assets=active_assets,
    )

def build_languages_svg(languages: dict[str, int], *, style: SvgStyle) -> str:
    """Render chart-like language data; data alignment intentionally beats centering.

    Narrative cards are centered, but charts keep labels/values aligned to the
    geometry they describe. This is a deliberate design-system exception, not an
    inconsistency.
    """
    ranked = sorted(languages.items(), key=lambda item: item[1], reverse=True)[:6]
    total = sum(value for _, value in ranked)
    width, height = SVG_WIDTH, STANDARD_CARD_HEIGHT

    track_fill_opacity = 0.42
    highlight_color = style.text_color
    bar_defs = (
        f'<linearGradient id="languageBarFill" x1="0%" y1="0%" x2="100%" y2="0%">'
        f'<stop offset="0%" stop-color="#{PROFILE_COLOR}" stop-opacity="0.98"/>'
        f'<stop offset="55%" stop-color="#{PROFILE_COLOR}" stop-opacity="0.86"/>'
        f'<stop offset="100%" stop-color="#{PROFILE_COLOR}" stop-opacity="0.52"/></linearGradient>'
    )

    def language_bar(x: float, y: float, total_width: float, fill_width: float, opacity: float) -> str:
        return (
            f'<rect x="{x}" y="{y}" width="{total_width:.1f}" height="7" rx="3.5" '
            f'fill="#{style.track_color}" fill-opacity="{track_fill_opacity:.2f}"/>'
            f'<rect x="{x}" y="{y}" width="{fill_width:.1f}" height="7" rx="3.5" '
            f'fill="url(#languageBarFill)" opacity="{opacity:.2f}"/>'
            f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{max(fill_width - 2, 0.0):.1f}" height="1.6" rx="0.8" '
            f'fill="#{highlight_color}" fill-opacity="{0.18 * opacity:.2f}"/>'
        )

    if not ranked or total <= 0:
        raise ValueError("No usable public language data")

    rows: list[str] = []
    if len(ranked) <= 3:
        # Keep short language lists visually balanced across the full card width.
        y_positions = {1: [122], 2: [98, 148], 3: [78, 123, 168]}[len(ranked)]
        for index, ((language, value), y) in enumerate(zip(ranked, y_positions)):
            pct = (value / total) * 100
            bar_width = max(2.0, 570.0 * pct / 100.0)
            opacity = max(0.35, 1.0 - index * 0.11)
            rows.extend(
                [
                    f'<text class="data-label" x="{CARD_PADDING_X}" y="{y}">{svg_escape(language)}</text>',
                    f'<text class="data-meta" x="{width - CARD_PADDING_X}" y="{y}" text-anchor="end">{pct:.1f}%</text>',
                    language_bar(CARD_PADDING_X, y + SPACE_SM, 570.0, bar_width, opacity),
                ]
            )
    else:
        # Six languages fit in the same footprint as GitHub Stats by using two
        # columns of at most three rows.
        column_x = (CARD_PADDING_X, 375)
        y_positions = (78, 123, 168)
        bar_max_width = 245.0
        for index, (language, value) in enumerate(ranked):
            column = index // 3
            row = index % 3
            x = column_x[column]
            y = y_positions[row]
            pct = (value / total) * 100
            bar_width = max(2.0, bar_max_width * pct / 100.0)
            opacity = max(0.35, 1.0 - index * 0.11)
            rows.extend(
                [
                    f'<text class="data-label" x="{x}" y="{y}">{svg_escape(language)}</text>',
                    f'<text class="data-meta" x="{x + 300}" y="{y}" text-anchor="end">{pct:.1f}%</text>',
                    language_bar(x, y + SPACE_SM, bar_max_width, bar_width, opacity),
                ]
            )

    return svg_card_document(
        width=width,
        height=height,
        aria_label="Most used languages",
        style=style,
        content="".join(rows),
        defs_extra=bar_defs,
    )


def build_stats_svg(
    snapshot: ForgeStatsSnapshot,
    *,
    style: SvgStyle,
) -> str:
    """Render compact professional GitHub collaboration and impact signals.

    This card intentionally avoids followers, repo counts and streaks. Community
    owns follower context; Activity owns contribution rhythm; Stats should answer
    how the account collaborates and what public impact its code has.
    """

    def fmt(value: int) -> str:
        return f"{value:,}".replace(",", " ")

    metrics = [
        ("Merged PRs", STATS_PERIOD_LABEL, fmt(snapshot.merged_requests)),
        ("Code reviews", STATS_PERIOD_LABEL, fmt(snapshot.reviews)),
        ("Repos contributed", STATS_PERIOD_LABEL, fmt(snapshot.repositories_contributed)),
        ("Stars earned", "", fmt(snapshot.stars_earned)),
    ]

    width, height = SVG_WIDTH, STATS_CARD_HEIGHT
    blocks: list[str] = []
    cell_width = width / len(metrics)
    separator_top = CARD_PADDING_Y
    separator_bottom = height - CARD_PADDING_Y

    for index, (label, period, value) in enumerate(metrics):
        center_x = (cell_width * index) + (cell_width / 2)
        if index:
            x = cell_width * index
            blocks.append(
                f'<line x1="{x:.0f}" y1="{separator_top}" '
                f'x2="{x:.0f}" y2="{separator_bottom}" stroke="#{PROFILE_COLOR}" '
                f'stroke-opacity="{style.divider_opacity:.2f}"/>'
            )
        blocks.append(
            f'<text class="metric-value" x="{center_x:.0f}" y="58" '
            f'text-anchor="middle">{svg_escape(value)}</text>'
        )
        blocks.append(
            f'<text class="metric-label" x="{center_x:.0f}" y="91" '
            f'text-anchor="middle">{svg_escape(label)}</text>'
        )
        if period:
            blocks.append(
                f'<text class="meta" x="{center_x:.0f}" y="111" '
                f'text-anchor="middle">{svg_escape(period)}</text>'
            )

    return svg_card_document(
        width=width,
        height=height,
        aria_label=(
            "GitHub professional statistics: merged pull requests, code reviews, "
            "repositories contributed to, and stars earned"
        ),
        style=style,
        content="".join(blocks),
    )


def wrap_project_description(value: object, *, width: int = 43, max_lines: int = 3) -> list[str]:
    """Wrap one repository description into a compact deterministic SVG block."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return []
    lines = textwrap.wrap(
        text,
        width=width,
        break_long_words=True,
        break_on_hyphens=True,
    )
    if len(lines) <= max_lines:
        return lines
    visible = lines[:max_lines]
    last = visible[-1].rstrip(" .")
    if len(last) >= width:
        last = last[: max(1, width - 1)].rstrip()
    visible[-1] = last + "…"
    return visible


def featured_project_stem(repository: JsonObject) -> str:
    """Return a stable generated-asset stem for one pinned repository."""
    identity = str(
        repository.get("nameWithOwner")
        or repository.get("name")
        or "repository"
    )
    slug = SLUG_SEPARATOR_RE.sub("-", identity.casefold()).strip("-")[:44]
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
    return f"featured-project-{slug or 'repository'}-{digest}"


def build_featured_project_card_svg(
    repository: JsonObject,
    *,
    style: SvgStyle,
) -> str:
    """Render one pinned repository using the shared semantic card hierarchy."""
    width = FEATURED_PROJECT_CARD_WIDTH
    height = FEATURED_PROJECT_CARD_HEIGHT
    center_x = width / 2
    name = str(repository.get("name") or "").strip()
    if not name:
        raise ValueError("Pinned repository is missing its name")

    raw_owner: Any = repository.get("owner")
    owner: JsonObject = raw_owner if is_json_object(raw_owner) else {}
    owner_login = str(owner.get("login") or "").strip()
    contributed = bool(owner_login) and owner_login.casefold() != USERNAME.casefold()
    description_lines = wrap_project_description(repository.get("description"))
    raw_language_data: Any = repository.get("primaryLanguage")
    language_data: JsonObject = (
        raw_language_data if is_json_object(raw_language_data) else {}
    )
    language = str(language_data.get("name") or "").strip()
    stars = int(repository.get("stargazerCount") or 0)
    stars_label = f"★ {stars:,}".replace(",", " ")
    metadata = f"{language} · {stars_label}" if language else stars_label

    title_attrs = ""
    if len(name) > 27:
        title_attrs = ' textLength="286" lengthAdjust="spacingAndGlyphs"'

    content: list[str] = []
    if contributed:
        content.append(
            f'<text class="action-label" x="{center_x:.1f}" y="19" '
            'text-anchor="middle">CONTRIBUTED</text>'
        )
    content.append(
        f'<text class="card-title" x="{center_x:.1f}" y="39" '
        f'text-anchor="middle"{title_attrs}>{svg_escape(name)}</text>'
    )
    content.append(
        svg_horizontal_divider(
            0,
            56,
            width,
            opacity=style.divider_opacity,
            inset_ratio=0.09,
        )
    )

    for index, line in enumerate(description_lines):
        content.append(
            f'<text class="card-description" x="{center_x:.1f}" '
            f'y="{80 + (index * 18)}" text-anchor="middle">'
            f'{svg_escape(line)}</text>'
        )

    content.append(
        f'<text class="meta" x="{center_x:.1f}" y="140" '
        f'text-anchor="middle">{svg_escape(metadata)}</text>'
    )

    return svg_card_document(
        width=width,
        height=height,
        aria_label=f"Featured project {name}",
        style=style,
        content="".join(content),
    )


def render_centered_paragraph(items: list[str], *, separator: str = "\n  ") -> str:
    """Wrap visible inline HTML items in the README's canonical centered block."""
    visible = [item for item in items if item]
    if not visible:
        return ""
    return '<p align="center">\n  ' + separator.join(visible) + '\n</p>'


def centered_card_rows(cards: list[str], *, per_row: int) -> str:
    """Render deterministic centered rows instead of relying on GitHub wrapping."""
    visible = [card for card in cards if card]
    if not visible:
        return ""
    rows: list[str] = []
    for offset in range(0, len(visible), per_row):
        rows.append(
            render_centered_paragraph(visible[offset : offset + per_row])
        )
    return "\n\n".join(rows)


def build_featured_projects_content(
    repositories: list[JsonObject],
    *,
    health: HealthReport,
    active_assets: set[str],
) -> str:
    """Render GitHub profile pins as centered, individually linked cards.

    GitHub pins are the portfolio CMS: their selection and order must come from
    ``pinnedItems`` rather than a second hand-maintained project list.
    """
    cards: list[str] = []
    for repository in repositories[:6]:
        url = valid_external_url(repository.get("url"))
        name = str(repository.get("name") or "").strip()
        if not name or not url:
            continue
        stem = featured_project_stem(repository)
        path = safe_svg_card(
            stem,
            lambda style, repository=repository: build_featured_project_card_svg(
                repository, style=style
            ),
            health=health,
            component=f"Featured Project card: {name}",
            active_assets=active_assets,
        )
        if path:
            cards.append(
                build_linked_image(
                    path,
                    url=url,
                    alt=f"Featured project: {name}",
                    width=FEATURED_PROJECT_CARD_WIDTH,
                )
            )
    return centered_card_rows(cards, per_row=2)


def blend_hex(start_hex: str, end_hex: str, ratio: float) -> str:
    """Blend two six-digit RGB colors and return a six-digit hex string."""
    ratio = max(0.0, min(1.0, ratio))
    start_rgb = tuple(int(start_hex[index:index + 2], 16) for index in (0, 2, 4))
    end_rgb = tuple(int(end_hex[index:index + 2], 16) for index in (0, 2, 4))
    blended = tuple(
        round(start + ((end - start) * ratio))
        for start, end in zip(start_rgb, end_rgb)
    )
    return "".join(f"{channel:02x}" for channel in blended)


def activity_level_colors(style: SvgStyle) -> dict[str, str]:
    """Map GitHub contribution quartiles onto the profile's green palette."""
    return {
        "NONE": style.track_color,
        "FIRST_QUARTILE": blend_hex(style.bg_color, PROFILE_COLOR, 0.28),
        "SECOND_QUARTILE": blend_hex(style.bg_color, PROFILE_COLOR, 0.48),
        "THIRD_QUARTILE": blend_hex(style.bg_color, PROFILE_COLOR, 0.72),
        "FOURTH_QUARTILE": PROFILE_COLOR,
    }



ACTIVITY_MONTH_NAMES = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _activity_month_labels(
    weeks: tuple[tuple[ContributionDay, ...], ...],
    *,
    grid_x: int,
    cell_step: int,
    months_y: int,
) -> str:
    blocks: list[str] = []
    last_month: tuple[int, int] | None = None
    for week_index, week in enumerate(weeks):
        ordered = sorted(week, key=lambda day: day.date)
        if not ordered:
            continue
        candidates = [day for day in ordered if day.date.day <= 7]
        marker = candidates[0] if candidates else ordered[0]
        month_key = (marker.date.year, marker.date.month)
        if month_key == last_month:
            continue
        blocks.append(
            f'<text class="data-meta" x="{grid_x + week_index * cell_step}" '
            f'y="{months_y}">{ACTIVITY_MONTH_NAMES[marker.date.month - 1]}</text>'
        )
        last_month = month_key
    return "".join(blocks)


def _activity_weekday_labels(
    *,
    grid_y: int,
    cell_step: int,
    cell_size: int,
    label_x: int,
) -> str:
    blocks: list[str] = []
    for weekday, label in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        y = grid_y + (weekday * cell_step) + cell_size - 1
        blocks.append(
            f'<text class="data-meta" x="{label_x}" y="{y}" '
            f'text-anchor="start">{label}</text>'
        )
    return "".join(blocks)


def _activity_cells(
    weeks: tuple[tuple[ContributionDay, ...], ...],
    colors: dict[str, str],
    *,
    grid_x: int,
    grid_y: int,
    cell_step: int,
    cell_size: int,
) -> str:
    blocks: list[str] = []
    for week_index, week in enumerate(weeks):
        x = grid_x + (week_index * cell_step)
        for day in week:
            y = grid_y + (day.weekday * cell_step)
            color = colors.get(day.level, colors["NONE"])
            plural = "" if day.count == 1 else "s"
            label = f"{day.date.isoformat()}: {day.count} contribution{plural}"
            blocks.append(
                f'<rect x="{x}" y="{y}" width="{cell_size}" height="{cell_size}" '
                f'rx="2" fill="#{color}"><title>{svg_escape(label)}</title></rect>'
            )
    return "".join(blocks)


def _activity_legend(colors: dict[str, str], *, width: int, height: int) -> str:
    legend_y = height - 27
    legend_cell = 8
    legend_gap = 4
    legend_colors = [
        colors["NONE"],
        colors["FIRST_QUARTILE"],
        colors["SECOND_QUARTILE"],
        colors["THIRD_QUARTILE"],
        colors["FOURTH_QUARTILE"],
    ]
    legend_width = 28 + (len(legend_colors) * legend_cell) + (
        (len(legend_colors) - 1) * legend_gap
    ) + 31
    legend_x = width - CARD_PADDING_X - legend_width
    blocks = [
        f'<text class="data-meta" x="{legend_x}" y="{legend_y + 7}">Less</text>'
    ]
    square_x = legend_x + 31
    for color in legend_colors:
        blocks.append(
            f'<rect x="{square_x}" y="{legend_y}" width="{legend_cell}" '
            f'height="{legend_cell}" rx="2" fill="#{color}"/>'
        )
        square_x += legend_cell + legend_gap
    blocks.append(
        f'<text class="data-meta" x="{square_x + 1}" y="{legend_y + 7}">More</text>'
    )
    return "".join(blocks)


def build_activity_svg(
    snapshot: ContributionSnapshot,
    *,
    style: SvgStyle,
) -> str:
    """Render a GitHub-style heatmap with the shared card-title hierarchy."""
    width, height = SVG_WIDTH, ACTIVITY_CARD_HEIGHT
    title_y, total_y, months_y, grid_y = 29, 49, 68, 80
    cell_size, cell_gap = 8, 3
    cell_step = cell_size + cell_gap
    grid_x = 58
    colors = activity_level_colors(style)
    plural = "" if snapshot.total == 1 else "s"
    blocks = [
        f'<text class="card-title" x="{width / 2:.1f}" y="{title_y}" '
        'text-anchor="middle">GitHub Activity · 365 days</text>',
        f'<text class="meta" x="{width / 2:.1f}" y="{total_y}" '
        f'text-anchor="middle">{snapshot.total} contribution{plural}</text>',
        _activity_month_labels(
            snapshot.weeks, grid_x=grid_x, cell_step=cell_step, months_y=months_y
        ),
        _activity_weekday_labels(
            grid_y=grid_y,
            cell_step=cell_step,
            cell_size=cell_size,
            label_x=CARD_PADDING_X,
        ),
        _activity_cells(
            snapshot.weeks,
            colors,
            grid_x=grid_x,
            grid_y=grid_y,
            cell_step=cell_step,
            cell_size=cell_size,
        ),
        _activity_legend(colors, width=width, height=height),
    ]
    return svg_card_document(
        width=width,
        height=height,
        aria_label="GitHub activity over 365 days",
        style=style,
        content="".join(blocks),
    )



def write_activity_svg(
    snapshot: ContributionSnapshot | None,
    *,
    health: HealthReport,
    active_assets: set[str],
) -> Path | None:
    """Generate the activity SVG or preserve the last valid asset."""
    if snapshot is None or not snapshot.weeks:
        return write_svg_with_fallback(
            "github-activity",
            lambda style: "",
            source_available=False,
            health=health,
            component="GitHub activity card",
            active_assets=active_assets,
        )
    return write_svg_with_fallback(
        "github-activity",
        lambda style: build_activity_svg(snapshot, style=style),
        source_available=True,
        health=health,
        component="GitHub activity card",
        active_assets=active_assets,
    )

def render_section_lead(text: str) -> str:
    """Render ``section-lead``: short centered semibold human-facing copy.

    Keep this visually equivalent to SVG ``card-title`` rather than turning it
    into a second section heading. Alignment is supplied by the parent block.
    """
    return f'<strong>{html.escape(text)}</strong>'


def build_community_block(followers: list[JsonObject] | None) -> str:
    """Render centered, clickable GitHub follower badges in deterministic rows."""
    if not followers:
        return ""

    rows: list[str] = [
        '<p align="center">',
        f'  {render_section_lead("I thank all my followers")}<br><br>',
        f'  <a href="https://github.com/{USERNAME}?tab=followers">'
        f'<img src="https://img.shields.io/github/followers/{USERNAME}'
        f'?style=for-the-badge&amp;logo=github&amp;logoColor=white'
        f'&amp;label=FOLLOWERS&amp;labelColor={PROFILE_COLOR}&amp;color={PROFILE_COLOR}" '
        f'alt="{USERNAME} followers"></a>',
        '</p>',
    ]

    badges_per_row = 4
    for offset in range(0, len(followers), badges_per_row):
        badge_row: list[str] = []
        for follower in followers[offset : offset + badges_per_row]:
            login = str(follower.get("login") or "").strip()
            if not login:
                continue
            escaped_login = html.escape(login, quote=True)
            encoded_label = escaped_login
            profile_url = f"https://github.com/{login}"
            badge_url = (
                f"https://img.shields.io/github/followers/{login}"
                f"?style=for-the-badge&amp;logo=github&amp;logoColor=white"
                f"&amp;label={encoded_label}&amp;labelColor={PROFILE_COLOR}"
                f"&amp;color={PROFILE_COLOR}"
            )
            badge_row.append(
                f'<a href="{profile_url}">'
                f'<img src="{badge_url}" alt="{escaped_login} followers"></a>'
            )
        if badge_row:
            rows.extend(
                [
                    '<p align="center">',
                    "  " + "\n  ".join(badge_row),
                    '</p>',
                ]
            )

    return "\n".join(rows)

def load_profile_config(health: HealthReport) -> JsonObject:
    """Load optional professional-profile data from the source TOML file."""
    if not CONFIG.exists():
        return {}
    try:
        return tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception as exc:
        health.add("Profile configuration", exc)
        return {}


def valid_external_url(value: object) -> str:
    """Return one normalized HTTPS URL, or an empty string when invalid."""
    url = normalize_url(str(value or ""))
    if not url:
        return ""
    parsed = urlparse(url)
    return url if parsed.scheme == "https" and bool(parsed.netloc) else ""


def build_compact_link_card_svg(
    title: str,
    subtitle: str,
    *,
    style: SvgStyle,
    width: int = 220,
    height: int = 104,
) -> str:
    """Render the standard compact narrative card for external profiles.

    Use this family when one external identity has a short title + purpose but
    no meaningful progress metric (primarily Security Research). Security Practice
    should prefer ``build_practice_progress_card_svg`` once an adapter is reviewed.
    """
    divider_y = 52
    content = (
        f'<text class="card-title" x="{width / 2:.1f}" y="34" text-anchor="middle">'
        f'{svg_escape(title)}</text>'
        + svg_horizontal_divider(
            0,
            divider_y,
            width,
            opacity=style.divider_opacity,
            inset_ratio=0.18,
        )
        + f'<text class="card-description" x="{width / 2:.1f}" y="79" '
        f'text-anchor="middle">{svg_escape(subtitle)}</text>'
    )
    return svg_card_document(
        width=width,
        height=height,
        aria_label=f"{title}: {subtitle}",
        style=style,
        content=content,
    )


def build_practice_progress_card_svg(
    title: str,
    subtitle: str,
    status: str,
    progress: float,
    progress_label: str,
    *,
    style: SvgStyle,
    width: int = 220,
    height: int = 156,
) -> str:
    """Render a Security Practice card with one status + one progression signal.

    This is the preferred practice-card family when a platform exposes reliable
    personal progress. Do not add points, global ranking, streaks, owns, or other
    gamification merely because an API returns them.
    """
    center_x = width / 2
    track_x = 30.0
    track_width = width - (2 * track_x)
    clamped = max(0.0, min(100.0, float(progress)))
    fill_width = track_width * clamped / 100.0
    content = (
        f'<text class="card-title" x="{center_x:.1f}" y="28" text-anchor="middle">'
        f'{svg_escape(title)}</text>'
        f'<text class="card-description" x="{center_x:.1f}" y="48" text-anchor="middle">'
        f'{svg_escape(subtitle)}</text>'
        + svg_horizontal_divider(
            0, 61, width, opacity=style.divider_opacity, inset_ratio=0.18
        )
        + f'<text class="status-value" x="{center_x:.1f}" y="88" text-anchor="middle">'
        f'{svg_escape(status)}</text>'
        f'<rect x="{track_x:.1f}" y="108" width="{track_width:.1f}" height="7" rx="3.5" '
        f'fill="#{style.track_color}" fill-opacity="0.72"/>'
        f'<rect x="{track_x:.1f}" y="108" width="{fill_width:.1f}" height="7" rx="3.5" '
        f'fill="#{PROFILE_COLOR}" fill-opacity="0.92"/>'
        f'<text class="meta" x="{center_x:.1f}" y="137" text-anchor="middle">'
        f'{svg_escape(progress_label)}</text>'
    )
    return svg_card_document(
        width=width,
        height=height,
        aria_label=f"{title}: {subtitle}; {status}; {progress_label}",
        style=style,
        content=content,
    )


def _format_hackerone_metric(value: float) -> str:
    """Format HackerOne metrics compactly without implying extra precision."""
    rounded = round(value, 1)
    return str(int(rounded)) if rounded.is_integer() else f"{rounded:.1f}"


def build_hackerone_research_card_svg(
    snapshot: HackerOneSnapshot,
    *,
    style: SvgStyle,
    width: int = 340,
    height: int = 156,
) -> str:
    """Render the HackerOne profile card using existing profile-card semantics.

    The card represents the researcher profile only. Public disclosures are
    separate linked evidence cards, so no disclosure status/CTA is duplicated
    inside this profile card. Reputation alone never activates the section.
    """
    if not snapshot.meaningful:
        return ""

    # A public disclosure can make the profile meaningful before Signal/Impact
    # are calculated. In that state reuse the existing compact profile family
    # rather than inventing a special disclosure-status layout.
    if snapshot.signal is None and snapshot.impact is None:
        return build_compact_link_card_svg(
            "HACKERONE",
            "Security Research · Disclosures",
            style=style,
            width=width,
            height=104,
        )

    center_x = width / 2
    header = (
        f'<text class="card-title" x="{center_x:.1f}" y="28" text-anchor="middle">'
        'HACKERONE</text>'
        f'<text class="card-description" x="{center_x:.1f}" y="48" text-anchor="middle">'
        'Security Research · Disclosures</text>'
        + svg_horizontal_divider(
            0, 61, width, opacity=style.divider_opacity, inset_ratio=0.18
        )
    )

    metrics: list[tuple[str, str]] = []
    if snapshot.reputation is not None:
        metrics.append(("Reputation", _format_hackerone_metric(snapshot.reputation)))
    if snapshot.signal is not None:
        metrics.append(("Signal", _format_hackerone_metric(snapshot.signal)))
    if snapshot.impact is not None:
        metrics.append(("Impact", _format_hackerone_metric(snapshot.impact)))

    blocks: list[str] = [header]
    cell_width = width / max(1, len(metrics))
    for index, (label, value) in enumerate(metrics):
        x = (cell_width * index) + (cell_width / 2)
        if index:
            separator_x = cell_width * index
            blocks.append(
                f'<line x1="{separator_x:.1f}" y1="76" x2="{separator_x:.1f}" y2="126" '
                f'stroke="#{PROFILE_COLOR}" stroke-opacity="{style.divider_opacity:.2f}"/>'
            )
        blocks.append(
            f'<text class="metric-value" x="{x:.1f}" y="104" text-anchor="middle">'
            f'{svg_escape(value)}</text>'
        )
        blocks.append(
            f'<text class="metric-label" x="{x:.1f}" y="127" text-anchor="middle">'
            f'{svg_escape(label)}</text>'
        )

    return svg_card_document(
        width=width,
        height=height,
        aria_label="HackerOne security research profile with earned researcher metrics",
        style=style,
        content="".join(blocks),
    )


def hackerone_disclosure_stem(disclosure: HackerOneDisclosure) -> str:
    """Return a stable generated-asset stem for one HackerOne disclosure."""
    safe_id = SLUG_SEPARATOR_RE.sub("-", disclosure.report_id.casefold()).strip("-")
    if safe_id:
        return f"security-research-hackerone-disclosure-{safe_id[:36]}"
    digest = hashlib.sha256(disclosure.url.encode("utf-8")).hexdigest()[:10]
    return f"security-research-hackerone-disclosure-{digest}"


def build_security_research_evidence_card_svg(
    source: str,
    title: str,
    metadata: tuple[str, ...],
    *,
    style: SvgStyle,
    width: int = 220,
    height: int = 156,
) -> str:
    """Render one compact, externally verifiable Security Research evidence card.

    This family reuses the canonical frame, semantic typography and 220px compact
    geometry already used elsewhere. Three cards therefore fit one deterministic
    centered row without introducing a Security Research-only grid system.
    """
    center_x = width / 2
    source_label = re.sub(r"\s+", " ", source).strip().upper()
    title_lines = wrap_project_description(title, width=27, max_lines=2)
    if not source_label or not title_lines:
        raise ValueError("Security Research evidence card requires source and title")

    source_attrs = ""
    if len(source_label) > 18:
        source_attrs = ' textLength="176" lengthAdjust="spacingAndGlyphs"'

    content: list[str] = [
        f'<text class="card-title" x="{center_x:.1f}" y="28" '
        f'text-anchor="middle"{source_attrs}>{svg_escape(source_label)}</text>',
        svg_horizontal_divider(
            0, 43, width, opacity=style.divider_opacity, inset_ratio=0.14
        ),
    ]

    description_start_y = 69 if len(title_lines) == 2 else 78
    for index, line in enumerate(title_lines):
        content.append(
            f'<text class="card-description" x="{center_x:.1f}" '
            f'y="{description_start_y + (index * 18)}" text-anchor="middle">'
            f'{svg_escape(line)}</text>'
        )

    meta_lines = [re.sub(r"\s+", " ", line).strip() for line in metadata if line]
    meta_lines = [line for line in meta_lines if line][:2]
    if meta_lines:
        meta_start_y = 126 if len(meta_lines) == 2 else 135
        for index, line in enumerate(meta_lines):
            meta_attrs = ""
            if len(line) > 30:
                meta_attrs = ' textLength="176" lengthAdjust="spacingAndGlyphs"'
            content.append(
                f'<text class="meta" x="{center_x:.1f}" '
                f'y="{meta_start_y + (index * 17)}" text-anchor="middle"{meta_attrs}>'
                f'{svg_escape(line)}</text>'
            )

    return svg_card_document(
        width=width,
        height=height,
        aria_label=f"Security research evidence from {source_label}: {title}",
        style=style,
        content="".join(content),
    )


def build_hackerone_disclosure_card_svg(
    disclosure: HackerOneDisclosure,
    *,
    style: SvgStyle,
) -> str:
    """Render one public HackerOne disclosure through the shared evidence card."""
    classification = " · ".join(
        part for part in (disclosure.severity, disclosure.cwe) if part
    )
    metadata = tuple(line for line in (classification, disclosure.cve) if line)
    return build_security_research_evidence_card_svg(
        disclosure.program,
        disclosure.title,
        metadata,
        style=style,
    )


def build_certification_card_svg(
    name: str,
    issuer: str,
    date_label: str,
    *,
    style: SvgStyle,
) -> str:
    """Render one verified credential with the shared centered card hierarchy.

    The surrounding linked picture owns the verification action, so the SVG
    contains only credential data and never renders a redundant VERIFY label.
    """
    width, height = 340, 104
    center_x = width / 2
    content = (
        f'<text class="card-title" x="{center_x:.1f}" y="29" '
        f'text-anchor="middle">{svg_escape(name)}</text>'
        f'<text class="card-description" x="{center_x:.1f}" y="49" '
        f'text-anchor="middle">{svg_escape(issuer)}</text>'
        + svg_horizontal_divider(
            0,
            62,
            width,
            opacity=style.divider_opacity,
            inset_ratio=0.09,
        )
        + f'<text class="meta" x="{center_x:.1f}" y="87" '
        f'text-anchor="middle">{svg_escape(date_label)}</text>'
    )
    return svg_card_document(
        width=width,
        height=height,
        aria_label=f"{name} certification by {issuer}",
        style=style,
        content=content,
    )


def build_linked_image(
    path: Path,
    *,
    url: str,
    alt: str,
    width: int | None = None,
) -> str:
    """Build one individually clickable generated SVG card."""
    asset_url = versioned_asset_url(path)
    escaped_url = html.escape(url, quote=True)
    escaped_alt = html.escape(alt, quote=True)
    width_attr = f' width="{width}"' if width else ""
    return (
        f'<a href="{escaped_url}"><img src="{asset_url}" '
        f'alt="{escaped_alt}"{width_attr}></a>'
    )


def centered_inline_cards(cards: list[str]) -> str:
    cards = [card for card in cards if card]
    if not cards:
        return ""
    return render_centered_paragraph(cards)



def _practice_progress_label(
    key: str,
    snapshot: HackTheBoxSnapshot | CyberDefendersSnapshot,
) -> str:
    if key == "hack_the_box":
        pct = int(round(snapshot.progress))
        next_rank = getattr(snapshot, "next_rank", "")
        return f"{pct}% to {next_rank}" if next_rank else f"{pct}% rank progress"
    progress_text = f"{snapshot.progress:.1f}".rstrip("0").rstrip(".")
    return f"{progress_text}% avg. skill progress"


def _practice_snapshot(
    key: str,
    hack_the_box: HackTheBoxSnapshot | None,
    cyberdefenders: CyberDefendersSnapshot | None,
) -> HackTheBoxSnapshot | CyberDefendersSnapshot | None:
    snapshots = {
        "hack_the_box": hack_the_box,
        "cyberdefenders": cyberdefenders,
    }
    return snapshots.get(key)


def _practice_platform_card(
    key: str,
    title: str,
    subtitle: str,
    url: str,
    *,
    health: HealthReport,
    active_assets: set[str],
    snapshot: HackTheBoxSnapshot | CyberDefendersSnapshot | None,
) -> str:
    if key not in {"hack_the_box", "cyberdefenders"}:
        health.add(
            f"Security Practice adapter: {title}",
            "Configured profile has no reviewed status/progress adapter",
        )
        return ""
    progress_label = _practice_progress_label(key, snapshot) if snapshot else ""
    stem = f"security-practice-{key.replace('_', '-')}"
    path = safe_svg_card(
        stem,
        lambda style,
        title=title,
        subtitle=subtitle,
        snapshot=snapshot,
        progress_label=progress_label: (
            build_practice_progress_card_svg(
                title,
                subtitle,
                snapshot.rank,
                snapshot.progress,
                progress_label,
                style=style,
            )
            if snapshot is not None
            else ""
        ),
        health=health,
        component=(
            "Security Practice card: Hack The Box"
            if key == "hack_the_box"
            else "Security Practice card: CyberDefenders"
        ),
        active_assets=active_assets,
        source_available=snapshot is not None,
    )
    if not path:
        return ""
    return build_linked_image(path, url=url, alt=f"{title} profile", width=220)


def _configured_profile_url(
    section: JsonObject,
    key: str,
    *,
    component: str,
    health: HealthReport,
) -> str:
    url = valid_external_url(section.get(key))
    if not url and section.get(key):
        health.add(component, f"Invalid HTTPS profile URL for {key}")
    return url


def build_security_practice_content(
    config: JsonObject,
    *,
    health: HealthReport,
    active_assets: set[str],
    hack_the_box: HackTheBoxSnapshot | None,
    cyberdefenders: CyberDefendersSnapshot | None,
) -> str:
    """Render configured, non-overlapping professional practice platforms."""
    raw_section: Any = config.get("security_practice")
    section_candidate: Any = raw_section or {}
    if not is_json_object(section_candidate):
        health.add("Security Practice configuration", "Expected a TOML table")
        return ""
    section = section_candidate

    cards: list[str] = []
    for key, (title, subtitle) in SECURITY_PRACTICE_CATALOG.items():
        url = _configured_profile_url(
            section,
            key,
            component="Security Practice configuration",
            health=health,
        )
        if not url:
            continue
        snapshot = _practice_snapshot(key, hack_the_box, cyberdefenders)
        card = _practice_platform_card(
            key,
            title,
            subtitle,
            url,
            health=health,
            active_assets=active_assets,
            snapshot=snapshot,
        )
        if card:
            cards.append(card)
    return centered_inline_cards(cards)




def _remove_generated_assets(stem: str) -> None:
    generated_asset_path(stem).unlink(missing_ok=True)


def _hackerone_profile_card(
    url: str,
    title: str,
    snapshot: HackerOneSnapshot | None,
    *,
    health: HealthReport,
    active_assets: set[str],
) -> tuple[str, HackerOneSnapshot | None]:
    visible = snapshot if snapshot and snapshot.meaningful else None
    stem = "security-research-hackerone"
    if snapshot is not None and visible is None:
        _remove_generated_assets(stem)
        return "", None
    if visible is None:
        return "", None
    path = safe_svg_card(
        stem,
        lambda style, snapshot=visible: build_hackerone_research_card_svg(
            snapshot, style=style
        ),
        health=health,
        component="Security Research card: HackerOne",
        active_assets=active_assets,
    )
    if not path:
        return "", visible
    return (
        build_linked_image(
            path,
            url=url,
            alt=f"{title} security research profile",
            width=340,
        ),
        visible,
    )


def _hackerone_disclosure_card(
    disclosure: HackerOneDisclosure,
    *,
    health: HealthReport,
    active_assets: set[str],
) -> str:
    path = safe_svg_card(
        hackerone_disclosure_stem(disclosure),
        lambda style, disclosure=disclosure: build_hackerone_disclosure_card_svg(
            disclosure, style=style
        ),
        health=health,
        component=f"Security Research disclosure: {disclosure.report_id}",
        active_assets=active_assets,
    )
    if not path:
        return ""
    return build_linked_image(
        path,
        url=disclosure.url,
        alt=f"HackerOne disclosure: {disclosure.title}",
        width=220,
    )


def _hackerone_disclosure_cards(
    snapshot: HackerOneSnapshot | None,
    *,
    health: HealthReport,
    active_assets: set[str],
) -> list[str]:
    if snapshot is None:
        return []
    return [
        card
        for disclosure in snapshot.disclosures[:6]
        if (
            card := _hackerone_disclosure_card(
                disclosure, health=health, active_assets=active_assets
            )
        )
    ]


def _manual_disclosure_data(
    disclosure: JsonObject,
    health: HealthReport,
) -> tuple[str, str, str, tuple[str, ...]] | None:
    source = str(disclosure.get("source") or disclosure.get("program") or "").strip()
    title = str(disclosure.get("title") or "").strip()
    disclosure_url = valid_external_url(disclosure.get("url"))
    if not source or not title or not disclosure_url:
        if source or title or disclosure.get("url"):
            health.add(
                SECURITY_RESEARCH_CONFIG_COMPONENT,
                f"Incomplete or invalid disclosure: {title or source or 'unnamed'}",
            )
        return None
    severity = str(disclosure.get("severity") or "").strip().upper()
    cwe = str(disclosure.get("cwe") or "").strip()
    cve = str(disclosure.get("cve") or "").strip().upper()
    classification = " · ".join(part for part in (severity, cwe) if part)
    metadata = tuple(line for line in (classification, cve) if line)
    return source, title, disclosure_url, metadata


def _manual_disclosure_card(
    disclosure: object,
    *,
    health: HealthReport,
    active_assets: set[str],
) -> str:
    if not is_json_object(disclosure):
        return ""
    data = _manual_disclosure_data(disclosure, health)
    if data is None:
        return ""
    source, title, disclosure_url, metadata = data
    digest = hashlib.sha256(
        f"{source}|{title}|{disclosure_url}".encode("utf-8")
    ).hexdigest()[:10]
    path = safe_svg_card(
        f"security-research-disclosure-{digest}",
        lambda style, source=source, title=title, metadata=metadata: build_security_research_evidence_card_svg(
            source, title, metadata, style=style
        ),
        health=health,
        component=f"Security Research disclosure: {title}",
        active_assets=active_assets,
    )
    if not path:
        return ""
    return build_linked_image(
        path,
        url=disclosure_url,
        alt=f"Security research disclosure: {title}",
        width=220,
    )


def _manual_disclosure_cards(
    section: JsonObject,
    *,
    health: HealthReport,
    active_assets: set[str],
) -> list[str]:
    raw_disclosures: Any = section.get("disclosures")
    disclosures_candidate: Any = raw_disclosures or []
    if not is_json_array(disclosures_candidate):
        health.add(SECURITY_RESEARCH_CONFIG_COMPONENT, "disclosures must be an array")
        return []
    disclosures = disclosures_candidate
    return [
        card
        for disclosure in disclosures[:6]
        if (
            card := _manual_disclosure_card(
                disclosure, health=health, active_assets=active_assets
            )
        )
    ]


def _security_research_profile_parts(
    section: JsonObject,
    hackerone: HackerOneSnapshot | None,
    *,
    health: HealthReport,
    active_assets: set[str],
) -> tuple[list[str], list[str]]:
    profile_cards: list[str] = []
    disclosure_cards: list[str] = []
    for key, (title, _subtitle) in SECURITY_RESEARCH_CATALOG.items():
        url = _configured_profile_url(
            section,
            key,
            component=SECURITY_RESEARCH_CONFIG_COMPONENT,
            health=health,
        )
        if not url:
            continue
        if key != "hackerone":
            health.add(
                f"Security Research adapter: {title}",
                "Configured profile has no reviewed research adapter",
            )
            continue
        profile_card, visible = _hackerone_profile_card(
            url,
            title,
            hackerone,
            health=health,
            active_assets=active_assets,
        )
        if profile_card:
            profile_cards.append(profile_card)
        disclosure_cards.extend(
            _hackerone_disclosure_cards(
                visible, health=health, active_assets=active_assets
            )
        )
    return profile_cards, disclosure_cards


def build_security_research_content(
    config: JsonObject,
    *,
    health: HealthReport,
    active_assets: set[str],
    hackerone: HackerOneSnapshot | None,
) -> str:
    """Render real-world security-research profiles and verified evidence cards."""
    raw_section: Any = config.get("security_research")
    section_candidate: Any = raw_section or {}
    if not is_json_object(section_candidate):
        health.add(SECURITY_RESEARCH_CONFIG_COMPONENT, "Expected a TOML table")
        return ""
    section = section_candidate

    profile_cards, disclosure_cards = _security_research_profile_parts(
        section,
        hackerone,
        health=health,
        active_assets=active_assets,
    )
    manual_cards = _manual_disclosure_cards(
        section, health=health, active_assets=active_assets
    )
    parts: list[str] = []
    if profile_cards:
        parts.append(centered_inline_cards(profile_cards))
    if disclosure_cards:
        parts.append(centered_card_rows(disclosure_cards, per_row=3))
    if manual_cards:
        parts.append(centered_card_rows(manual_cards, per_row=3))
    return "\n\n".join(parts)




def _credential_year(value: str) -> str:
    if not value:
        return ""
    if len(value) == 4 and value.isdigit():
        return value
    try:
        return str(dt.date.fromisoformat(value).year)
    except ValueError:
        return ""


def _credential_date_value(
    credential: JsonObject,
    field: str,
    prefix: str,
    health: HealthReport,
) -> str:
    value = str(credential.get(field) or "").strip()
    if not value:
        return ""
    parsed = _credential_year(value)
    if parsed:
        return f"{prefix} {parsed}"
    health.add(
        CERTIFICATIONS_CONFIG_COMPONENT,
        f"Invalid {field} for {credential.get('name') or 'credential'}",
    )
    return ""


def credential_date_label(credential: JsonObject, health: HealthReport) -> str:
    """Expose one compact temporal field: validity first, issuance only if needed."""
    for field, prefix in (("expires_at", "Valid until"), ("issued_at", "Issued")):
        label = _credential_date_value(credential, field, prefix, health)
        if label:
            return label
    return "Verified credential"




def _certification_card(
    credential: object,
    index: int,
    *,
    health: HealthReport,
    active_assets: set[str],
) -> str:
    if not is_json_object(credential):
        return ""
    name = str(credential.get("name") or "").strip()
    issuer = str(credential.get("issuer") or "").strip()
    verification_url = valid_external_url(credential.get("verification_url"))
    if not name or not issuer or not verification_url:
        if name or issuer or credential.get("verification_url"):
            health.add(
                CERTIFICATIONS_CONFIG_COMPONENT,
                f"Incomplete or unverifiable certification: {name or f'item {index + 1}'}",
            )
        return ""
    stem_key = SLUG_SEPARATOR_RE.sub("-", name.casefold()).strip("-") or str(index + 1)
    date_label = credential_date_label(credential, health)
    path = safe_svg_card(
        f"certification-{stem_key[:48]}",
        lambda style, name=name, issuer=issuer, date_label=date_label: build_certification_card_svg(
            name, issuer, date_label, style=style
        ),
        health=health,
        component=f"Certification card: {name}",
        active_assets=active_assets,
    )
    if not path:
        return ""
    return build_linked_image(
        path,
        url=verification_url,
        alt=f"Verify {name}",
        width=340,
    )


def build_certifications_content(
    config: JsonObject,
    *,
    health: HealthReport,
    active_assets: set[str],
) -> str:
    """Render only obtained credentials with a public verification URL."""
    raw_credentials: Any = config.get("certifications")
    credentials_candidate: Any = raw_credentials or []
    if not is_json_array(credentials_candidate):
        health.add(CERTIFICATIONS_CONFIG_COMPONENT, "certifications must be an array")
        return ""
    credentials = credentials_candidate
    cards = [
        card
        for index, credential in enumerate(credentials)
        if (
            card := _certification_card(
                credential, index, health=health, active_assets=active_assets
            )
        )
    ]
    return centered_inline_cards(cards)




BLOG_FEED_MAX_BYTES = 2 * 1024 * 1024
BLOG_POST_LIMIT = 4


def _read_blog_feed(feed_url: str) -> ET.Element:
    request = urllib.request.Request(
        feed_url,
        headers={
            "Accept": "application/atom+xml,application/rss+xml,application/xml,text/xml,*/*;q=0.8",
            "User-Agent": f"{USERNAME}-profile-readme",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(BLOG_FEED_MAX_BYTES + 1)
    if len(raw) > BLOG_FEED_MAX_BYTES:
        raise RuntimeError("Blog feed exceeds 2 MiB")
    return ET.fromstring(raw)


def _rss_posts(root: ET.Element) -> list[tuple[str, str]]:
    posts: list[tuple[str, str]] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if title and valid_external_url(link):
            posts.append((html.unescape(title), link))
        if len(posts) >= BLOG_POST_LIMIT:
            break
    return posts


def _atom_entry_post(entry: ET.Element) -> tuple[str, str] | None:
    title = ""
    link = ""
    for child in entry:
        local = child.tag.rsplit("}", 1)[-1]
        if local == "title" and not title:
            title = "".join(child.itertext()).strip()
        elif local == "link" and not link:
            candidate = (child.attrib.get("href") or "").strip()
            rel = (child.attrib.get("rel") or "alternate").strip()
            if candidate and rel in {"", "alternate"}:
                link = candidate
    if title and valid_external_url(link):
        return html.unescape(title), link
    return None


def _atom_posts(root: ET.Element, remaining: int) -> list[tuple[str, str]]:
    posts: list[tuple[str, str]] = []
    for entry in root.iter():
        if entry.tag.rsplit("}", 1)[-1] != "entry":
            continue
        post = _atom_entry_post(entry)
        if post:
            posts.append(post)
        if len(posts) >= remaining:
            break
    return posts


def fetch_blog_posts(feed_url: str, health: HealthReport) -> list[tuple[str, str]] | None:
    """Fetch up to four RSS/Atom posts; None means source failure, [] means empty."""
    if not feed_url:
        return []
    try:
        root = _read_blog_feed(feed_url)
    except Exception as exc:
        health.add("Blog feed", exc)
        return None
    posts = _rss_posts(root)
    if len(posts) < BLOG_POST_LIMIT:
        posts.extend(_atom_posts(root, BLOG_POST_LIMIT - len(posts)))
    return posts[:BLOG_POST_LIMIT]



def build_blog_content(posts: list[tuple[str, str]] | None) -> str:
    if not posts:
        return ""
    links = [
        f'<a href="{html.escape(url, quote=True)}">{html.escape(title)}</a>'
        for title, url in posts[:4]
    ]
    return render_centered_paragraph(links, separator="<br>\n  ")

def build_contact_content(
    *,
    website_url: str,
    linkedin_url: str,
    github_url: str,
    gitlab_url: str,
    gpg_url: str,
) -> str:
    """Render valid contact destinations with repository-local static badges.

    CONTACT badges are intentionally vendored assets: their visual identity is
    stable while only the surrounding destination links may change. COMMUNITY
    remains dynamic and continues to use Shields.io for follower counts.
    """
    contacts = [
        ("Website", Path("assets/badges/contact/website.svg"), website_url),
        ("LinkedIn", Path("assets/badges/contact/linkedin.svg"), linkedin_url),
        ("GitHub", Path("assets/badges/contact/github.svg"), github_url),
        ("GitLab", Path("assets/badges/contact/gitlab.svg"), gitlab_url),
        ("GPG Public Key", Path("assets/badges/contact/gpg-public-key.svg"), gpg_url),
    ]
    badges: list[str] = []
    for label, badge_path, url in contacts:
        target = valid_external_url(url)
        if not target:
            continue
        badge_url = versioned_asset_url(badge_path)
        badges.append(
            f'<a href="{html.escape(target, quote=True)}">'
            f'<img src="{html.escape(badge_url, quote=True)}" alt="{html.escape(label, quote=True)}">'
            '</a>'
        )
    return render_centered_paragraph(badges)

def extract_section_content(text: str, key: str) -> str:
    """Read the last successfully rendered content of one canonical section."""
    return block_body(
        text,
        f"<!-- SECTION:{key}:CONTENT:START -->",
        f"<!-- SECTION:{key}:CONTENT:END -->",
    )


def preserve_cached_generated_assets(
    content: str,
    *,
    active_assets: set[str],
    health: HealthReport,
    component: str,
) -> str:
    """Keep a cached section only when all generated assets it references exist."""
    if not content:
        return ""

    names = set(re.findall(r"\./assets/generated/([^?\"')]+\.svg)", content))
    missing = sorted(name for name in names if not (ASSET_DIR / name).is_file())
    if missing:
        health.add(
            component,
            "Cached section references missing generated assets: " + ", ".join(missing),
        )
        return ""
    active_assets.update(names)
    return content


def render_section(section: Section, *, include_separator: bool) -> str:
    """Render one all-or-nothing section with its owned trailing separator.

    The separator belongs logically to the preceding visible section. Never emit
    a title/separator for empty content, and never make sections reason about the
    visibility of their neighbors themselves.
    """
    content = section.content
    if not content:
        return ""
    body: list[str] = [
        f"<!-- SECTION:{section.key}:START -->",
        '<div align="center">',
        "",
    ]
    if section.section_title:
        body.extend([f"### {section.section_title}", ""])
    body.extend(
        [
            f"<!-- SECTION:{section.key}:CONTENT:START -->",
            content,
            f"<!-- SECTION:{section.key}:CONTENT:END -->",
        ]
    )
    if include_separator:
        body.extend(["", "![separator][separator]"])
    body.extend(["", "</div>", f"<!-- SECTION:{section.key}:END -->"])
    return "\n".join(body)


def render_license_footer() -> str:
    """Render the always-visible licensing footer after the last content section.

    The footer is intentionally not a ``Section``: it has no section heading or
    card and should never compete with professional profile content. ``LICENSE``
    and ``NOTICE`` use repository-relative links so they always target the files
    published alongside README.md on the active branch.
    """
    return (
        '<p align="center">\n'
        '  <sub>Code &amp; reusable design licensed under '
        '<strong>EUPL-1.2-or-later</strong> &middot; See '
        '<a href="./LICENSE">LICENSE</a> &amp; '
        '<a href="./NOTICE">NOTICE</a></sub>\n'
        '</p>'
    )


def assemble_sections(sections: list[Section], *, footer: str = "") -> str:
    """Filter empty sections, then assemble them with canonical separators.

    Empty sections leave no visual residue. Separators belong to the preceding
    visible section. When a footer is present, the last visible section also owns
    a trailing separator so the footer remains a distinct technical epilogue.
    """
    visible = [section for section in sections if section.content]
    blocks = [
        render_section(
            section,
            include_separator=(index < len(visible) - 1) or bool(footer.strip()),
        )
        for index, section in enumerate(visible)
    ]
    if footer.strip():
        blocks.append(footer.strip())
    return "\n\n".join(blocks)

def build_development_workflow_svg(*, style: SvgStyle) -> str:
    """Render the profile's development and publication workflow."""
    width = SVG_WIDTH
    divider_opacity = style.divider_opacity
    secondary_opacity = style.secondary_opacity

    # Workflow cards intentionally use the same internal rhythm and height.
    card_padding_y = 18
    title_baseline = card_padding_y + 11
    divider_offset = 46
    footer_offset = 70
    main_card_height = WORKFLOW_CARD_HEIGHT
    secondary_card_height = WORKFLOW_CARD_HEIGHT

    def main_card(
        x: int,
        y: int,
        node_width: int,
        title: str,
        footer: str,
    ) -> str:
        divider_y = y + divider_offset
        title_y = y + title_baseline
        footer_y = y + footer_offset
        return (
            svg_card_frame(x, y, node_width, main_card_height, style=style)
            + f'<text class="card-title" x="{x + node_width / 2:.1f}" y="{title_y:.1f}" '
            'text-anchor="middle">'
            f'{svg_escape(title)}</text>'
            + svg_horizontal_divider(
                x,
                divider_y,
                node_width,
                opacity=divider_opacity,
            )
            + f'<text class="card-description" x="{x + node_width / 2:.1f}" y="{footer_y:.1f}" '
            'text-anchor="middle">'
            f'{svg_escape(footer)}</text>'
        )

    def secondary_card(
        x: int,
        y: int,
        node_width: int,
        title: str,
        footer: str,
    ) -> str:
        title_y = y + title_baseline
        divider_y = y + divider_offset
        footer_y = y + footer_offset
        return (
            svg_card_frame(x, y, node_width, secondary_card_height, style=style)
            + f'<text class="card-title" x="{x + node_width / 2:.1f}" y="{title_y:.1f}" '
            'text-anchor="middle">'
            f'{svg_escape(title)}</text>'
            + svg_horizontal_divider(
                x,
                divider_y,
                node_width,
                opacity=divider_opacity,
            )
            + f'<text class="card-description" x="{x + node_width / 2:.1f}" y="{footer_y:.1f}" '
            'text-anchor="middle">'
            f'{svg_escape(footer)}</text>'
        )

    def connector_label_width(label: str) -> float:
        # The connector labels use a monospace stack, so a stable width estimate
        # keeps the same horizontal padding around every label.
        glyph_width = CONNECTOR_LABEL_FONT_SIZE * 0.60
        text_width = (len(label) * glyph_width) + (
            max(len(label) - 1, 0) * CONNECTOR_LABEL_LETTER_SPACING
        )
        return text_width + (2 * CONNECTOR_LABEL_PADDING_X)

    def connector_label(
        x: float,
        y: int,
        label: str,
    ) -> str:
        opacity = 0.82
        label_width = connector_label_width(label)
        return (
            f'<rect x="{x:.1f}" y="{y}" width="{label_width:.1f}" '
            f'height="{CONNECTOR_LABEL_HEIGHT}" rx="8" fill="none" '
            f'stroke="#{PROFILE_COLOR}" stroke-width="1.2" '
            f'stroke-opacity="{opacity:.2f}"/>'
            f'<text class="connector-label" x="{x + label_width / 2:.1f}" y="{y + 18}" '
            f'fill-opacity="{min(opacity + 0.12, 1.0):.2f}" text-anchor="middle">'
            f'{svg_escape(label)}</text>'
        )

    # The primary workflow forms one compact vertical column aligned with the
    # left edge of the Languages / Stats cards. GitLab Pages and Docker Hub are
    # optional publication branches whose right edges align with those cards.
    main_x = 0
    main_w = WORKFLOW_MAIN_CARD_WIDTH
    local_y = WORKFLOW_TOP
    gitlab_y = (
        local_y
        + main_card_height
        + CONNECTOR_LABEL_HEIGHT
        + (2 * WORKFLOW_TRANSITION_GAP)
    )
    github_y = (
        gitlab_y
        + main_card_height
        + CONNECTOR_LABEL_HEIGHT
        + (2 * WORKFLOW_TRANSITION_GAP)
    )

    secondary_w = WORKFLOW_SECONDARY_CARD_WIDTH
    secondary_x = width - secondary_w
    pages_y = int(gitlab_y + main_card_height / 2 - secondary_card_height / 2)
    docker_y = int(github_y + main_card_height / 2 - secondary_card_height / 2)

    local = main_card(
        main_x,
        local_y,
        main_w,
        "LOCAL DEVELOPMENT",
        "GPG-signed commits & tags · Ed25519",
    )
    gitlab = main_card(
        main_x,
        gitlab_y,
        main_w,
        "GITLAB",
        "CI/CD · Tests · Security",
    )
    github = main_card(
        main_x,
        github_y,
        main_w,
        "GITHUB",
        "Sharing · Community · Discovery",
    )
    pages = secondary_card(
        secondary_x,
        pages_y,
        secondary_w,
        "GITLAB PAGES",
        "Static websites only",
    )
    docker = secondary_card(
        secondary_x,
        docker_y,
        secondary_w,
        "DOCKER HUB",
        "Image projects only",
    )

    flow_x = main_x + main_w / 2
    push_label_y = local_y + main_card_height + WORKFLOW_TRANSITION_GAP
    mirror_label_y = gitlab_y + main_card_height + WORKFLOW_TRANSITION_GAP
    publish_gap_left = main_x + main_w
    publish_gap_right = secondary_x
    website_label_width = connector_label_width("PUBLISH WEBSITE")
    website_label_x = publish_gap_left + (
        publish_gap_right - publish_gap_left - website_label_width
    ) / 2
    website_line_y = gitlab_y + main_card_height / 2
    website_label_y = int(
        website_line_y - CONNECTOR_LABEL_HEIGHT - PUBLISH_LABEL_LINE_GAP
    )
    images_label_width = connector_label_width("PUBLISH IMAGES")
    images_label_x = publish_gap_left + (
        publish_gap_right - publish_gap_left - images_label_width
    ) / 2
    images_line_y = github_y + main_card_height / 2
    images_label_y = int(
        images_line_y - CONNECTOR_LABEL_HEIGHT - PUBLISH_LABEL_LINE_GAP
    )

    height = int(
        max(
            github_y + main_card_height,
            pages_y + secondary_card_height,
            docker_y + secondary_card_height,
        )
    )

    connectors = (
        # LOCAL -> GITLAB.
        f'<line x1="{flow_x:.1f}" y1="{local_y + main_card_height}" '
        f'x2="{flow_x:.1f}" y2="{push_label_y}" '
        f'stroke="#{PROFILE_COLOR}" stroke-width="2.2" stroke-opacity="0.88"/>'
        f'<line x1="{flow_x:.1f}" y1="{push_label_y + CONNECTOR_LABEL_HEIGHT}" '
        f'x2="{flow_x:.1f}" y2="{gitlab_y - 5}" '
        f'stroke="#{PROFILE_COLOR}" stroke-width="2.2" stroke-opacity="0.88" '
        'marker-end="url(#arrowMain)"/>'
        # GITLAB -> GITHUB.
        f'<line x1="{flow_x:.1f}" y1="{gitlab_y + main_card_height}" '
        f'x2="{flow_x:.1f}" y2="{mirror_label_y}" '
        f'stroke="#{PROFILE_COLOR}" stroke-width="2.2" stroke-opacity="0.88"/>'
        f'<line x1="{flow_x:.1f}" y1="{mirror_label_y + CONNECTOR_LABEL_HEIGHT}" '
        f'x2="{flow_x:.1f}" y2="{github_y - 5}" '
        f'stroke="#{PROFILE_COLOR}" stroke-width="2.2" stroke-opacity="0.88" '
        'marker-end="url(#arrowMain)"/>'
        # GITLAB -> GITLAB PAGES: secondary, static-website-only publication path.
        f'<line x1="{main_x + main_w}" y1="{website_line_y:.1f}" '
        f'x2="{secondary_x - 5}" y2="{website_line_y:.1f}" '
        f'stroke="#{PROFILE_COLOR}" stroke-width="1.8" '
        f'stroke-opacity="{secondary_opacity:.2f}" '
        'stroke-dasharray="6 6" marker-end="url(#arrowSecondary)"/>'
        # GITHUB -> DOCKER HUB: secondary, image-project-only publication path.
        f'<line x1="{main_x + main_w}" y1="{images_line_y:.1f}" '
        f'x2="{secondary_x - 5}" y2="{images_line_y:.1f}" '
        f'stroke="#{PROFILE_COLOR}" stroke-width="1.8" '
        f'stroke-opacity="{secondary_opacity:.2f}" '
        'stroke-dasharray="6 6" marker-end="url(#arrowSecondary)"/>'
    )

    push_label_width = connector_label_width("PUSH CHANGES")
    mirror_label_width = connector_label_width("AUTOMATIC MIRRORING")
    labels = (
        connector_label(
            flow_x - push_label_width / 2,
            push_label_y,
            "PUSH CHANGES",
        )
        + connector_label(
            flow_x - mirror_label_width / 2,
            mirror_label_y,
            "AUTOMATIC MIRRORING",
        )
        + connector_label(
            website_label_x,
            website_label_y,
            "PUBLISH WEBSITE",
        )
        + connector_label(
            images_label_x,
            images_label_y,
            "PUBLISH IMAGES",
        )
    )

    arrow_defs = (
        '<marker id="arrowMain" viewBox="0 0 10 10" refX="8.5" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto">'
        f'<path d="M0 0 L10 5 L0 10 Z" fill="#{PROFILE_COLOR}" fill-opacity="0.92"/>'
        '</marker>'
        '<marker id="arrowSecondary" viewBox="0 0 10 10" refX="8.5" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto">'
        f'<path d="M0 0 L10 5 L0 10 Z" fill="#{PROFILE_COLOR}" '
        f'fill-opacity="{secondary_opacity:.2f}"/>'
        '</marker>'
    )

    return svg_card_document(
        width=width,
        height=height,
        aria_label="Development workflow",
        style=style,
        content=connectors + local + gitlab + pages + github + docker + labels,
        include_frame=False,
        defs_extra=arrow_defs,
    )


def build_picture_block(
    path: Path,
    alt: str,
    *,
    width: int | None = None,
) -> str:
    asset_url = versioned_asset_url(path)
    escaped_alt = html.escape(alt, quote=True)
    width_attr = f' width="{width}"' if width else ""
    return (
        '<p align="center">\n'
        f'  <img src="{asset_url}" alt="{escaped_alt}"{width_attr}>\n'
        '</p>'
    )

def prune_generated_assets(active_assets: set[str]) -> None:
    """Remove every generated artifact not used by the current rendered README."""
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    for path in ASSET_DIR.iterdir():
        if path.is_file() and path.name not in active_assets:
            path.unlink()


def verify_generated_asset_references(text: str) -> None:
    """Fail before publication if README references a missing generated SVG."""
    names = set(re.findall(r"\./assets/generated/([^?\"')]+\.svg)", text))
    missing = sorted(name for name in names if not (ASSET_DIR / name).is_file())
    if missing:
        raise RuntimeError(
            "README references missing generated assets: " + ", ".join(missing)
        )



def _published_readme() -> tuple[bool, str]:
    exists = README.exists()
    return exists, README.read_text(encoding="utf-8") if exists else ""


def _profile_snapshot(
    published_exists: bool,
    published: str,
    health: HealthReport,
) -> ProfileSnapshot | None:
    try:
        return PROVIDER.profile()
    except Exception as exc:
        health.add(f"{PROVIDER.display_name} profile", exc)
        if published_exists and published.strip():
            README.write_text(published, encoding="utf-8")
            return None
        raise


def _blog_feed_settings(config: JsonObject, health: HealthReport) -> tuple[str, bool]:
    blog_config = config.get("blog", {})
    if not is_json_object(blog_config):
        health.add("Blog feed configuration", "[blog] must be a TOML table")
        return "", False
    raw_url = str(blog_config.get("feed_url") or "").strip()
    feed_url = valid_external_url(raw_url)
    if raw_url and not feed_url:
        health.add(
            "Blog feed configuration",
            "[blog].feed_url must be a valid HTTPS URL",
        )
        return "", False
    return feed_url, True


def _build_profile_section(
    display_name: str,
    bio: str,
    avatar_url: str,
    *,
    health: HealthReport,
    active_assets: set[str],
) -> Section:
    avatar_block = build_avatar_block(
        avatar_url,
        display_name,
        health=health,
        active_assets=active_assets,
    )
    parts = [avatar_block, f"## {display_name}"]
    if bio:
        parts.append(f"**{bio}**")
    return Section("PROFILE", None, tuple(parts))


def _repository_snapshot(health: HealthReport) -> list[JsonObject] | None:
    return PROVIDER.repositories(health)


def _language_snapshot(
    repos: list[JsonObject] | None,
    health: HealthReport,
) -> tuple[dict[str, int], bool]:
    if repos is None:
        return {}, False
    return PROVIDER.languages(repos, health)


def _language_card_path(
    languages: dict[str, int],
    complete: bool,
    repos_available: bool,
    *,
    health: HealthReport,
    active_assets: set[str],
) -> Path | None:
    source_available = repos_available and complete
    if source_available and not languages:
        return None
    return safe_svg_card(
        "languages",
        lambda style: build_languages_svg(languages, style=style),
        health=health,
        component="Most Used Languages card",
        active_assets=active_assets,
        source_available=source_available,
    )


def _stats_card_path(
    repos: list[JsonObject] | None,
    contribution_snapshot: ContributionSnapshot | None,
    merged_pull_requests: int | None,
    *,
    health: HealthReport,
    active_assets: set[str],
) -> Path | None:
    if (
        repos is None
        or contribution_snapshot is None
        or merged_pull_requests is None
    ):
        return safe_svg_card(
            "github-stats",
            lambda style: "",
            health=health,
            component="GitHub Stats card",
            active_assets=active_assets,
            source_available=False,
        )
    snapshot = PROVIDER.stats_snapshot(
        repos, contribution_snapshot, merged_pull_requests
    )
    return safe_svg_card(
        "github-stats",
        lambda style: build_stats_svg(snapshot, style=style),
        health=health,
        component="GitHub Stats card",
        active_assets=active_assets,
    )


def _featured_projects_content(
    published: str,
    pinned: list[JsonObject] | None,
    *,
    health: HealthReport,
    active_assets: set[str],
) -> str:
    if pinned is None:
        cached = extract_section_content(published, "FEATURED-PROJECTS")
        return preserve_cached_generated_assets(
            cached,
            active_assets=active_assets,
            health=health,
            component="Featured Projects cache",
        )
    if not pinned:
        return ""
    return build_featured_projects_content(
        pinned, health=health, active_assets=active_assets
    )


def _practice_content(
    config: JsonObject,
    *,
    health: HealthReport,
    active_assets: set[str],
) -> str:
    raw_section: Any = config.get("security_practice")
    section: JsonObject = raw_section if is_json_object(raw_section) else {}
    htb_url = valid_external_url(section.get("hack_the_box"))
    cyberdefenders_url = valid_external_url(section.get("cyberdefenders"))
    htb = fetch_hack_the_box_snapshot(health) if htb_url else None
    cyberdefenders = (
        fetch_cyberdefenders_snapshot(cyberdefenders_url, health)
        if cyberdefenders_url
        else None
    )
    return build_security_practice_content(
        config,
        health=health,
        active_assets=active_assets,
        hack_the_box=htb,
        cyberdefenders=cyberdefenders,
    )


def _research_content(
    config: JsonObject,
    published: str,
    *,
    health: HealthReport,
    active_assets: set[str],
) -> str:
    raw_section: Any = config.get("security_research")
    section: JsonObject = raw_section if is_json_object(raw_section) else {}
    profile_url = valid_external_url(section.get("hackerone"))
    hackerone = fetch_hackerone_snapshot(profile_url, health) if profile_url else None
    if profile_url and hackerone is None:
        cached = extract_section_content(published, "SECURITY-RESEARCH")
        return preserve_cached_generated_assets(
            cached,
            active_assets=active_assets,
            health=health,
            component="Security Research cache",
        )
    return build_security_research_content(
        config,
        health=health,
        active_assets=active_assets,
        hackerone=hackerone,
    )


def _picture_block(path: Path | None, alt: str) -> str:
    return build_picture_block(path, alt) if path else ""


def _cached_blog_content(
    published: str,
    posts: list[tuple[str, str]] | None,
    content: str,
) -> str:
    if posts is not None:
        return content
    return extract_section_content(published, "LATEST-BLOG-POSTS") or content


def _cached_community_content(
    published: str,
    followers: list[JsonObject] | None,
    content: str,
) -> str:
    if followers is not None:
        return content
    cached = extract_section_content(published, "COMMUNITY")
    if "temporarily unavailable" in cached.lower():
        cached = ""
    return cached or content


def _contact_references(
    website_url: str,
    linkedin_url: str,
    github_url: str,
    gitlab_url: str,
    gpg_url: str,
) -> str:
    return "\n".join(
        [
            f"[website_url]: {website_url} 'Website'",
            f"[linkedin_url]: {linkedin_url} 'LinkedIn'",
            f"[github_url]: {github_url} 'GitHub Profile'",
            f"[gitlab_url]: {gitlab_url} 'GitLab Profile'",
            f"[gpg_url]: {gpg_url} 'Public GPG Key'",
        ]
    )


def _write_rendered_readme(
    template: str,
    sections: list[Section],
    contact_references: str,
    active_assets: set[str],
) -> None:
    rendered = assemble_sections(sections, footer=render_license_footer())
    text = replace_block(
        template, "<!-- README:START -->", "<!-- README:END -->", rendered
    )
    text = replace_block(
        text,
        "<!-- CONTACT-URLS:START -->",
        "<!-- CONTACT-URLS:END -->",
        contact_references,
    )
    text = replace_reference(
        text,
        "separator",
        versioned_asset_url(Path("assets/separator.gif")),
    )
    prune_generated_assets(active_assets)
    verify_generated_asset_references(text)
    README.write_text(text, encoding="utf-8")


def _summary_count(value: JsonArray | JsonObject | None, available: bool) -> int | str:
    if not available or value is None:
        return "cached"
    return len(value)


def main(health: HealthReport) -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    template = TEMPLATE.read_text(encoding="utf-8")
    published_exists, published = _published_readme()
    config = load_profile_config(health)
    active_assets: set[str] = set()

    profile = _profile_snapshot(published_exists, published, health)
    if profile is None:
        return
    display_name = profile.display_name
    bio = profile.bio
    avatar_url = profile.avatar_url
    website_url = profile.website_url
    social_accounts = PROVIDER.social_accounts(health)
    website_url = website_url or current_reference(published, "website_url")
    gitlab_username = derive_gitlab_username(published, social_accounts)
    github_url = f"https://github.com/{USERNAME}"
    gitlab_url = f"https://gitlab.com/{gitlab_username}"
    gpg_url = f"https://gitlab.com/{gitlab_username}.gpg"
    blog_feed_url, blog_config_valid = _blog_feed_settings(config, health)

    profile_section = _build_profile_section(
        display_name,
        bio,
        avatar_url,
        health=health,
        active_assets=active_assets,
    )
    contact_content = build_contact_content(
        website_url=website_url,
        linkedin_url=LINKEDIN_URL,
        github_url=github_url,
        gitlab_url=gitlab_url,
        gpg_url=gpg_url,
    )
    repos = _repository_snapshot(health)
    languages, languages_complete = _language_snapshot(repos, health)
    language_path = _language_card_path(
        languages,
        languages_complete,
        repos is not None,
        health=health,
        active_assets=active_assets,
    )
    contribution_snapshot = PROVIDER.contribution_snapshot(health)
    merged_pull_requests = PROVIDER.merged_request_count(health)
    stats_path = _stats_card_path(
        repos,
        contribution_snapshot,
        merged_pull_requests,
        health=health,
        active_assets=active_assets,
    )
    activity_path = write_activity_svg(
        contribution_snapshot, health=health, active_assets=active_assets
    )
    workflow_path = safe_svg_card(
        "development-workflow",
        lambda style: build_development_workflow_svg(style=style),
        health=health,
        component="Development Workflow card",
        active_assets=active_assets,
    )

    pinned = PROVIDER.featured_projects(health)
    featured = _featured_projects_content(
        published, pinned, health=health, active_assets=active_assets
    )
    followers = PROVIDER.followers(health)
    community = build_community_block(followers)
    blog_posts = fetch_blog_posts(blog_feed_url, health) if blog_config_valid else None
    blog = build_blog_content(blog_posts)
    practice = _practice_content(config, health=health, active_assets=active_assets)
    research = _research_content(
        config, published, health=health, active_assets=active_assets
    )
    certifications = build_certifications_content(
        config, health=health, active_assets=active_assets
    )

    sections = [
        profile_section,
        Section("CONTACT", "CONTACT", (contact_content,)),
        Section(
            "DEVELOPMENT-WORKFLOW",
            "DEVELOPMENT WORKFLOW",
            (_picture_block(workflow_path, "Development Workflow"),),
        ),
        Section("FEATURED-PROJECTS", "FEATURED PROJECTS", (featured,)),
        Section("SECURITY-PRACTICE", "SECURITY PRACTICE", (practice,)),
        Section("SECURITY-RESEARCH", "SECURITY RESEARCH", (research,)),
        Section("CERTIFICATIONS", "CERTIFICATIONS", (certifications,)),
        Section(
            "MOST-USED-LANGUAGES",
            "MOST USED LANGUAGES",
            (_picture_block(language_path, "Most Used Languages"),),
        ),
        Section(
            "GITHUB-STATS",
            "GITHUB STATS",
            (
                _picture_block(stats_path, "GitHub Stats"),
                _picture_block(activity_path, "GitHub Activity · 365 Days"),
            ),
        ),
    ]
    blog = _cached_blog_content(published, blog_posts, blog)
    community = _cached_community_content(published, followers, community)
    sections.append(Section("LATEST-BLOG-POSTS", "LATEST BLOG POSTS", (blog,)))
    sections.append(Section("COMMUNITY", "COMMUNITY", (community,)))

    references = _contact_references(
        website_url, LINKEDIN_URL, github_url, gitlab_url, gpg_url
    )
    _write_rendered_readme(template, sections, references, active_assets)
    print(
        f"Updated README for {USERNAME}: "
        f"{_summary_count(followers, followers is not None)} followers, "
        f"{_summary_count(repos, repos is not None)} public repositories, "
        f"{_summary_count(pinned, pinned is not None)} pinned projects, "
        f"{_summary_count(languages, languages_complete)} languages."
    )



if __name__ == "__main__":
    report = HealthReport()
    try:
        main(report)
    except Exception as exc:
        report.add("README generator", exc)
        raise
    finally:
        report.write()
