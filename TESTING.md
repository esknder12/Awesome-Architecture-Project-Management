# 🧪 Testing this repository

This repo is a **curated markdown list** — there is no app to run, no build step, and no
runtime. "Testing" here means **validating the content**: that every heading, anchor,
table row, link, badge and image is correct, that the listed GitHub projects still exist,
and that the list obeys its own sorting claims.

Everything below is standard-library Python 3.8+ — **no dependencies to install**.

```bash
make test          # offline checks; fast, no network
make test-online   # + GitHub API: repos exist, badges match, star order
make test-all      # + probe each SaaS product site for HTTP errors (slow)
```

Equivalent without `make`:

```bash
python3 scripts/check_readme.py
python3 scripts/check_readme.py --online
python3 scripts/check_readme.py --online --check-external
```

Useful flags: `--strict` (warnings fail too), `--json` (machine-readable), `--quiet`.
Exit codes: `0` pass, `1` failures, `2` bad invocation.

Also run the validator's own test suite (19 tests, no network):

```bash
python3 -m unittest discover -s tests -v
```

## What each layer checks

| Layer | Checks | Runs in CI |
| :--- | :--- | :--- |
| **Files** | UTF-8 validity, no BOM, LF endings, trailing newline, tabs, no CRLF drift | on every push/PR |
| **Headings** | exactly one H1 and it is line 1, no H1→H3 level jumps, all required sections present | on every push/PR |
| **Table of contents** | every `#anchor` resolves to a real heading slug (emoji-safe, github-slugger compatible), every H2 listed | on every push/PR |
| **Tables** | consistent column count per row, valid `---` separator, no empty first column / pricing cell | on every push/PR |
| **Links** | no empty targets, no unencoded spaces, no `http://`, relative image/link targets exist on disk, duplicate URLs | on every push/PR |
| **HTML & badges** | balanced `<p>/<a>/<b>`, every `<img>` has `alt` + `src` | on every push/PR |
| **Awesome-list rules** | entry format `- **[Name](github url)**` + stars badge, badge repo == link repo, no duplicate entries, each entry has a description | on every push/PR |
| **GitHub API** (`--online`) | repo still exists, not archived/disabled, not silently renamed (redirects), star count | on every push/PR |
| **Sorting claims** (`--online`) | "sorted by GitHub Stars" really is descending | on every push/PR |
| **External sites** (`--check-external`) | each SaaS product URL returns < 400 | weekly schedule only |

### Why the emoji anchors matter

GitHub slugs keep Unicode variation selectors but drop emoji, so `## ⚠️ Disclaimer`
becomes `#%EF%B8%8F-disclaimer`, **not** `#disclaimer`. The slug function in
`scripts/check_readme.py` is verified against `github-slugger` output for every heading in
this README, so a broken TOC link fails the build instead of silently doing nothing.

## What cannot be automated

- **SaaS market figures** — valuations/revenue in the comparison table have no
  machine-readable source; they need a periodic manual review.
- **Pricing correctness** — row content is checked for completeness, not for the current
  price on the vendor's page (vendor pages change and often block bots).
- **Whether a tool actually belongs in the list** — editorial judgement.

## Adding an entry

1. SaaS: add a row to the `💼 SaaS & Commercial Hosted Platforms` table, filled in on
   **every** column (Platform link, description, size/revenue, pricing, free tier).
2. Open source: add under `💻 Open-Source GitHub Projects` using
   `- **[Name](https://github.com/owner/repo)** <stars badge>` followed by an indented
   description line — and keep the section's star order descending.
3. Run `make test-online` (or at least `make test`), then open the PR.

## CI

| Workflow | Trigger | Purpose |
| :--- | :--- | :--- |
| `.github/workflows/readme-check.yml` | push to `main`, every PR, manual | validator self-tests + `--strict` lint + GitHub API checks |
| `.github/workflows/link-health.yml` | weekly (Mon 06:00 UTC), manual | probes external SaaS sites; scheduled so a vendor outage never blocks a PR |

`--strict` means a stale warning (e.g. an unlisted H2, or the star order drifting) fails
the build, so the list cannot quietly rot.
