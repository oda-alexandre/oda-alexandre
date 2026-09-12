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
class ProfileSnapshot:
    """Public identity fields needed by the forge-independent renderer."""

    display_name: str
    bio: str
    avatar_url: str
    website_url: str


@dataclass(frozen=True)
class ContributionDay:
    """One normalized contribution-calendar day."""

    date: dt.date
    count: int
    level: str
    weekday: int


@dataclass(frozen=True)
class ContributionSnapshot:
    """Rolling public contribution activity and collaboration metrics."""

    total: int
    reviews: int
    repositories_contributed: int
    weeks: tuple[tuple[ContributionDay, ...], ...]


@dataclass(frozen=True)
class ForgeStatsSnapshot:
    """Professional forge signals displayed in the compact stats card."""

    merged_requests: int
    reviews: int
    repositories_contributed: int
    stars_earned: int


class ForgeProvider(Protocol):
    """Data-source contract implemented by each supported development forge."""

    key: str
    display_name: str
    username: str

    def profile(self) -> ProfileSnapshot: ...

    def social_accounts(self, health: HealthReporter) -> list[JsonObject]: ...

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

    def merged_request_count(self, health: HealthReporter) -> int | None: ...

    def stats_snapshot(
        self,
        repositories: list[JsonObject],
        contributions: ContributionSnapshot,
        merged_requests: int,
    ) -> ForgeStatsSnapshot: ...

    def featured_projects(self, health: HealthReporter) -> list[JsonObject] | None: ...

    def followers(self, health: HealthReporter) -> list[JsonObject] | None: ...
