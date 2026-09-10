<!-- SPDX-FileCopyrightText: 2024-2026 ODA Alexandre -->
<!-- SPDX-License-Identifier: EUPL-1.2+ -->

# Profile README architecture and design contract

This document is the maintenance contract for the generated GitHub Profile README.
Read it before changing `scripts/update_readme.py`, `README.template`,
`profile.config.toml`, generated SVG cards, or section ordering.

The goal is not to preserve individual pixel values forever. The goal is to
preserve the **rules** that keep the profile professional, consistent,
conditional, resilient, and easy to extend.

## 1. Component hierarchy

The README has three visual/semantic levels:

1. **Section** — a complete README block such as `FEATURED PROJECTS` or
   `GITHUB STATS`.
2. **Card/component** — one independently renderable child inside a section.
3. **Data element** — metric, chart label, legend, metadata, action, etc.

A section is not a title plus unrelated content. It owns its title, content,
spacing, and trailing separator as one logical unit.

### Conditional rendering rule

The same rule applies to static and dynamic content:

- Empty card/component -> do not render that card.
- Section with at least one valid child -> render only the valid children.
- Section with no valid children -> render nothing at all: no title, whitespace,
  placeholder, or separator.
- Separators are inserted only after sections that actually survive.
- The separator is owned by the preceding visible section.
- Because the README ends with a dedicated licence footer, the last visible
  content section also owns one trailing separator before that footer.

Do not add public `temporarily unavailable` cards/messages. Operational failures
belong in Profile Health, not in the visitor-facing README.

## 2. Typography roles

Choose typography by **semantic role**, never by copying a font size from a
nearby card.

| Role | Use | Default alignment |
| --- | --- | --- |
| `section-title` | README section headings (`FEATURED PROJECTS`, `GITHUB STATS`) | centered |
| `section-lead` | short human lead inside a section (`I thank all my followers`) | centered |
| `card-title` | project/platform/certification/activity card heading | centered |
| `card-description` | explanatory narrative copy inside a card | centered |
| `metric-value` | prominent KPI number | centered |
| `metric-label` | label paired with a numeric KPI | centered |
| `status-value` | prominent textual state such as a platform rank | centered |
| `data-label` | primary text that belongs to chart geometry | follows data geometry |
| `data-meta` | secondary chart value/axis/legend text | follows data geometry |
| `meta` | tertiary contextual information | normally centered in narrative cards |
| `action-label` | compact status/CTA such as `CONTRIBUTED` | centered unless action geometry requires otherwise |
| `connector-label` | workflow transition label | tied to connector geometry |

`section-title` and `section-lead` are rendered by GitHub Markdown/HTML.
SVG roles are defined centrally in `svg_typography_css()`.

### Alignment rule

**Narrative content is centered. Structured data follows its data geometry.**

Examples:

- Featured project title/description/meta -> centered.
- Security Practice / Research / Certification cards -> centered.
- GitHub Activity card title and contribution count -> centered.
- Stats KPI cells -> centered.
- Languages: language label left, percentage right, because both describe a bar.
- Heatmap: month/day labels and Less/More legend stay aligned to the grid.
- Workflow labels/connectors follow the diagram geometry.

Do not center chart axes merely for visual symmetry.

## 3. Card families

All generated SVG cards share the same frame, theme, glow, radius, and font
family through `svg_card_frame()`, `svg_card_document()`, and
`svg_typography_css()`.

Do **not** create a second border/glow/frame style for a special card. If a card
looks clipped or unbalanced, fix its geometry or padding instead.

### A. Professional external-profile cards

Builders: `build_compact_link_card_svg()` and `build_practice_progress_card_svg()`

Use the compact narrative card when the external identity has only a verified
public destination and short purpose. Use the progress variant for Security
Practice when a reviewed adapter exposes meaningful personal progression. Use
the status variant when only one reliable public state (for example a rank) is
available; never fabricate a percentage/bar to make cards look symmetric.

The practice-card contract is intentionally minimal:

- `card-title` -> platform
- `card-description` -> distinct professional practice domain
- `status-value` -> one primary state, such as the current rank
- optional progress bar + `meta` line -> progression only when the source exposes it reliably
- status-only cards keep the same outer geometry and use a short `meta` label instead

Do not surface points, streaks, global leaderboard position, owns, bloods or other
gamification merely because a platform API exposes them. A Security Practice
card should communicate competence/progression, not become a game dashboard.

Current adapters:

- Hack The Box -> authenticated Labs rank + next-rank progression
- CyberDefenders -> public rank from the official share-badge endpoint + `Avg. skill progress`, derived as the arithmetic mean of the seven public `progress` values from `/api/user/<username>/overview/skills/`. This is explicitly a README-derived summary, not an official overall CyberDefenders score. Rank and progress retain independent last-good values on transient source failure.

Security Research must never invent progression. HackerOne keeps two distinct
card roles while reusing the existing shared primitives:

- the HackerOne **profile card** is centered and entirely linked to the public
  researcher profile through `build_linked_picture()`. When Signal/Impact exist it
  uses the semantic KPI roles for Reputation / Signal / Impact. When a public
  disclosure makes the profile meaningful before those metrics are calculated,
  reuse `build_compact_link_card_svg()` instead of inventing a disclosure-status
  variant. Reputation alone never activates the card because a fresh account has
  a baseline reputation before meaningful research activity.
- each explicitly public Hacktivity disclosure is a separate **evidence card**
  rendered through `build_security_research_evidence_card_svg()`. Evidence cards
  use the existing 220px compact geometry, canonical frame and semantic roles:
  program/source -> `card-title`, public report title -> `card-description`, and
  severity/CWE/CVE -> `meta`. The surrounding `<a><picture>...</picture></a>`
  makes the whole card clickable to the public report, so no redundant CTA,
  status line, or arrow is rendered inside the SVG.
- evidence cards use `centered_card_rows(..., per_row=3)` and are capped at six
  recent HackerOne disclosures to avoid a wall of cards. Incomplete final rows
  remain centered. Optional manually verified external disclosures must use the
  same full evidence-card structure (source/product + title + metadata). CVE-like
  identifiers belong inside that disclosure card and are never standalone cards.

### B. Featured project card

Builder: `build_featured_project_card_svg()`

Source of truth: GitHub `pinnedItems`, in GitHub pin order.

Display only information that helps a reviewer understand the project quickly:

- repository name (`card-title`)
- repository description (`card-description`)
- primary language + stars (`meta`)
- `CONTRIBUTED` only when the pinned repository is owned by another account

Do not add topics, forks, timestamps, or custom project lists unless the product
requirements explicitly change. Pin/unpin/reorder on GitHub should be sufficient
to update this section.

### C. Certification card

Builder: `build_certification_card_svg()`

Only obtained, publicly verifiable credentials belong here.

Structure:

- name -> `card-title`
- issuer -> `card-description`
- one temporal line -> `meta`

The surrounding `<a><picture>...</picture></a>` is the verification action. Do
not render `VERIFY`, an arrow, or another CTA inside the certification SVG.

Time display rule:

- if `expires_at` exists: show `Valid until YYYY`
- otherwise if `issued_at` exists: show `Issued YYYY`
- do not show both dates simultaneously in the card

The source model may retain both dates for sorting/future use.

### D. Professional GitHub stats card

Builder: `build_stats_svg()`

This card measures collaboration/impact, not account gamification.
Current intended metrics:

- Merged PRs · 365d
- Code reviews · 365d
- Repos contributed · 365d
- Stars earned

Do not reintroduce followers, public repo count, current streak, longest streak,
or a duplicate contribution count here:

- followers belong to `COMMUNITY`
- contribution rhythm belongs to the Activity heatmap
- repo count measures quantity more than impact
- streaks are intentionally excluded from the professional signal

A numeric zero is valid data and should remain visible. Missing/unavailable data
is different and follows the fallback rules below.

### E. Data-visualization card

Builders include `build_languages_svg()` and `build_activity_svg()`.

Use `data-label` / `data-meta` for chart-bound text. The card may still have a
centered `card-title`, but axes, percentages, legends, and other data-bound text
must stay attached to the visualization geometry.

### F. Workflow diagram

Builder: `build_development_workflow_svg()`

Workflow nodes reuse the canonical card frame and narrative typography, but
connector labels/arrows are geometry-specific. Connector labels are transparent,
outlined annotations rather than filled cards so transitions stay visually
secondary to workflow nodes. Local Development, GitLab, GitLab Pages, GitHub,
and Docker Hub must not invent different frame variants.
GitLab Pages is the optional static-site publication branch from GitLab, while
Docker Hub remains the optional image publication branch from GitHub.

## 4. Sections and source ownership

Current logical order:

1. CONTACT
2. DEVELOPMENT WORKFLOW
3. FEATURED PROJECTS
4. SECURITY PRACTICE (optional)
5. SECURITY RESEARCH (optional)
6. CERTIFICATIONS (optional)
7. MOST USED LANGUAGES
8. GITHUB STATS (Stats + Activity are independent child cards)
9. LATEST BLOG POSTS (optional when empty)
10. COMMUNITY (optional when empty)
11. Licence footer (always visible, not a section)

The licence footer is deliberately outside the section/card hierarchy. It is a
small centered technical epilogue rendered after the last visible content
section. It links directly to `./LICENSE` and `./NOTICE` and must not be merged
into CONTACT, COMMUNITY, or any other professional-content block.

Important source-of-truth rules:

- `CONTACT`: destinations are dynamic/config-derived links, but the visual badges
  are repository-local static SVG assets under `assets/badges/contact/`. They are
  intentionally not regenerated by the workflow and must keep the established
  Shields-style appearance. Only the surrounding `<a href>` destinations change.
- `FEATURED PROJECTS`: GitHub pinned repositories, not a manual README list.
- `SECURITY PRACTICE`: `profile.config.toml`; only distinct platforms with a
  professional public profile. Platform credentials/tokens belong only in GitHub
  Actions secrets, never in TOML or generated content. HTB uses `HTB_TOKEN`.
- `SECURITY RESEARCH`: real-world, externally verifiable research only. HackerOne
  uses the official Hacker API with the public profile handle as API username and
  `HACKERONE_API_TOKEN` from Actions secrets. `/hackers/me/reports` supplies
  reporter Reputation/Signal/Impact; `/hackers/hacktivity` is filtered by that
  handle plus `disclosed:true`, sorted newest-first, and supplies up to six public
  evidence cards. The HackerOne profile card links to the researcher profile; each
  disclosure card links directly to its public HackerOne report. Successful empty
  responses are normal and keep the section hidden; financial data and private
  report content are never rendered.
- `CERTIFICATIONS`: obtained + verifiable credentials only; no `planned` or
  `in progress` entries.
- `LATEST BLOG POSTS`: `[blog].feed_url` in `profile.config.toml` is explicit and
  opt-in. An empty value means the blog is intentionally disabled: do not perform
  a network request, do not preserve stale blog content, and do not create a
  Profile Health incident. Once a valid HTTPS feed URL is configured, temporary
  source failures preserve the last canonical section content and are reported.
- `COMMUNITY`: GitHub followers; avoid duplicating follower count in Stats. Its
  follower badges remain dynamic Shields.io badges because follower counts and the
  follower set change over time; do not vendor these as static source assets.

## 5. Resilience and fallbacks

Dynamic content follows this priority:

1. fresh valid data -> render/update it
2. source/build failure + last valid generated content exists -> preserve it
3. no valid current or cached content -> omit the card/section
4. record the failure in Profile Health

Never let a transient API failure replace a good public card with an error
placeholder.

Dark/light SVG pairs are atomic: build both before replacing the previous pair.
A card should never publish a new dark asset with an old/missing light asset.

External platform APIs must be isolated behind adapter functions, use secrets
only from the workflow environment, and follow the same last-good + Profile
Health fallback. For HackerOne, distinguish a healthy empty/new account from an
operational failure: empty reports/Hacktivity remove stale HackerOne assets and
hide the section without creating a Profile Health incident. Authentication,
network or schema failures preserve the previous complete SECURITY RESEARCH
section and every generated asset it references, then report the incident through
Profile Health. Never let source instability leak into README.md.

## 6. Profile Health

Visitor-facing resilience and maintainer-facing observability are separate.

- README stays clean through fallback/omission.
- `.profile-health.json` records degraded sources/build failures.
- `scripts/report_profile_health.py` creates or updates one open Profile Health
  issue instead of duplicating incidents.
- When a later workflow is healthy, the existing incident receives a recovery
  comment and is closed automatically.
- Repository Issues must therefore be enabled and workflow permissions include
  `issues: write`.

Do not weaken or bypass issue de-duplication when changing health reporting.

## 7. Adding a new section or card

Before implementing a new feature:

1. Decide whether it adds a **distinct professional signal** or duplicates an
   existing section.
2. Define its data source and what counts as valid data.
3. Choose an existing card family above before inventing a new layout.
4. Use semantic typography roles, not one-off font declarations.
5. Make each child independently optional.
6. Make the whole section disappear when every child is empty.
7. Define last-good fallback behaviour for dynamic data.
8. Send operational errors to Profile Health, not to the public README.
9. Generate dark + light assets through the shared themed helpers when the
   component is dynamic. Stable, intentionally vendored visual assets such as
   CONTACT badges belong outside `assets/generated/`.
10. Add new generated assets to the normal active-asset lifecycle; never commit
    generated SVGs to the source ZIP/dev branch.

## 8. Git branch and publication lineage

`dev` is the editable source branch and `main` is the default, published snapshot.
The publication workflow must preserve both properties at the same time:

- source-only files such as `README.template` and `profile.config.toml` stay on `dev`;
- `main` contains only the public snapshot and generated assets;
- every new `dev` commit becomes reachable from `main` so GitHub can attribute
  eligible source commits through the default branch;
- existing `dev` commits are never rebased, squashed, cherry-picked, or recreated
  during publication, which preserves their commit IDs, authorship, and signatures.

When `origin/dev` is not already an ancestor of the current `main`, the workflow
starts an `ours` merge with `--no-commit`. The `ours` strategy records `dev` as the
second parent while retaining the current `main` tree. The normal selective `rsync`,
README generation, and asset generation then produce the exact public snapshot, and
one publication commit records both parents. On scheduled refreshes where `dev` is
already an ancestor, publication remains a normal single-parent snapshot commit.

Do not replace this lineage with squash/rebase publication or a force push. Those
approaches either rewrite signed source commits or disconnect them from the default
branch, defeating contribution attribution and signature preservation.

Repository branch rules should match this model: `dev` may require signed commits
and should block deletion/force-push; `main` should block deletion/force-push but
must allow the publication bot's normal push. Do not require signed commits on
`main` unless the publication identity itself is configured to create verifiable
signed commits.

## 9. Things intentionally avoided

Unless requirements explicitly change, do not add:

- visitor counters
- random quotes
- Spotify/music widgets
- typing animations
- generic GitHub trophies
- large technology-icon walls
- redundant CTF/lab platforms that prove the same thing
- empty `planned` professional sections
- public API-error messages
- special card frame variants

The profile should favor a small number of verifiable signals over visual noise.

## 10. Licensing boundary

The repository uses a deliberate split licence model. Read `NOTICE` before
changing licensing, moving content between files, or adding new generated data.

- Reusable code, automation, architecture documentation and the implemented
  design system are licensed under **EUPL-1.2-or-later**.
- Reusable source files use the SPDX expression `EUPL-1.2+` (`+` means "this
  version or any later version" in SPDX license-expression syntax).
- Account-specific **Personal Profile Content is not licensed under the EUPL**
  by this repository and remains reserved to the profile owner to the extent
  protected by applicable law.
- Third-party logos, marks, badges and linked material retain their own rights.
- The rendered README ends with a compact licence footer: `LICENSE` and `NOTICE`
  must remain clickable repository-relative links to the published files.

### Rule for future components

A component implementation (layout code, card builder, typography role,
fallback mechanism, workflow logic) can be EUPL-licensed while the personal
data rendered through that component is not. Do not conflate **renderer** and
**rendered personal content** when adding new platforms, credentials, research
findings, projects, posts or account metadata.

Do not copy personal values into EUPL source-code comments or examples when a
neutral placeholder communicates the same architecture rule.

