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
- Forge Activity card title and contribution count -> centered.
- Stats KPI cells -> centered.
- Languages: language label left, percentage right, because both describe a bar.
- Heatmap: month labels are centered over the visible week columns occupied by each month; leading/trailing partial months remain visible. Weekday labels follow the native forge convention (GitHub: Sunday-based grid with Mon/Wed/Fri labels; GitLab: Monday-based grid with M/W/F/S labels). The Less/More legend stays aligned to the grid.
- Workflow labels/connectors follow the diagram geometry.

Do not center chart axes merely for visual symmetry.

## 3. Card families

All generated SVG cards share the same frame, glow, radius, adaptive color
palette, and font family through `svg_card_frame()`, `svg_card_document()`, and
`svg_typography_css()`. Each component remains one transparent SVG. Its internal
CSS uses `prefers-color-scheme` to select the historical light/dark foreground
and track palettes; do not reintroduce separate `-light.svg` / `-dark.svg`
assets.

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
  exposes a reviewed contribution-calendar source that can be validated and
  protected by the normal last-good fallback
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
GitLab `main`. GitLab `dev` is the canonical source branch and signed `v*` release
tags are canonical GitLab source refs. After cryptographic validation, GitLab CI
mirrors only those source refs to GitHub; publication branches are forge-owned and
must never be mirrored between forges. The provider boundary must preserve GitHub
rendering, fallback semantics, and Profile Health component names while allowing
GitLab-native professional signals.

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

Forge-specific cached content must never cross publication targets. Stats,
language and Activity assets use forge-specific stems where necessary, and
FEATURED PROJECTS / COMMUNITY cache reuse is allowed only when the existing README
identifies the same forge through its stats section marker. GitLab Activity uses
the unauthenticated profile-calendar route `/users/<username>/calendar.json`, the
same aggregated visible-contribution source used by the profile UI. GitLab does
not document this route as a stable public API, so its schema is validated
strictly and any network/schema failure is a Profile Health incident handled by
the normal last-good fallback. Do not add a persistent PAT solely for Activity.

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
GitLab Issues is the canonical operational incident tracker even when the failing
automation runs on GitHub.

- README stays clean through fallback/omission.
- the configured `PROFILE_HEALTH_FILE` records degraded sources/build failures
  for the current generation job.
- `scripts/report_profile_health.py` sends both forges to the same GitLab project,
  but keeps **two independent incident streams**: one for GitHub Actions and one
  for GitLab CI. A healthy run from one forge must never close the other forge's
  incident.
- at most one managed issue is open per forge. A later healthy run adds a recovery
  note and closes that forge's issue; a later unrelated incident starts a new
  issue, preserving an incident history instead of reopening one issue forever.
- the reporter owns and reconciles the project labels `health::incident`,
  `forge::github`, and `forge::gitlab`. Each managed issue carries the global
  health label plus exactly one forge-scoped label.
- managed issues are confidential and may be assigned to the configured GitLab
  maintainer when that username can be resolved.
- GitHub Actions separates publication from health reporting. The publication
  job exports step outcomes plus a Base64 copy of the small generator health
  report; a distinct `if: always()` job sends those signals to GitLab. This means
  a failed or timed-out publication job can still produce an incident when the
  reporting job can run. GitHub needs no Issues write permission; the GitLab
  service-account token exists only in the dedicated reporting job as an Actions
  secret.
- GitLab CI adds a `when: always` watchdog on GitLab-hosted compute with
  `needs: []`, so it starts independently of the local-runner path. It polls the
  expected pipeline jobs through the GitLab API, reports failed/cancelled jobs,
  and treats a job still non-terminal after ten minutes as an incident instead of
  requiring the maintainer to notice a stuck pipeline manually.
- the GitLab publication job preserves `.profile-health.gitlab.json` as a short-
  lived artifact. After a successful publication job, the watchdog retrieves that
  exact job artifact through the GitLab API and merges recoverable generator
  incidents into the GitLab CI issue without making the report a source file.
- the automation credential must belong to a project-scoped GitLab service
  account with Reporter access and `api` scope. It is an observability credential,
  not a repository-write identity. Use separate tokens for GitLab CI and GitHub
  Actions so either integration can be revoked or rotated independently.

Do not weaken issue de-duplication, merge the two forge states into one issue, or
let one forge close the other forge's active incident.

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

### Canonical source authority

GitLab is the canonical source authority:

- GitLab `dev` is the editable source branch;
- annotated, signed release tags matching `v*` identify commits from that GitLab
  `dev` lineage and are created canonically on GitLab;
- after CI verifies the complete trusted source history, GitLab mirrors only the
  triggering `dev` source commit or exact signed `v*` tag object to GitHub;
- GitHub `dev` and `v*` are mirrors and must never become independent source refs;
- GitHub `main` is generated only by GitHub Actions with `PROFILE_FORGE=github`;
- GitLab `main` is generated only by GitLab CI with `PROFILE_FORGE=gitlab`;
- **`main` is never mirrored between forges.** The two publication branches are
  expected to diverge because their forge-native statistics and links differ.

No GitHub -> GitLab source mirror exists in steady state. GitHub Actions never
pushes `dev`, release tags, or `main` to GitLab. The
only cross-forge source direction is GitLab -> GitHub for `dev` and signed `v*`;
publication branches remain forge-local.

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

When the verified source commit is not already an ancestor of a forge's current
`main`, its publication workflow starts an `ours` merge with `--no-commit`. The
`ours` strategy records that immutable source commit as a second parent while
retaining the current publication tree. The selective `rsync`, README generation,
and asset generation then build the exact forge-specific public snapshot, and one
publication commit records both parents. On scheduled refreshes where the verified
`dev` commit is already an ancestor, publication remains a normal single-parent
snapshot commit. A stale push pipeline whose source commit is already represented
in GitLab `main` exits without regenerating an older public tree.

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
2. Protect `dev` and `main` and disable force-push on both. Keep **Allowed to
   merge** set to `No one` and **Allowed to push and merge** set to `Maintainers`
   on both branches. The latter permission is required for the Maintainer/Owner
   identity whose pipeline uses `CI_JOB_TOKEN` and already grants the merge
   permission GitLab requires for protected-branch schedules; do not widen the
   separate merge rule just for scheduling. No GitHub -> GitLab deploy key is
   permitted on either branch. Protect the `v*` namespace as immutable release
   refs.
3. Store `AUTHORIZED_GPG_FINGERPRINT`, `HTB_TOKEN`, `HACKERONE_API_TOKEN`,
   `GITHUB_MIRROR_APP_ID`, and `GITHUB_MIRROR_APP_PRIVATE_KEY_B64` as protected
   GitLab CI/CD variables. The fingerprint and App ID are identifiers/trust
   configuration rather than secrets. External-platform tokens and the Base64
   private key are `Masked and hidden` secrets and must never be written into the
   repository or logs.
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
   end-to-end publication succeeds. Use cron `10 0 * * *` with the schedule
   timezone explicitly set to **UTC**, keeping a small offset from the GitHub
   `0 0 * * *` UTC schedule and avoiding simultaneous external API traffic.

### GitLab Free cryptographic gate

GitLab Free can display and verify GPG-signed commits, but the server-side push rule
that rejects unsigned commits is a Premium/Ultimate feature. This project therefore
uses CI as a compensating publication/mirroring gate. It deliberately validates a
stable source-history trust boundary rather than only the most recent push range:

- `AUTHORIZED_GPG_FINGERPRINT` lives outside the repository as a protected CI/CD
  variable and identifies the only accepted signing key;
- `SOURCE_TRUST_ANCHOR_SHA` is the bootstrap commit verified on both forges before
  the canonical GitLab history advanced. The pipeline verifies that anchor itself
  on every run;
- the public key is downloaded from `${CI_SERVER_URL}/${CI_PROJECT_ROOT_NAMESPACE}.gpg`
  and its full fingerprint must contain the configured key before import;
- every commit reachable from `SOURCE_TRUST_ANCHOR_SHA..CI_COMMIT_SHA` is verified,
  so an unsigned commit that once made a pipeline fail cannot become implicitly
  trusted by a later signed commit;
- every pushed tag in the protected `v*` namespace enters CI; the tag name must
  match the supported version format, the release must be annotated, it must target
  a commit in canonical `dev` history, and both the tag object and all source history
  through its target must validate against the authorized key;
- schedule and manual pipelines are accepted only when they target `dev`, so the
  immutable `CI_COMMIT_SHA` that is verified is also the exact source object later
  published;
- `publish_profile` is intentionally skipped for tag pipelines. Tags version source
  history; they do not independently refresh the GitLab publication branch.

This CI control **does not reject the Git push itself**: an unsigned commit can reach
GitLab `dev` before the pipeline fails. It cannot, however, be published to GitLab
`main` or mirrored to GitHub, and every later pipeline continues to encounter and
reject that unsigned history. Protected branch/tag permissions remain part of the
control boundary.

A Gitleaks job scans source-changing pipelines on the protected project-local shell
runner. It is intentionally skipped for daily schedules because those runs do not
introduce a new source tree and should consume only the minimum hosted-runner
compute required for verification and publication. Docker `image:` declarations
are relevant only to the hosted schedule path; the shell executor runs directly on
the trusted host and therefore uses the preinstalled local toolchain.

### GitLab -> GitHub source mirror credentials and rules

The canonical source mirror uses a repository-scoped GitHub App named
`oda-alexandre-gitlab-mirror`, not a persistent PAT or another write deploy key.
The App installation is limited to `oda-alexandre/oda-alexandre` and grants only
repository metadata read plus `Contents: read/write` and `Workflows: read/write`.
The latter is required because canonical `dev` contains `.github/workflows/`.

GitLab stores the App ID plus a Base64-encoded private key as protected CI/CD
variables. `scripts/mirror_source_to_github.py` creates a short-lived RS256 App JWT,
resolves the repository installation dynamically, and requests a one-hour
installation token scoped back down to this repository with `contents:write` and
`workflows:write`. Git authentication uses an HTTP extra header kept only in the
job process environment; the token is never placed in a remote URL or printed.

The mirror job runs only for push pipelines on canonical `dev` or signed release
tags that match the supported `v*` version format, after all earlier pipeline
stages succeed. It never runs for schedules or manual refreshes and has no code
path for `main`:

- `dev` is fast-forwarded only to the exact `CI_COMMIT_SHA` validated by that
  pipeline. Divergence is an error; force-push is never used. A stale concurrent
  pipeline may safely no-op only when GitHub's newer tip is already contained in
  current canonical GitLab `dev`;
- a tag job mirrors only its triggering annotated tag object. Existing GitHub tags
  must have the exact same object ID; differing objects are an immutability error;
- a tag is not created on GitHub until GitHub `dev` already contains its target
  commit, preserving branch/tag ordering and exact source lineage.

GitHub repository rulesets intentionally separate authorization from immutable
history controls:

- `dev-source-write-authorization`: creation/update bypass for the installed App
  only; GitHub `dev` is not an editable source branch;
- `dev-source-history-protection`: signed commits required, deletion and force-push
  blocked, no bypass;
- `release-tag-creation-authorization`: tag creation bypass for the installed App
  only; release tags are created canonically on GitLab;
- `release-tag-immutability-and-signatures`: updates, deletion and force-push
  blocked plus signed commits required, no bypass;
- GitHub `main` remains governed separately by `main-publication-*` rules and its
  existing publication deploy key. The mirror App receives no `main` bypass. The
  ruleset bypass applies to the `DeployKey` actor category rather than one named
  key, so do not add another write-enabled deploy key to this repository.

Steady state contains no GitHub -> GitLab source credentials or mirror step.
GitHub source/tag authorization rules contain only the mirror App, making GitHub
`dev` and `v*` technical mirrors. GitHub Actions publishes `main` from mirrored
`dev`; mirrored release-tag creation does not trigger a redundant profile publish.
A daily GitLab schedule may refresh GitLab `main` independently from source changes.

Repository branch rules should match this model: GitLab `dev` blocks force-push and
is cryptographically gated by CI; each forge `main` blocks deletion/force-push but
allows only its publication identity's normal fast-forward push. Generated
publication commits remain unsigned unless a dedicated publication signing identity
is introduced, so signed-source enforcement applies to `dev` and release tags, not
generated `main` snapshots.

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
