# SPDX-FileCopyrightText: 2024-2026 ODA Alexandre
# SPDX-License-Identifier: EUPL-1.2+

"""GitLab data provider for the profile README generator.

Public GitLab REST endpoints provide the professional profile without a
persistent credential. The provider deliberately does not use a personal,
project, or group access token; GitLab CI's native CI_JOB_TOKEN is reserved for
workflow operations whose endpoints explicitly support it.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode, urlparse

from .base import (
    CommunitySnapshot,
    ContributionSnapshot,
    FeaturedProject,
    ForgeMetric,
    ForgeStatsSnapshot,
    HealthReporter,
    JsonContainer,
    JsonObject,
    ProfileSnapshot,
    SocialAccount,
    is_json_array,
    is_json_object,
)

JSON_MEDIA_TYPE = "application/json"
FEATURED_TOPIC = "profile-featured"
PAGE_SIZE = 100


class GitLabProvider:
    """Fetch and normalize public GitLab data without private profile leakage."""

    key = "gitlab"
    display_name = "GitLab"
    supports_activity = False
    supports_community = False
    language_asset_stem = "gitlab-languages"
    featured_summary_label = "featured projects"

    def __init__(self, username: str, api_base_url: str) -> None:
        self.username = username
        self._api_base_url = api_base_url.rstrip("/")
        self._user_cache: JsonObject | None = None
        self._repositories_cache: list[JsonObject] | None = None
        self._contributed_projects_cache: list[JsonObject] | None = None

    @classmethod
    def from_environment(cls) -> "GitLabProvider":
        username = (
            os.environ.get("GITLAB_PROFILE_USERNAME", "").strip()
            or os.environ.get("CI_PROJECT_ROOT_NAMESPACE", "").strip()
            or os.environ.get("CI_PROJECT_NAMESPACE", "").strip()
        )
        if not username:
            raise SystemExit(
                "GITLAB_PROFILE_USERNAME, CI_PROJECT_ROOT_NAMESPACE, or "
                "CI_PROJECT_NAMESPACE is required"
            )
        api_base_url = (
            os.environ.get("GITLAB_API_BASE_URL", "").strip()
            or os.environ.get("CI_API_V4_URL", "").strip()
            or "https://gitlab.com/api/v4"
        )
        if not api_base_url:
            raise SystemExit("GITLAB_API_BASE_URL must not be empty")
        parsed = urlparse(api_base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise SystemExit("GITLAB_API_BASE_URL must be a valid HTTPS URL")
        return cls(username=username, api_base_url=api_base_url)

    def _json(
        self,
        path: str,
        *,
        query: dict[str, object] | None = None,
    ) -> JsonContainer:
        url = f"{self._api_base_url}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{urlencode(query)}"
        headers = {
            "Accept": JSON_MEDIA_TYPE,
            "User-Agent": f"{self.username}-profile-readme",
        }
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"GitLab API HTTP {exc.code} for /{path.lstrip('/')}: {body}"
            ) from exc

    @staticmethod
    def _normalize_url(value: str) -> str:
        value = (value or "").strip()
        if not value:
            return ""
        parsed = urlparse(value)
        if not parsed.scheme:
            value = "https://" + value
        return value.rstrip("/")

    def _user(self) -> JsonObject:
        if self._user_cache is not None:
            return self._user_cache
        result = self._json("users", query={"username": self.username})
        if not is_json_array(result):
            raise RuntimeError("Unexpected GitLab user lookup response")
        matches = [
            user
            for user in result
            if is_json_object(user)
            and str(user.get("username") or "").casefold() == self.username.casefold()
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"GitLab user lookup did not return exactly one match for {self.username}"
            )
        user = matches[0]
        self._user_cache = user
        return self._user_cache

    def profile(self) -> ProfileSnapshot:
        user = self._user()
        avatar_url = str(user.get("avatar_url") or "").strip()
        if not avatar_url:
            raise RuntimeError("GitLab profile does not expose an avatar URL")
        profile_url = self._normalize_url(
            str(user.get("web_url") or f"https://gitlab.com/{self.username}")
        )
        return ProfileSnapshot(
            display_name=str(user.get("name") or user.get("username") or self.username),
            bio=str(user.get("bio") or "").strip(),
            avatar_url=avatar_url,
            website_url=self._normalize_url(str(user.get("website_url") or "")),
            profile_url=profile_url,
        )

    def social_accounts(self, health: HealthReporter) -> list[SocialAccount]:
        try:
            user = self._user()
            accounts = [
                SocialAccount(
                    provider="gitlab",
                    url=self._normalize_url(
                        str(user.get("web_url") or f"https://gitlab.com/{self.username}")
                    ),
                )
            ]
            return accounts
        except Exception as exc:  # noqa: BLE001
            health.add("GitLab social accounts", exc)
            return []

    def _paged_list(
        self,
        path: str,
        *,
        query: dict[str, object] | None = None,
    ) -> list[JsonObject]:
        items: list[JsonObject] = []
        page = 1
        while True:
            params: dict[str, object] = dict(query or {})
            params.update({"per_page": PAGE_SIZE, "page": page})
            payload = self._json(path, query=params)
            if not is_json_array(payload):
                raise RuntimeError(f"Unexpected GitLab list response for /{path}")
            batch = [item for item in payload if is_json_object(item)]
            items.extend(batch)
            if len(payload) < PAGE_SIZE:
                return items
            page += 1

    def repositories(self, health: HealthReporter) -> list[JsonObject] | None:
        if self._repositories_cache is not None:
            return self._repositories_cache
        try:
            listed = self._paged_list(
                f"users/{quote(self.username, safe='')}/projects",
                query={
                    "visibility": "public",
                    "order_by": "last_activity_at",
                    "sort": "desc",
                },
            )
            repositories: list[JsonObject] = []
            for repository in listed:
                project_id = repository.get("id")
                if project_id is None:
                    raise RuntimeError("GitLab project list returned an entry without an id")
                detail = self._json(f"projects/{quote(str(project_id), safe='')}")
                if not is_json_object(detail):
                    raise RuntimeError(
                        f"Unexpected GitLab project response for id {project_id}"
                    )
                if str(detail.get("visibility") or "") != "public":
                    continue
                repositories.append(detail)
            self._repositories_cache = repositories
            return self._repositories_cache
        except Exception as exc:  # noqa: BLE001
            health.add("GitLab projects", exc)
            return None

    @staticmethod
    def _is_fork(repository: JsonObject) -> bool:
        return is_json_object(repository.get("forked_from_project"))

    def _project_languages(self, repository: JsonObject) -> dict[str, float] | None:
        if self._is_fork(repository):
            return {}
        project_id = repository.get("id")
        if project_id is None:
            return {}
        try:
            payload = self._json(f"projects/{quote(str(project_id), safe='')}/languages")
            if not is_json_object(payload):
                raise RuntimeError("Unexpected GitLab languages API response")
            languages: dict[str, float] = {}
            for language, percentage in payload.items():
                if isinstance(percentage, bool) or not isinstance(percentage, (int, float)):
                    continue
                numeric = float(percentage)
                if numeric > 0:
                    languages[str(language)] = numeric
            return languages
        except Exception:  # noqa: BLE001
            return None

    def languages(
        self,
        repositories: list[JsonObject],
        health: HealthReporter,
    ) -> tuple[dict[str, int], bool]:
        """Aggregate GitLab's per-project language percentages with equal project weight."""
        totals: dict[str, int] = {}
        failures: list[str] = []
        for repository in repositories:
            project_languages = self._project_languages(repository)
            if project_languages is None:
                failures.append(str(repository.get("path_with_namespace") or "unknown"))
                continue
            for language, percentage in project_languages.items():
                # GitLab exposes percentages rather than byte counts. Scaling to an
                # integer preserves deterministic relative weights for the shared
                # renderer while giving every public non-fork project equal weight.
                totals[language] = totals.get(language, 0) + round(percentage * 1000)
        if failures:
            sample = ", ".join(failures[:5])
            suffix = "" if len(failures) <= 5 else f" (+{len(failures) - 5} more)"
            health.add(
                "GitLab languages",
                f"Unable to refresh language data for {len(failures)} projects: "
                f"{sample}{suffix}",
            )
        return totals, not failures

    def contribution_snapshot(
        self,
        health: HealthReporter,
    ) -> ContributionSnapshot | None:
        # GitLab does not expose its profile contribution calendar through a
        # supported public API. Unsupported activity is omitted, not degraded.
        del health
        return None

    @staticmethod
    def _rolling_365_window() -> tuple[dt.datetime, dt.datetime]:
        now = dt.datetime.now(dt.timezone.utc)
        start = (now - dt.timedelta(days=364)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return start, now

    def _contributed_projects(self) -> list[JsonObject]:
        if self._contributed_projects_cache is None:
            self._contributed_projects_cache = self._paged_list(
                f"users/{quote(self.username, safe='')}/contributed_projects",
                query={"order_by": "last_activity_at", "sort": "desc"},
            )
        return self._contributed_projects_cache

    def _merged_request_count(
        self,
        repositories: list[JsonObject],
        contributed_projects: list[JsonObject],
        health: HealthReporter,
    ) -> int | None:
        start, now = self._rolling_365_window()
        projects: dict[str, JsonObject] = {}
        for project in repositories:
            if self._is_fork(project):
                continue
            project_id = project.get("id")
            if project_id is not None:
                projects[str(project_id)] = project
        for project in contributed_projects:
            project_id = project.get("id")
            if project_id is not None:
                projects[str(project_id)] = project

        total = 0
        failures: list[str] = []
        for project_id, project in projects.items():
            try:
                merged = self._paged_list(
                    f"projects/{quote(project_id, safe='')}/merge_requests",
                    query={
                        "state": "merged",
                        "author_username": self.username,
                        "merged_after": start.isoformat().replace("+00:00", "Z"),
                        "merged_before": now.isoformat().replace("+00:00", "Z"),
                        "order_by": "merged_at",
                        "sort": "desc",
                    },
                )
                total += len(merged)
            except Exception:  # noqa: BLE001
                failures.append(
                    str(
                        project.get("path_with_namespace")
                        or project.get("name_with_namespace")
                        or project_id
                    )
                )

        if failures:
            sample = ", ".join(failures[:5])
            suffix = "" if len(failures) <= 5 else f" (+{len(failures) - 5} more)"
            health.add(
                "GitLab merged merge requests",
                f"Unable to refresh merge requests for {len(failures)} projects: "
                f"{sample}{suffix}",
            )
            return None
        return total

    def stats_snapshot(
        self,
        repositories: list[JsonObject],
        contributions: ContributionSnapshot | None,
        health: HealthReporter,
    ) -> ForgeStatsSnapshot | None:
        del contributions
        try:
            contributed = self._contributed_projects()
        except Exception as exc:  # noqa: BLE001
            health.add("GitLab contributed projects", exc)
            return None
        contributed_projects = len(
            {str(project.get("id")) for project in contributed if project.get("id") is not None}
        )
        merged_requests = self._merged_request_count(
            repositories, contributed, health
        )
        if merged_requests is None:
            return None
        owned = [repository for repository in repositories if not self._is_fork(repository)]
        stars_earned = sum(int(repository.get("star_count") or 0) for repository in owned)
        forks_earned = sum(int(repository.get("forks_count") or 0) for repository in owned)
        return ForgeStatsSnapshot(
            metrics=(
                ForgeMetric("Merged MRs", merged_requests, "· 365d"),
                ForgeMetric("Projects contributed", contributed_projects, "· 1y"),
                ForgeMetric("Stars earned", stars_earned),
                ForgeMetric("Forks earned", forks_earned),
            ),
            aria_label=(
                "GitLab professional statistics: merged merge requests, projects "
                "contributed to, stars earned, and forks earned"
            ),
        )

    @staticmethod
    def _sort_featured_candidates(repositories: list[JsonObject]) -> list[JsonObject]:
        """Order projects by impact, recency, then stable human-readable name."""
        ordered = list(repositories)
        ordered.sort(key=lambda project: str(project.get("name") or "").casefold())
        ordered.sort(
            key=lambda project: str(project.get("last_activity_at") or ""),
            reverse=True,
        )
        ordered.sort(
            key=lambda project: int(project.get("star_count") or 0),
            reverse=True,
        )
        return ordered

    def _featured_candidates(self, repositories: list[JsonObject]) -> list[JsonObject]:
        eligible = [
            repository
            for repository in repositories
            if not self._is_fork(repository)
            and repository.get("name")
            and repository.get("web_url")
        ]
        tagged: list[JsonObject] = []
        for repository in eligible:
            raw_topics = repository.get("topics")
            topics = raw_topics if is_json_array(raw_topics) else []
            normalized = {str(topic).strip().casefold() for topic in topics}
            if FEATURED_TOPIC in normalized:
                tagged.append(repository)
        candidates = tagged or eligible
        return self._sort_featured_candidates(candidates)[:6]

    def _primary_language(
        self,
        repository: JsonObject,
        health: HealthReporter,
    ) -> str:
        languages = self._project_languages(repository)
        if languages is None:
            health.add(
                f"GitLab featured project language: {repository.get('name') or 'unknown'}",
                "Unable to refresh project language data",
            )
            return ""
        if not languages:
            return ""
        return max(languages.items(), key=lambda item: item[1])[0]

    def featured_projects(
        self,
        health: HealthReporter,
    ) -> list[FeaturedProject] | None:
        repositories = self.repositories(health)
        if repositories is None:
            return None
        projects: list[FeaturedProject] = []
        for repository in self._featured_candidates(repositories):
            name = str(repository.get("name") or "").strip()
            url = self._normalize_url(str(repository.get("web_url") or ""))
            if not name or not url:
                continue
            namespace = repository.get("namespace")
            owner = (
                str(namespace.get("path") or "").strip()
                if is_json_object(namespace)
                else self.username
            )
            projects.append(
                FeaturedProject(
                    identity=str(repository.get("path_with_namespace") or name),
                    name=name,
                    url=url,
                    description=str(repository.get("description") or "").strip(),
                    owner=owner,
                    primary_language=self._primary_language(repository, health),
                    stars=int(repository.get("star_count") or 0),
                    contributed=False,
                )
            )
        return projects

    def community(self, health: HealthReporter) -> CommunitySnapshot | None:
        # GitLab follower endpoints and rich single-user details require signed-in
        # user access, while CI_JOB_TOKEN does not support the Users API. Keep the
        # publication credential-free instead of introducing a persistent token
        # merely to render COMMUNITY. Unsupported data is omitted, not degraded.
        del health
        return None
