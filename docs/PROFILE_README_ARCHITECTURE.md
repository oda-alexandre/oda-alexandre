<!-- SPDX-FileCopyrightText: 2024-2026 ODA Alexandre -->
<!-- SPDX-License-Identifier: EUPL-1.2+ -->

# Profile README architecture and design contract

This document is the maintenance contract for the generated GitHub and GitLab profile READMEs.
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

All generated SVG cards share the same frame, glow, radius, color palette, and font
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
  researcher profile through `build_linked_image()`. When Signal/Impact exist it
  uses the semantic KPI roles for Reputation / Signal / Impact. When a public
  disclosure makes the profile meaningful before those metrics are calculated,
  reuse `build_compact_link_card_svg()` instead of inventing a disclosure-status
  variant. Reputation alone never activates the card because a fresh account has
  a baseline reputation before meaningful research activity.
- each explicitly public Hacktivity disclosure is a separate **evidence card**
  rendered through `build_security_research_evidence_card_svg()`. Evidence cards
  use the existing 220px compact geometry, canonical frame and semantic roles:
  program/source -> `card-title`, public report title -> `card-description`, and
  severity/CWE/CVE -> `meta`. The surrounding `<a><img ...></a>`
  makes the whole card clickable to the public report, so no redundant CTA,
  status line, or arrow is rendered inside the SVG.
- evidence cards use `centered_card_rows(..., per_row=3)` and are capped at six
  recent HackerOne disclosures to avoid a wall of cards. Incomplete final rows
  remain centered. Optional manually verified external disclosures must use the
  same full evidence-card structure (source/product + title + metadata). CVE-like
  identifiers belong inside that disclosure card and are never standalone cards.

### B. Featured project card

Builder: `build_featured_project_card_svg()`

The active forge provider owns project selection and normalizes it into the same
card model:

- GitHub uses profile `pinnedItems` in the configured pin order.
- GitLab prefers public personal projects carrying the `profile-featured` topic.
  When none are tagged, it falls back deterministically to public personal
  non-fork projects ordered by stars, recent activity, then name.

Display only information that helps a reviewer understand the project quickly:

- project name (`card-title`)
- project description (`card-description`)
- primary language + stars (`meta`)
- `CONTRIBUTED` only when the normalized project represents work in another
  account/namespace

Do not render topics, forks, or timestamps merely because a forge API exposes
them. GitHub pinning and the GitLab `profile-featured` topic are selection
mechanisms, not card metadata, and no second hand-maintained project list belongs
in repository configuration.

### C. Certification card

Builder: `build_certification_card_svg()`

Only obtained, publicly verifiable credentials belong here.

Structure:

- name -> `card-title`
- issuer -> `card-description`
- one temporal line -> `meta`

The surrounding `<a><img ...></a>` is the verification action. Do
not render `VERIFY`, an arrow, or another CTA inside the certification SVG.

Time display rule:

- if `expires_at` exists: show `Valid until YYYY`
- otherwise if `issued_at` exists: show `Issued YYYY`
- do not show both dates simultaneously in the card

The source model may retain both dates for sorting/future use.

### D. Professional forge stats card

Builder: `build_stats_svg()`

This card measures collaboration/impact, not account gamification. The provider
selects forge-native metrics instead of forcing GitHub concepts onto GitLab.

GitHub metrics:

- Merged PRs · 365d
- Code reviews · 365d
- Repos contributed · 365d
- Stars earned

GitLab metrics:

- Merged MRs · 365d
- Projects contributed · 1y
- Stars earned
- Forks earned

Do not add followers, public repository/project count, current streak, longest
streak, or a duplicate contribution count here:

- followers belong to `COMMUNITY` when the active provider can retrieve them
  through a supported contract
- contribution rhythm belongs to an Activity visualization only when the forge
  exposes a supported contribution-calendar API
- repository/project count measures quantity more than impact
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

### Forge data boundary

`update_readme.py` owns rendering, fallbacks, shared configuration and reviewed
external-platform adapters. Forge API access is isolated under `scripts/providers/`:

- `base.py` defines forge-neutral snapshots and the provider contract;
- `github.py` owns GitHub API calls and normalizes them into that contract;
- `gitlab.py` owns GitLab REST API calls and normalizes them into the same
  renderer contract. Profile, project, language and statistics data use public
  endpoints only; the provider does not require a persistent GitLab access token;
- `PROFILE_FORGE` selects `github` or `gitlab` and defaults to `github`; unsupported
  values fail closed rather than silently falling back to a different data source.

The GitHub workflow sets `PROFILE_FORGE=github` explicitly and publishes GitHub
`main`. The GitLab pipeline sets `PROFILE_FORGE=gitlab` explicitly and publishes
GitLab `main`. During this migration stage GitHub remains authoritative for source
`dev` and signed `v*` release tags, which continue to mirror one-way to GitLab.
Publication branches are now forge-owned and must never be mirrored between forges.
The provider boundary must preserve GitHub rendering, fallback semantics, and Profile
Health component names while allowing GitLab-native professional signals.

GitLab anonymous user lookup supplies the basic public identity needed for a
functional profile (name, avatar and profile URL). Richer documented user detail
such as biography, website, linked accounts and follower data requires signed-in
Users API access. `CI_JOB_TOKEN` does not support that API family, so the provider
does not introduce a persistent PAT merely for presentation data. Optional fields
are omitted or recovered from an existing repository reference where the renderer
already defines that fallback; absence is not a health incident.

GitLab CI should prefer predefined variables such as `CI_API_V4_URL` and
`CI_PROJECT_ROOT_NAMESPACE`. The native `CI_JOB_TOKEN` is reserved for CI actions
whose endpoints explicitly support it, notably publication Git pushes once that
project setting is enabled. It must not be treated as a general-purpose GitLab API
token.

Forge-specific cached content must never cross publication targets. Stats and
language assets use forge-specific stems where necessary, and FEATURED PROJECTS /
COMMUNITY cache reuse is allowed only when the existing README identifies the same
forge through its stats section marker. GitLab activity is intentionally omitted:
GitLab does not currently expose its profile contribution calendar through a
supported public API, so unsupported data is not approximated or reported as an
incident.

Important source-of-truth rules:

- `CONTACT`: destinations are dynamic/config-derived links, but the visual badges
  are repository-local static SVG assets under `assets/badges/contact/`. They are
  intentionally not regenerated by the workflow and must keep the established
  Shields-style appearance. Only the surrounding `<a href>` destinations change.
- `FEATURED PROJECTS`: GitHub uses profile pinned repositories in their configured
  order. GitLab first selects public personal projects carrying the
  `profile-featured` topic; if none are tagged, it deterministically falls back to
  the most relevant public personal projects by stars and recent activity. No
  second hand-maintained project list belongs in this file.
- `SECURITY PRACTICE`: `profile.config.toml`; only distinct platforms with a
  professional public profile. Platform credentials/tokens belong only in the active
  forge CI secret store, never in TOML or generated content. HTB uses `HTB_TOKEN`.
- `SECURITY RESEARCH`: real-world, externally verifiable research only. HackerOne
  uses the official Hacker API with the public profile handle as API username and
  `HACKERONE_API_TOKEN` from the active forge CI secret store. `/hackers/me/reports` supplies
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
- `MOST USED LANGUAGES`: GitHub aggregates language byte counts across public
  owned non-fork repositories. GitLab exposes per-project language percentages, so
  its provider aggregates those percentages with equal project weight instead of
  pretending that GitLab supplies repository byte counts.
- `COMMUNITY`: followers from the active forge; avoid duplicating follower count in
  Stats. GitHub keeps its dynamic follower-count Shields.io badges. GitLab omits
  COMMUNITY because follower and rich user-detail endpoints require signed-in user
  access and are not available to `CI_JOB_TOKEN`; a persistent token is not added
  solely for this presentation feature. Do not vendor changing community assets as
  static source files.

## 5. Resilience and fallbacks

Dynamic content follows this priority:

1. fresh valid data -> render/update it
2. source/build failure + last valid generated content exists -> preserve it
3. no valid current or cached content -> omit the card/section
4. record the failure in Profile Health

Never let a transient API failure replace a good public card with an error
placeholder.

Generated components are stored as `assets/generated/<stem>.svg`. Build the
complete document in a temporary file before atomically replacing the previous
asset, so a failed refresh cannot destroy last-good public content.

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
- Repository Issues must therefore be enabled and the GitHub workflow permissions
  include `issues: write`. During the migration, GitHub remains the operational
  incident tracker; GitLab CI uses the same last-good generation behavior but does not
  create a second forge-specific incident system yet.

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
9. Generate dynamic SVGs through the shared asset helpers. Stable, intentionally
   vendored visual assets such as CONTACT badges belong outside
   `assets/generated/`.
10. Add new generated assets to the normal active-asset lifecycle; never commit
    generated SVGs to the source ZIP/dev branch.

## 8. Git branches, release tags and publication lineage

`dev` is the editable source branch. `main` is a generated publication branch on
each forge. Release tags version source revisions, not generated publication
snapshots.

### Transitional authority during the GitLab migration

Until the source-of-truth cutover is completed:

- GitHub `dev` remains the canonical editable source branch;
- annotated, signed release tags matching `v*` identify commits from that `dev`
  lineage and remain canonical on GitHub;
- GitHub mirrors only `dev` and exact `v*` tag refs one-way to GitLab;
- GitHub `main` is generated only by GitHub Actions with `PROFILE_FORGE=github`;
- GitLab `main` is generated only by GitLab CI with `PROFILE_FORGE=gitlab`;
- **`main` is never mirrored between forges.** The two publication branches are
  expected to diverge because their forge-native statistics and links differ;
- GitLab must not create independent release tags while GitHub remains the source
  authority.

The remaining GitHub -> GitLab source mirror is deliberately temporary. It keeps
GitLab `dev` and `v*` synchronized while GitLab publication is validated. A later
migration stage reverses source synchronization to GitLab -> GitHub, then removes
the old GitHub -> GitLab mirror credentials and logic.

### Publication lineage

Both forge publication workflows preserve the same source-history invariant:

- source-only files such as `README.template` and `profile.config.toml` stay on
  `dev`;
- each forge `main` contains only its public snapshot, automation files required by
  that forge, licensing files, static assets, and generated assets;
- every new `dev` commit becomes reachable from that forge's `main` without
  rewriting the source commit;
- existing `dev` commits are never rebased, squashed, cherry-picked, or recreated
  during publication, which preserves commit IDs, authorship, and signatures.

When `origin/dev` is not already an ancestor of a forge's current `main`, its
publication workflow starts an `ours` merge with `--no-commit`. The `ours` strategy
records `dev` as a second parent while retaining the current publication tree. The
selective `rsync`, README generation, and asset generation then build the exact
forge-specific public snapshot, and one publication commit records both parents.
On scheduled refreshes where `dev` is already an ancestor, publication remains a
normal single-parent snapshot commit.

Do not replace this lineage with squash/rebase publication or a force push. Those
approaches either rewrite signed source commits or disconnect them from the default
branch, defeating contribution attribution and signature preservation.

### GitLab CI publication credentials

GitLab publication uses the native `CI_JOB_TOKEN`, not a personal, project, or
group access token and not a second publication deploy key. `CI_REPOSITORY_URL`
provides the job-scoped HTTPS Git URL and contains that ephemeral token. GitLab
revokes the token when the job ends, and pushes authenticated with a job token do
not trigger another pipeline, which prevents publication loops.

The GitLab project must be configured before the first publication job runs:

1. In **Settings -> CI/CD -> Job token permissions**, enable **Allow Git push
   requests to the repository**. Keep cross-project job-token pushes disabled; this
   pipeline only needs same-project publication.
2. Protect `dev` and `main`, disable force-push on both, and protect the `v*` tag
   namespace. `dev` must still allow the temporary GitHub -> GitLab mirror deploy
   key to update the source ref, while `main` must allow the Maintainer/Owner
   identity whose pipeline uses `CI_JOB_TOKEN` to perform the publication push.
3. Store `AUTHORIZED_GPG_FINGERPRINT`, `HTB_TOKEN`, and `HACKERONE_API_TOKEN` as
   protected GitLab CI/CD variables. The fingerprint is a trust anchor, not a
   secret, and must contain the complete primary-key fingerprint. The external
   platform tokens remain secrets and must never be written into the repository.
4. Register one project-local **system-mode shell runner** named `oda-alexandre`
   and tagged `oda-alexandre-gitlab-runner`; disable untagged jobs and mark it
   protected. This intentionally matches the existing Starfighter host model and
   avoids GitLab.com hosted-runner minutes for source-changing `dev`, `v*`, and
   manual pipelines. The runner service executes as the dedicated `gitlab-runner`
   account, so `git`, `gpg`, `curl`, `python3`, `rsync`, and `gitleaks` must be
   available on that account's PATH. Scheduled refreshes use
   `saas-linux-small-amd64` and the pinned job images so the public profile can
   still refresh when the local runner is offline.
5. Create a daily GitLab pipeline schedule targeting `dev` after the first
   end-to-end publication succeeds. A small offset from the GitHub schedule (for
   example `10 0 * * *` UTC) avoids unnecessary simultaneous external API traffic.

### GitLab Free cryptographic gate

GitLab Free can display and verify GPG-signed commits, but the server-side push rule
that rejects unsigned commits is a Premium/Ultimate feature. This project therefore
uses CI as a compensating publication gate rather than pretending Free has an
equivalent pre-receive policy. The gate is deliberately stricter than a tip-only
check:

- `AUTHORIZED_GPG_FINGERPRINT` lives outside the repository as a protected CI/CD
  variable, so an unsigned source change cannot replace the configured trust anchor;
- the public key is downloaded from `${CI_SERVER_URL}/${CI_PROJECT_ROOT_NAMESPACE}.gpg`
  and its full fingerprint must contain the configured trust anchor before import;
- a `dev` push verifies every commit introduced by `CI_COMMIT_BEFORE_SHA..CI_COMMIT_SHA`;
- a `v*` release verifies both the annotated tag object with `git verify-tag` and
  the target source commit with `git verify-commit`;
- schedule/manual pipelines verify the source tip before publication;
- every successful cryptographic verification must resolve to the authorized primary
  key fingerprint, not merely to any valid key present on the GitLab profile.

This CI control **does not reject the Git push itself**: an unsigned commit can reach
`dev` before the pipeline fails. Protected branch/tag permissions therefore remain
part of the control boundary, and `publish_profile` cannot run after a failed
verification stage. When the source-of-truth cutover later moves `dev` to GitLab,
this limitation must remain explicit unless the subscription or enforcement model
changes.

A Gitleaks job scans source-changing pipelines on the same project-local shell
runner. It is intentionally skipped for daily schedules because those runs do not
introduce a new source tree and should consume only the minimum hosted-runner
compute required for verification and publication. Docker `image:` declarations
are relevant only to the hosted schedule path; the shell executor runs directly on
the trusted host and therefore uses the preinstalled local toolchain.

The `.gitlab-ci.yml` pipeline is accepted for `dev` pushes, signed SemVer `v*` tag
pushes, schedules, and manual web runs. Regardless of the triggering ref, it
explicitly fetches and publishes `origin/dev`, so release and scheduled refreshes
cannot render a stale publication branch as source. `main` pushes made with
`CI_JOB_TOKEN` do not create a second pipeline.

Repository branch rules should match this model: `dev` must block deletion and
force-push and is cryptographically gated by CI; each forge `main` should block
deletion/force-push but must allow its publication identity's normal fast-forward
push. Generated publication commits remain unsigned unless a dedicated publication
signing identity is introduced, so signed-source enforcement applies to `dev` and
release tags, not generated `main` snapshots.

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

