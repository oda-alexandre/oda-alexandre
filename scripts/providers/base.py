# SPDX-FileCopyrightText: 2024-2026 ODA Alexandre
# SPDX-License-Identifier: EUPL-1.2+

"""Forge-neutral data contracts used by the profile README generator."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Protocol, TypeGuard

JsonObject = dict[str, Any]
JsonArray = list[Any]
JsonContainer = JsonObject | JsonArray


def is_json_object(value: object) -> TypeGuard[JsonObject]:
    """Narrow an untrusted mapping-shaped value to the project JSON object type."""
    return isinstance(value, dict)


def is_json_array(value: object) -> TypeGuard[JsonArray]:
    """Narrow an untrusted sequence-shaped value to the project JSON array type."""
    return isinstance(value, list)


class HealthReporter(Protocol):
    """Minimal health-reporting interface required by forge providers."""

    def add(self, component: str, message: object) -> None: ...


@dataclass(frozen=True)
class SocialAccount:
    """One normalized public social-profile destination exposed by a forge."""

    provider: str
    url: str


@dataclass(frozen=True)
class ProfileSnapshot:
    """Public identity fields needed by the forge-independent renderer."""

    display_name: str
    bio: str
    avatar_url: str
    website_url: str
    profile_url: str


@dataclass(frozen=True)
class FeaturedProject:
    """Forge-neutral portfolio project rendered by the shared project card."""

    identity: str
    name: str
    url: str
    description: str
    owner: str
    primary_language: str
    stars: int
    contributed: bool


@dataclass(frozen=True)
class CommunityBadge:
    """One linked image badge used by the forge-neutral COMMUNITY renderer."""

    url: str
    image_url: str
    alt: str


@dataclass(frozen=True)
class CommunitySnapshot:
    """Public follower presentation prepared by a forge provider."""

    summary: CommunityBadge
    follower_count: int
    followers: tuple[CommunityBadge, ...]


@dataclass(frozen=True)
class ContributionDay:
    """One normalized contribution-calendar day."""

    date: dt.date
    count: int
    level: str
    weekday: int


@dataclass(frozen=True)
class ContributionSnapshot:
    """Rolling public contribution activity used by forge activity cards."""

    total: int
    reviews: int
    repositories_contributed: int
    weeks: tuple[tuple[ContributionDay, ...], ...]


@dataclass(frozen=True)
class ForgeMetric:
    """One compact professional metric displayed in a forge statistics card."""

    label: str
    value: int
    period: str = ""


@dataclass(frozen=True)
class ForgeStatsSnapshot:
    """Forge-specific professional signals normalized for the shared renderer."""

    metrics: tuple[ForgeMetric, ...]
    aria_label: str


class ForgeProvider(Protocol):
    """Data-source contract implemented by each supported development forge."""

    key: str
    display_name: str
    username: str
    supports_activity: bool
    supports_community: bool
    language_asset_stem: str
    featured_summary_label: str

    def profile(self) -> ProfileSnapshot: ...

    def social_accounts(self, health: HealthReporter) -> list[SocialAccount]: ...

    def repositories(self, health: HealthReporter) -> list[JsonObject] | None: ...

    def languages(
        self,
        repositories: list[JsonObject],
        health: HealthReporter,
    ) -> tuple[dict[str, int], bool]: ...

    def contribution_snapshot(
        self,
        health: HealthReporter,
    ) -> ContributionSnapshot | None: ...

    def stats_snapshot(
        self,
        repositories: list[JsonObject],
        contributions: ContributionSnapshot | None,
        health: HealthReporter,
    ) -> ForgeStatsSnapshot | None: ...

    def featured_projects(
        self,
        health: HealthReporter,
    ) -> list[FeaturedProject] | None: ...

    def community(self, health: HealthReporter) -> CommunitySnapshot | None: ...
