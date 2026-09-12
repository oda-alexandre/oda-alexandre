# SPDX-FileCopyrightText: 2024-2026 ODA Alexandre
# SPDX-License-Identifier: EUPL-1.2+

"""GitHub data provider for the profile README generator."""

from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlparse

from .base import (
    CommunityBadge,
    CommunitySnapshot,
    ContributionDay,
    ContributionSnapshot,
    FeaturedProject,
    ForgeMetric,
    ForgeStatsSnapshot,
    HealthReporter,
    JsonArray,
    JsonContainer,
    JsonObject,
    ProfileSnapshot,
    SocialAccount,
    is_json_array,
    is_json_object,
)

JSON_MEDIA_TYPE = "application/json"

CONTRIBUTIONS_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalPullRequestReviewContributions
      commitContributionsByRepository(maxRepositories: 100) {
        repository { nameWithOwner }
      }
      issueContributionsByRepository(maxRepositories: 100) {
        repository { nameWithOwner }
      }
      pullRequestContributionsByRepository(maxRepositories: 100) {
        repository { nameWithOwner }
      }
      pullRequestReviewContributionsByRepository(maxRepositories: 100) {
        repository { nameWithOwner }
      }
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
            contributionLevel
            weekday
          }
        }
      }
    }
  }
}
"""

CONTRIBUTION_REPOSITORY_FIELDS = (
    "commitContributionsByRepository",
    "issueContributionsByRepository",
    "pullRequestContributionsByRepository",
    "pullRequestReviewContributionsByRepository",
)


class GitHubProvider:
    """Fetch and normalize the GitHub-specific data used by the renderer."""

    key = "github"
    display_name = "GitHub"
    supports_activity = True
    supports_community = True
    language_asset_stem = "languages"
    featured_summary_label = "pinned projects"

    def __init__(self, username: str, token: str) -> None:
        self.username = username
        self._token = token

    @classmethod
    def from_environment(cls) -> "GitHubProvider":
        username = os.environ.get("GITHUB_REPOSITORY_OWNER", "").strip()
        token = os.environ.get("GH_TOKEN", "").strip()
        if not username:
            raise SystemExit("GITHUB_REPOSITORY_OWNER is required")
        if not token:
            raise SystemExit("GH_TOKEN is required")
        return cls(username=username, token=token)

    def _json(
        self,
        url: str,
        *,
        payload: JsonObject | None = None,
    ) -> JsonContainer:
        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": f"{self.username}-profile-readme",
        }
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = JSON_MEDIA_TYPE

        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"GitHub API HTTP {exc.code} for {url}: {body}"
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

    def profile(self) -> ProfileSnapshot:
        user = self._json(f"https://api.github.com/users/{self.username}")
        if not is_json_object(user):
            raise RuntimeError("Unexpected user API response")
        display_name = user.get("name") or user.get("login") or self.username
        bio = str(user.get("bio") or "").strip()
        avatar_url = str(
            user.get("avatar_url") or f"https://github.com/{self.username}.png"
        )
        website_url = self._normalize_url(str(user.get("blog") or ""))
        return ProfileSnapshot(
            display_name=str(display_name),
            bio=bio,
            avatar_url=avatar_url,
            website_url=website_url,
            profile_url=f"https://github.com/{self.username}",
        )

    def social_accounts(self, health: HealthReporter) -> list[SocialAccount]:
        try:
            accounts = self._json(
                f"https://api.github.com/users/{self.username}/social_accounts?per_page=100"
            )
            if not is_json_array(accounts):
                raise RuntimeError("Unexpected social accounts API response")
            return [
                SocialAccount(
                    provider=str(account.get("provider") or "").strip().casefold(),
                    url=self._normalize_url(str(account.get("url") or "")),
                )
                for account in accounts
                if is_json_object(account) and account.get("url")
            ]
        except Exception as exc:  # noqa: BLE001
            health.add("GitHub social accounts", exc)
            return []

    def _list_owned_public_repositories(self) -> list[JsonObject]:
        repositories: list[JsonObject] = []
        page = 1
        while True:
            batch = self._json(
                f"https://api.github.com/users/{self.username}/repos"
                f"?type=owner&sort=updated&per_page=100&page={page}"
            )
            if not is_json_array(batch):
                raise RuntimeError("Unexpected repository API response")
            repositories.extend(batch)
            if len(batch) < 100:
                return repositories
            page += 1

    def repositories(self, health: HealthReporter) -> list[JsonObject] | None:
        try:
            return self._list_owned_public_repositories()
        except Exception as exc:  # noqa: BLE001
            health.add("GitHub repositories", exc)
            return None

    def _followers(self, health: HealthReporter) -> list[JsonObject] | None:
        followers: list[JsonObject] = []
        page = 1
        try:
            while True:
                batch = self._json(
                    f"https://api.github.com/users/{self.username}/followers"
                    f"?per_page=100&page={page}"
                )
                if not is_json_array(batch):
                    raise RuntimeError("Unexpected followers API response")
                followers.extend(batch)
                if len(batch) < 100:
                    break
                page += 1
        except Exception as exc:  # noqa: BLE001
            health.add("GitHub followers", exc)
            return None

        return sorted(
            (follower for follower in followers if follower.get("login")),
            key=lambda follower: str(follower.get("login") or "").casefold(),
        )

    def community(self, health: HealthReporter) -> CommunitySnapshot | None:
        followers = self._followers(health)
        if followers is None:
            return None

        summary = CommunityBadge(
            url=f"https://github.com/{self.username}?tab=followers",
            image_url=(
                f"https://img.shields.io/github/followers/{self.username}"
                f"?style=for-the-badge&logo=github&logoColor=white"
                f"&label=FOLLOWERS&labelColor=116466&color=116466"
            ),
            alt=f"{self.username} followers",
        )
        badges: list[CommunityBadge] = []
        for follower in followers:
            login = str(follower.get("login") or "").strip()
            if not login:
                continue
            badges.append(
                CommunityBadge(
                    url=f"https://github.com/{login}",
                    image_url=(
                        f"https://img.shields.io/github/followers/{login}"
                        f"?style=for-the-badge&logo=github&logoColor=white"
                        f"&label={login}&labelColor=116466&color=116466"
                    ),
                    alt=f"{login} followers",
                )
            )
        return CommunitySnapshot(
            summary=summary, follower_count=len(followers), followers=tuple(badges)
        )

    @staticmethod
    def _repository_name(repository: JsonObject) -> str:
        return str(repository.get("full_name") or repository.get("name") or "unknown")

    def _repository_languages(self, repository: JsonObject) -> JsonObject | None:
        if repository.get("fork"):
            return {}
        languages_url = repository.get("languages_url")
        if not languages_url:
            return {}
        try:
            languages = self._json(str(languages_url))
            if not is_json_object(languages):
                raise RuntimeError("Unexpected languages API response")
            return languages
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _add_language_totals(totals: dict[str, int], languages: JsonObject) -> None:
        for language, byte_count in languages.items():
            try:
                totals[language] = totals.get(language, 0) + int(byte_count)
            except (TypeError, ValueError):
                continue

    @staticmethod
    def _report_language_failures(
        failures: list[str],
        health: HealthReporter,
    ) -> None:
        if not failures:
            return
        sample = ", ".join(failures[:5])
        suffix = "" if len(failures) <= 5 else f" (+{len(failures) - 5} more)"
        health.add(
            "GitHub languages",
            f"Unable to refresh language data for {len(failures)} repositories: "
            f"{sample}{suffix}",
        )

    def languages(
        self,
        repositories: list[JsonObject],
        health: HealthReporter,
    ) -> tuple[dict[str, int], bool]:
        totals: dict[str, int] = {}
        failures: list[str] = []
        for repository in repositories:
            languages = self._repository_languages(repository)
            if languages is None:
                failures.append(self._repository_name(repository))
            else:
                self._add_language_totals(totals, languages)
        self._report_language_failures(failures, health)
        return totals, not failures

    @staticmethod
    def _rolling_365_window() -> tuple[dt.datetime, dt.datetime]:
        now = dt.datetime.now(dt.timezone.utc)
        start = (now - dt.timedelta(days=364)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return start, now

    @staticmethod
    def _contribution_collection(result: object) -> tuple[JsonObject, JsonObject]:
        if not is_json_object(result) or result.get("errors"):
            errors = result.get("errors") if is_json_object(result) else result
            raise RuntimeError(f"GraphQL error: {errors}")
        data = result.get("data")
        user_data = data.get("user") if is_json_object(data) else None
        if not is_json_object(user_data):
            raise RuntimeError("GitHub contribution user was not returned")
        collection = user_data.get("contributionsCollection")
        if not is_json_object(collection):
            raise RuntimeError("GitHub contributionsCollection was not returned")
        calendar = collection.get("contributionCalendar")
        if not is_json_object(calendar):
            raise RuntimeError("GitHub contribution calendar was not returned")
        return collection, calendar

    @staticmethod
    def _repository_names_from_groups(groups: object) -> set[str]:
        names: set[str] = set()
        if not is_json_array(groups):
            return names
        for group in groups:
            repository = group.get("repository") if is_json_object(group) else None
            if not is_json_object(repository):
                continue
            name = str(repository.get("nameWithOwner") or "").strip()
            if name:
                names.add(name.casefold())
        return names

    @classmethod
    def _contributed_repository_names(cls, collection: JsonObject) -> set[str]:
        names: set[str] = set()
        for field in CONTRIBUTION_REPOSITORY_FIELDS:
            names.update(cls._repository_names_from_groups(collection.get(field) or []))
        return names

    @staticmethod
    def _contribution_day(
        day: object,
        start_date: dt.date,
        end_date: dt.date,
    ) -> ContributionDay | None:
        if not is_json_object(day):
            return None
        day_date = dt.date.fromisoformat(str(day["date"]))
        if not start_date <= day_date <= end_date:
            return None
        return ContributionDay(
            date=day_date,
            count=int(day.get("contributionCount") or 0),
            level=str(day.get("contributionLevel") or "NONE"),
            weekday=int(day.get("weekday") or 0),
        )

    @classmethod
    def _contribution_week(
        cls,
        week: object,
        start_date: dt.date,
        end_date: dt.date,
    ) -> tuple[ContributionDay, ...]:
        if not is_json_object(week):
            return ()
        days = (
            cls._contribution_day(day, start_date, end_date)
            for day in week.get("contributionDays", [])
        )
        return tuple(day for day in days if day is not None)

    @classmethod
    def _contribution_weeks(
        cls,
        calendar: JsonObject,
        start_date: dt.date,
        end_date: dt.date,
    ) -> tuple[tuple[ContributionDay, ...], ...]:
        weeks = (
            cls._contribution_week(week, start_date, end_date)
            for week in calendar.get("weeks", [])
        )
        return tuple(week for week in weeks if week)

    def contribution_snapshot(
        self,
        health: HealthReporter,
    ) -> ContributionSnapshot | None:
        start, now = self._rolling_365_window()
        try:
            result = self._json(
                "https://api.github.com/graphql",
                payload={
                    "query": CONTRIBUTIONS_QUERY,
                    "variables": {
                        "login": self.username,
                        "from": start.isoformat().replace("+00:00", "Z"),
                        "to": now.isoformat().replace("+00:00", "Z"),
                    },
                },
            )
            collection, calendar = self._contribution_collection(result)
            repository_names = self._contributed_repository_names(collection)
            weeks = self._contribution_weeks(calendar, start.date(), now.date())
            return ContributionSnapshot(
                total=int(calendar.get("totalContributions") or 0),
                reviews=int(collection.get("totalPullRequestReviewContributions") or 0),
                repositories_contributed=len(repository_names),
                weeks=weeks,
            )
        except Exception as exc:  # noqa: BLE001
            health.add("GitHub contributions", exc)
            return None

    def _merged_request_count(self, health: HealthReporter) -> int | None:
        start, now = self._rolling_365_window()
        search_query = (
            f"author:{self.username} is:pr is:merged "
            f"merged:{start.date().isoformat()}..{now.date().isoformat()}"
        )
        url = "https://api.github.com/search/issues?" + urlencode(
            {"q": search_query, "per_page": 1}
        )
        try:
            result = self._json(url)
            if not is_json_object(result):
                raise RuntimeError("Unexpected pull-request search response")
            return int(result.get("total_count") or 0)
        except Exception as exc:  # noqa: BLE001
            health.add("GitHub merged pull requests", exc)
            return None

    def stats_snapshot(
        self,
        repositories: list[JsonObject],
        contributions: ContributionSnapshot | None,
        health: HealthReporter,
    ) -> ForgeStatsSnapshot | None:
        if contributions is None:
            return None
        merged_requests = self._merged_request_count(health)
        if merged_requests is None:
            return None
        owned = [repository for repository in repositories if not repository.get("fork")]
        stars_earned = sum(
            int(repository.get("stargazers_count") or 0) for repository in owned
        )
        return ForgeStatsSnapshot(
            metrics=(
                ForgeMetric("Merged PRs", merged_requests, "· 365d"),
                ForgeMetric("Code reviews", contributions.reviews, "· 365d"),
                ForgeMetric(
                    "Repos contributed",
                    contributions.repositories_contributed,
                    "· 365d",
                ),
                ForgeMetric("Stars earned", stars_earned),
            ),
            aria_label=(
                "GitHub professional statistics: merged pull requests, code reviews, "
                "repositories contributed to, and stars earned"
            ),
        )

    def featured_projects(
        self,
        health: HealthReporter,
    ) -> list[FeaturedProject] | None:
        query = """
        query($login: String!) {
          user(login: $login) {
            pinnedItems(first: 6, types: [REPOSITORY]) {
              nodes {
                ... on Repository {
                  name
                  nameWithOwner
                  url
                  description
                  stargazerCount
                  primaryLanguage { name }
                  owner { login }
                }
              }
            }
          }
        }
        """
        try:
            result = self._json(
                "https://api.github.com/graphql",
                payload={"query": query, "variables": {"login": self.username}},
            )
            if not is_json_object(result) or result.get("errors"):
                raise RuntimeError(
                    f"GraphQL error: "
                    f"{result.get('errors') if is_json_object(result) else result}"
                )
            data = result.get("data")
            user_data = data.get("user") if is_json_object(data) else None
            if not is_json_object(user_data):
                raise RuntimeError("GitHub pinned-items user was not returned")
            pinned = user_data.get("pinnedItems")
            if not is_json_object(pinned):
                raise RuntimeError("GitHub pinnedItems was not returned")
            raw_nodes = pinned.get("nodes")
            if not raw_nodes:
                nodes: JsonArray = []
            elif is_json_array(raw_nodes):
                nodes = raw_nodes
            else:
                raise RuntimeError("Unexpected GitHub pinnedItems nodes response")
            projects: list[FeaturedProject] = []
            for node in nodes:
                if not is_json_object(node):
                    continue
                name = str(node.get("name") or "").strip()
                url = str(node.get("url") or "").strip()
                if not name or not url:
                    continue
                owner = node.get("owner")
                owner_login = (
                    str(owner.get("login") or "").strip()
                    if is_json_object(owner)
                    else ""
                )
                language = node.get("primaryLanguage")
                primary_language = (
                    str(language.get("name") or "").strip()
                    if is_json_object(language)
                    else ""
                )
                identity = str(node.get("nameWithOwner") or name).strip()
                projects.append(
                    FeaturedProject(
                        identity=identity,
                        name=name,
                        url=url,
                        description=str(node.get("description") or "").strip(),
                        owner=owner_login,
                        primary_language=primary_language,
                        stars=int(node.get("stargazerCount") or 0),
                        contributed=(
                            bool(owner_login)
                            and owner_login.casefold() != self.username.casefold()
                        ),
                    )
                )
            return projects
        except Exception as exc:  # noqa: BLE001
            health.add("GitHub pinned repositories", exc)
            return None
