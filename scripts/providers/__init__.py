# SPDX-FileCopyrightText: 2024-2026 ODA Alexandre
# SPDX-License-Identifier: EUPL-1.2+

"""Forge-provider selection for the profile README generator."""

from __future__ import annotations

import os

from .base import ForgeProvider
from .github import GitHubProvider


def load_provider() -> ForgeProvider:
    """Build the configured forge provider, defaulting to the current GitHub path."""
    forge = (os.environ.get("PROFILE_FORGE", "github").strip() or "github").casefold()
    if forge == "github":
        return GitHubProvider.from_environment()
    raise SystemExit(f"Unsupported PROFILE_FORGE: {forge}")
