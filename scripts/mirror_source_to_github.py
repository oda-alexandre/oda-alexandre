#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024-2026 ODA Alexandre
# SPDX-License-Identifier: EUPL-1.2+

"""Mirror canonical GitLab source refs to GitHub with a GitHub App token.

Only the canonical ``dev`` branch and the currently-triggering signed ``v*`` tag
are eligible. GitHub ``main`` is intentionally outside this script: each forge
publishes its own generated ``main`` independently.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NoReturn, cast

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"
SOURCE_BRANCH = "dev"


class MirrorError(RuntimeError):
    """A safe-to-display source-mirroring failure."""


def fail(message: str) -> NoReturn:
    """Terminate without printing credentials or API response secrets."""
    raise MirrorError(message)


def require_env(name: str) -> str:
    """Return one required environment variable after trimming whitespace."""
    value = os.environ.get(name, "").strip()
    if not value:
        fail(f"{name} is required")
    return value


def git(
    *args: str,
    env: dict[str, str] | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run Git with predictable text I/O and optional credential-safe env."""
    return subprocess.run(
        ["git", *args],
        check=check,
        env=env,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def git_output(*args: str, env: dict[str, str] | None = None) -> str:
    """Return trimmed Git stdout."""
    result = git(*args, env=env, capture=True)
    return result.stdout.strip()


def is_ancestor(ancestor: str, descendant: str) -> bool:
    """Return whether ``ancestor`` is reachable from ``descendant``."""
    result = git(
        "merge-base",
        "--is-ancestor",
        ancestor,
        descendant,
        capture=True,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    fail(
        "git merge-base failed while checking source history: "
        f"{result.stderr.strip() or 'unknown error'}"
    )


def base64url(raw: bytes) -> str:
    """Encode bytes using unpadded JWT Base64URL."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_private_key(encoded: str) -> bytes:
    """Decode and minimally validate the protected Base64 PEM variable."""
    try:
        private_key = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise MirrorError("GITHUB_MIRROR_APP_PRIVATE_KEY_B64 is not valid Base64") from exc
    if b"-----BEGIN" not in private_key or b"PRIVATE KEY-----" not in private_key:
        fail("Decoded GitHub App private key is not a PEM private key")
    return private_key


def create_app_jwt(app_id: str, private_key: bytes) -> str:
    """Create the short-lived RS256 JWT required by GitHub App endpoints."""
    now = int(time.time())
    header = base64url(b'{"alg":"RS256","typ":"JWT"}')
    payload = base64url(
        json.dumps(
            {"iat": now - 60, "exp": now + 540, "iss": app_id},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signing_input = f"{header}.{payload}".encode("ascii")

    with tempfile.TemporaryDirectory(prefix="github-app-key-") as directory:
        key_path = Path(directory) / "app-private-key.pem"
        key_path.write_bytes(private_key)
        key_path.chmod(0o600)
        try:
            signature = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", str(key_path), "-binary"],
                input=signing_input,
                check=True,
                capture_output=True,
            ).stdout
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.decode("utf-8", errors="replace").strip()
            raise MirrorError(f"OpenSSL could not sign the GitHub App JWT: {detail}") from exc

    return f"{signing_input.decode('ascii')}.{base64url(signature)}"


def github_json(
    method: str,
    path: str,
    bearer: str,
    *,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    """Call one GitHub JSON endpoint without exposing bearer credentials."""
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {bearer}",
        "User-Agent": "oda-alexandre-gitlab-mirror",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        f"{GITHUB_API_BASE}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            parsed: object = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise MirrorError(
            f"GitHub API HTTP {exc.code} for {method} {path}: {body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise MirrorError(f"GitHub API request failed for {method} {path}: {exc.reason}") from exc

    if not isinstance(parsed, dict):
        fail(f"GitHub API returned an unexpected payload for {method} {path}")
    parsed_dict = cast(dict[object, object], parsed)
    if not all(isinstance(key, str) for key in parsed_dict):
        fail(f"GitHub API returned an unexpected payload for {method} {path}")
    return cast(dict[str, object], parsed_dict)


def create_installation_token(app_id: str, private_key_b64: str, owner: str, repo: str) -> str:
    """Resolve this repository's installation and mint a repository-scoped token."""
    jwt = create_app_jwt(app_id, decode_private_key(private_key_b64))
    installation = github_json(
        "GET",
        f"/repos/{owner}/{repo}/installation",
        jwt,
    )
    installation_id = installation.get("id")
    if not isinstance(installation_id, int) or installation_id <= 0:
        fail("GitHub App installation response does not contain a valid installation id")

    token_payload = github_json(
        "POST",
        f"/app/installations/{installation_id}/access_tokens",
        jwt,
        payload={
            "repositories": [repo],
            "permissions": {"contents": "write", "workflows": "write"},
        },
    )
    token = token_payload.get("token")
    if not isinstance(token, str) or not token:
        fail("GitHub App installation token response does not contain a token")
    return token


def github_git_env(token: str) -> dict[str, str]:
    """Authenticate Git over HTTPS without placing the token in a URL or argv."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return env


def fetch_github_dev(github_url: str, auth_env: dict[str, str]) -> str:
    """Fetch GitHub dev into a private local ref and return its commit SHA."""
    mirror_ref = "refs/github-mirror/dev"
    git(
        "fetch",
        "--no-tags",
        github_url,
        f"+refs/heads/{SOURCE_BRANCH}:{mirror_ref}",
        env=auth_env,
    )
    return git_output("rev-parse", f"{mirror_ref}^{{commit}}")


def mirror_dev(
    github_url: str,
    auth_env: dict[str, str],
    source_sha: str,
) -> None:
    """Fast-forward GitHub dev to this pipeline's already-verified source commit."""
    github_sha = fetch_github_dev(github_url, auth_env)
    if github_sha == source_sha:
        print("[ok] GitHub dev already matches the verified GitLab source commit.")
        return

    if is_ancestor(github_sha, source_sha):
        print(f"[*] Mirroring verified GitLab dev {source_sha[:12]} to GitHub dev...")
        git(
            "push",
            "--porcelain",
            github_url,
            f"{source_sha}:refs/heads/{SOURCE_BRANCH}",
            env=auth_env,
        )
        print("[ok] GitHub dev fast-forwarded from canonical GitLab dev.")
        return

    if is_ancestor(source_sha, github_sha):
        # Concurrent GitLab pipelines can finish out of order. A stale pipeline
        # may observe GitHub already advanced by a newer verified pipeline. Only
        # accept that state if the GitHub tip is still inside current GitLab dev.
        git(
            "fetch",
            "--no-tags",
            "origin",
            f"+refs/heads/{SOURCE_BRANCH}:refs/remotes/origin/{SOURCE_BRANCH}",
        )
        current_gitlab_dev = git_output(
            "rev-parse", f"refs/remotes/origin/{SOURCE_BRANCH}^{{commit}}"
        )
        if is_ancestor(github_sha, current_gitlab_dev):
            print(
                "[ok] A newer canonical GitLab dev commit is already mirrored to GitHub; "
                "this pipeline is stale."
            )
            return
        fail("GitHub dev is ahead of this pipeline but outside current canonical GitLab dev")

    fail("GitHub dev and canonical GitLab dev have diverged; refusing to force-push")


def mirror_tag(
    github_url: str,
    auth_env: dict[str, str],
    tag_name: str,
    source_sha: str,
) -> None:
    """Create one immutable GitHub release tag with the exact GitLab tag object."""
    tag_ref = f"refs/tags/{tag_name}"
    local_tag_object = git_output("rev-parse", tag_ref)
    tag_target = git_output("rev-parse", f"{tag_ref}^{{commit}}")
    if tag_target != source_sha:
        fail(
            f"Tag {tag_name} targets {tag_target}, but GitLab pipeline commit is {source_sha}"
        )

    github_dev = fetch_github_dev(github_url, auth_env)
    if not is_ancestor(tag_target, github_dev):
        fail(
            f"GitHub dev does not yet contain release target {tag_target}; "
            "finish or retry the dev mirror before mirroring this tag"
        )

    remote_line = git_output(
        "ls-remote",
        github_url,
        tag_ref,
        env=auth_env,
    )
    if remote_line:
        remote_tag_object = remote_line.split(maxsplit=1)[0]
        if remote_tag_object != local_tag_object:
            fail(
                f"GitHub {tag_ref} already exists with a different object; "
                "release tags are immutable"
            )
        print(f"[ok] GitHub {tag_name} already matches the exact GitLab tag object.")
        return

    print(f"[*] Mirroring signed release tag {tag_name} to GitHub...")
    git(
        "push",
        "--porcelain",
        github_url,
        f"{tag_ref}:{tag_ref}",
        env=auth_env,
    )
    print(f"[ok] GitHub release tag {tag_name} created without rewriting its Git object.")


def main() -> None:
    """Authenticate as the installed GitHub App and mirror the triggering source ref."""
    app_id = require_env("GITHUB_MIRROR_APP_ID")
    private_key_b64 = require_env("GITHUB_MIRROR_APP_PRIVATE_KEY_B64")
    target = require_env("GITHUB_MIRROR_REPOSITORY")
    source_sha = require_env("CI_COMMIT_SHA")
    pipeline_source = require_env("CI_PIPELINE_SOURCE")

    try:
        owner, repo = target.split("/", maxsplit=1)
    except ValueError as exc:
        raise MirrorError("GITHUB_MIRROR_REPOSITORY must use owner/repository format") from exc
    if not owner or not repo or "/" in repo:
        fail("GITHUB_MIRROR_REPOSITORY must use owner/repository format")

    token = create_installation_token(app_id, private_key_b64, owner, repo)
    auth_env = github_git_env(token)
    github_url = f"https://github.com/{owner}/{repo}.git"

    tag_name = os.environ.get("CI_COMMIT_TAG", "").strip()
    branch_name = os.environ.get("CI_COMMIT_BRANCH", "").strip()
    if tag_name:
        if pipeline_source != "push":
            fail("Release tag mirroring is allowed only for push pipelines")
        mirror_tag(github_url, auth_env, tag_name, source_sha)
        return

    if pipeline_source != "push" or branch_name != SOURCE_BRANCH:
        fail("Source branch mirroring is allowed only for push pipelines on dev")
    mirror_dev(github_url, auth_env, source_sha)


if __name__ == "__main__":
    try:
        main()
    except MirrorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except subprocess.CalledProcessError as exc:
        print(
            f"ERROR: command failed with exit status {exc.returncode}; source mirror aborted",
            file=sys.stderr,
        )
        raise SystemExit(exc.returncode) from exc
