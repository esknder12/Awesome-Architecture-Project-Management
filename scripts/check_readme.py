#!/usr/bin/env python3
"""Validate the Awesome-Architecture-Project-Management curated list.

This repository is a markdown-only "awesome list", so its tests are content
tests: heading/TOC integrity, table well-formedness, link and image targets,
HTML/badge hygiene, and (with --online) whether the listed GitHub projects
actually exist, are maintained, and are sorted as the README claims.

Usage
-----
    python3 scripts/check_readme.py                 # offline checks (fast, no network)
    python3 scripts/check_readme.py --online        # + GitHub API checks
    python3 scripts/check_readme.py --online --check-external   # + SaaS site reachability
    python3 scripts/check_readme.py --strict        # warnings fail the run too
    python3 scripts/check_readme.py --json          # machine-readable report

Exit codes: 0 = pass, 1 = failures (or warnings with --strict), 2 = bad invocation.

Only the standard library is required (Python 3.8+).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"

# Sections every "awesome list" in this series is expected to carry.
REQUIRED_SECTIONS = [
    "Market Size & Industry Dynamics",
    "SaaS & Commercial Hosted Platforms",
    "Open-Source GitHub Projects",
    "How to Contribute",
    "Support & Sponsorship",
    "Star History",
    "Disclaimer",
]

GITHUB_REPO_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
LOOSE_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]*)\)")
TITLED_TARGET_RE = re.compile(r"^\S+\s+\"[^\"]*\"$")
HTML_ATTR_RE = re.compile(r"<(?:a|img)\b[^>]*?\b(?:href|src)=\"([^\"]+)\"", re.IGNORECASE)
IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
BADGE_REPO_RE = re.compile(r"img\.shields\.io/github/stars/([^?\"\s]+)")
SEPARATOR_CELL_RE = re.compile(r"^\s*:?-{3,}:?\s*$")


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
@dataclass
class Finding:
    level: str  # "error" | "warning" | "info"
    check: str
    message: str
    line: int | None = None

    def render(self) -> str:
        where = f"{README.name}:{self.line}" if self.line else README.name
        tag = {"error": "FAIL", "warning": "WARN", "info": "INFO"}[self.level]
        return f"[{tag}] {self.check} ({where}): {self.message}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0
    skipped: list[str] = field(default_factory=list)

    def add(self, level: str, check: str, message: str, line: int | None = None) -> None:
        self.findings.append(Finding(level, check, message, line))

    def error(self, check: str, message: str, line: int | None = None) -> None:
        self.add("error", check, message, line)

    def warn(self, check: str, message: str, line: int | None = None) -> None:
        self.add("warning", check, message, line)

    def info(self, check: str, message: str, line: int | None = None) -> None:
        self.add("info", check, message, line)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]


# --------------------------------------------------------------------------- #
# Markdown helpers
# --------------------------------------------------------------------------- #
def slugify(text: str, seen: dict[str, int]) -> str:
    """Reproduce github-slugger (v2) closely enough for GitHub heading anchors.

    Rule: lowercase, drop characters that are not letters/digits/space/hyphen/
    underscore/combining mark (this removes emoji and punctuation but keeps
    variation selectors such as U+FE0F), then map spaces to hyphens without
    collapsing repeats. Duplicate slugs get -1, -2, ... suffixes.
    """
    kept = []
    for ch in text.lower():
        if ch in "-_ " or ch.isalnum() or unicodedata.category(ch).startswith("M"):
            kept.append(ch)
    slug = "".join(kept).replace(" ", "-")
    if slug in seen:
        seen[slug] += 1
        slug = f"{slug}-{seen[slug]}"
    else:
        seen[slug] = 0
    return slug


def parse_headings(lines: list[str]) -> list[dict]:
    seen: dict[str, int] = {}
    headings = []
    in_fence = False
    for idx, line in enumerate(lines, start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_RE.match(line)
        if match:
            text = match.group(2)
            headings.append(
                {"level": len(match.group(1)), "text": text, "slug": slugify(text, seen), "line": idx}
            )
    return headings


def split_row(line: str) -> list[str]:
    """Split a markdown table row into cells, honouring escaped pipes."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    cells, current, escaped = [], [], False
    for ch in body:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    cells.append("".join(current).strip())
    return cells


def strip_markdown(text: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("**", "").replace("`", "").strip()


# --------------------------------------------------------------------------- #
# Offline checks
# --------------------------------------------------------------------------- #
def check_encoding(raw: bytes, report: Report) -> list[str] | None:
    check = "encoding"
    if not raw:
        report.error(check, "README.md is empty")
        return None
    if raw.startswith(b"\xef\xbb\xbf"):
        report.error(check, "file starts with a UTF-8 BOM; GitHub renders it as a stray character")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        report.error(check, f"file is not valid UTF-8: {exc}")
        return None
    if "\r\n" in text:
        report.warn(check, "file uses CRLF line endings; the repo convention is LF")
    if "\t" in text:
        report.warn(check, "file contains tab characters; they can break table alignment")
    if not text.endswith("\n"):
        report.warn(check, "file does not end with a trailing newline")
    report.checked += 1
    return text.split("\n")


def check_headings(lines: list[str], report: Report) -> list[dict]:
    check = "headings"
    headings = parse_headings(lines)
    h1 = [h for h in headings if h["level"] == 1]
    if len(h1) != 1:
        report.error(check, f"expected exactly one H1 title, found {len(h1)}")
    if h1 and h1[0]["line"] != 1:
        report.warn(check, f"H1 should be the first line, found on line {h1[0]['line']}", h1[0]["line"])

    present = {strip_markdown(h["text"]).casefold() for h in headings}
    for section in REQUIRED_SECTIONS:
        if section.casefold() not in {re.sub(r"^[^\w]+", "", p).strip() for p in present} and not any(
            section.casefold() in p for p in present
        ):
            report.error(check, f"required section missing: '## {section}'")

    for idx in range(1, len(headings)):
        if headings[idx]["level"] - headings[idx - 1]["level"] > 1:
            report.warn(
                check,
                f"heading level jumps from H{headings[idx-1]['level']} to H{headings[idx]['level']}",
                headings[idx]["line"],
            )
    report.checked += len(headings)
    return headings


def check_toc(lines: list[str], headings: list[dict], report: Report) -> None:
    check = "toc"
    anchors = {h["slug"] for h in headings}
    in_page_links: list[tuple[int, str]] = []
    for idx, line in enumerate(lines, start=1):
        for match in MARKDOWN_LINK_RE.finditer(line):
            target = match.group(2)
            if target.startswith("#"):
                in_page_links.append((idx, target))

    if not in_page_links:
        report.warn(check, "no in-page (TOC) links found")
        return

    for line_no, target in in_page_links:
        anchor = urllib.parse.unquote(target[1:])
        if anchor not in anchors:
            report.error(
                check,
                f"TOC anchor '{target}' does not match any heading slug "
                f"(closest slugs: {', '.join(sorted(anchors)[:3])}...)",
                line_no,
            )

    linked = {urllib.parse.unquote(t[1:]) for _, t in in_page_links}
    for heading in headings:
        if heading["level"] == 2 and heading["slug"] not in linked:
            if "table of contents" in heading["text"].casefold():
                continue
            report.warn(
                check,
                f"H2 '{strip_markdown(heading['text'])}' is not listed in the Table of Contents",
                heading["line"],
            )
    report.checked += len(in_page_links)


def check_tables(lines: list[str], report: Report) -> None:
    check = "tables"
    idx, tables = 0, 0
    while idx < len(lines):
        if not lines[idx].lstrip().startswith("|"):
            idx += 1
            continue
        start = idx
        block = []
        while idx < len(lines) and lines[idx].lstrip().startswith("|"):
            block.append((idx + 1, lines[idx]))
            idx += 1
        tables += 1
        if len(block) < 3:
            report.error(check, f"table starting line {start+1} has {len(block)} row(s); need header, separator, data", start + 1)
            continue

        header_line, header = block[0]
        columns = len(split_row(header))
        separator_line, separator = block[1]
        sep_cells = split_row(separator)
        if len(sep_cells) != columns:
            report.error(
                check,
                f"separator row has {len(sep_cells)} cells but header has {columns}",
                separator_line,
            )
        for cell in sep_cells:
            if not SEPARATOR_CELL_RE.match(cell):
                report.error(check, f"invalid separator cell '{cell}' (expected ---, :---, ---: or :---:)", separator_line)
                break

        for line_no, line in block[2:]:
            cells = split_row(line)
            if len(cells) != columns:
                report.error(
                    check,
                    f"row has {len(cells)} cells but header has {columns} — table will render broken",
                    line_no,
                )
            elif not cells[0]:
                report.error(check, "row has an empty first column", line_no)
        report.checked += len(block)
    if tables == 0:
        report.warn(check, "no markdown tables found (expected the SaaS comparison table)")


def collect_links(lines: list[str]) -> list[tuple[int, str, str]]:
    links = []
    for idx, line in enumerate(lines, start=1):
        for match in MARKDOWN_LINK_RE.finditer(line):
            links.append((idx, match.group(1), match.group(2)))
        for match in HTML_ATTR_RE.finditer(line):
            links.append((idx, "", match.group(1)))
    return links


def check_links(lines: list[str], report: Report) -> list[tuple[int, str]]:
    check = "links"
    links = collect_links(lines)
    external: dict[str, int] = {}

    # A URL containing a raw space does not render as a link on GitHub; the strict
    # link regex above stops at the space, so catch those spans separately.
    for idx, line in enumerate(lines, start=1):
        for match in LOOSE_LINK_RE.finditer(line):
            target = match.group(1).strip()
            if " " in target and not TITLED_TARGET_RE.match(target):
                report.error(
                    check,
                    f"link target contains an unencoded space, so it will not render: '{target}'",
                    idx,
                )
    for line_no, text, target in links:
        if not target.strip():
            report.error(check, "link with an empty target", line_no)
            continue
        if " " in target:
            report.error(check, f"link target contains a space, so it will not render: '{target}'", line_no)
            continue
        if target.startswith("#"):
            continue
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme in ("http", "https"):
            if parsed.scheme == "http":
                report.warn(check, f"insecure http:// link: {target}", line_no)
            external[target] = external.get(target, 0) + 1
            if not parsed.netloc:
                report.error(check, f"malformed URL: {target}", line_no)
        elif parsed.scheme in ("mailto", ""):
            rel = urllib.parse.unquote(parsed.path)
            if not rel:
                report.error(check, f"relative link with no path: '{target}'", line_no)
                continue
            if not (REPO_ROOT / rel).exists():
                report.error(check, f"relative link/image target does not exist on disk: '{rel}'", line_no)
        else:
            report.warn(check, f"unusual URL scheme '{parsed.scheme}' in '{target}'", line_no)

        if target.startswith("http") and not text and "<img" not in lines[line_no - 1].lower():
            report.info(check, f"link with empty link text (screen readers see only the URL): {target}", line_no)

    for url, count in external.items():
        if count > 1:
            report.warn(check, f"duplicate link target appears {count}x: {url}")
    report.checked += len(links)
    return [(line, target) for line, _, target in links]


def check_html(lines: list[str], report: Report) -> None:
    check = "html"
    for tag in ("p", "a", "b", "div", "table"):
        opens = sum(len(re.findall(rf"<{tag}\b", line, re.I)) for line in lines)
        closes = sum(len(re.findall(rf"</{tag}>", line, re.I)) for line in lines)
        if opens != closes:
            report.error(check, f"unbalanced <{tag}> tags: {opens} opening vs {closes} closing")
    for idx, line in enumerate(lines, start=1):
        for tag in IMG_TAG_RE.findall(line):
            if not re.search(r'\balt="[^"]*"', tag, re.I):
                report.error(check, f"<img> without alt text: {tag[:80]}", idx)
            if not re.search(r'\bsrc="[^"]+"', tag, re.I):
                report.error(check, "<img> without src", idx)
    report.checked += 4


def parse_awesome_entries(lines: list[str]) -> list[dict]:
    """Extract '- **[Name](github url)** ... badge ...' entries from the open-source section."""
    entries, in_section = [], False
    for idx, line in enumerate(lines, start=1):
        if line.startswith("## "):
            in_section = "open-source" in line.casefold()
            continue
        if not in_section:
            continue
        match = re.match(r"^- \*\*\[([^\]]+)\]\((https?://[^)]+)\)\*\*", line)
        if not match:
            continue
        badge = BADGE_REPO_RE.search(line)
        entries.append(
            {
                "line": idx,
                "name": match.group(1),
                "url": match.group(2),
                "badge_repo": badge.group(1) if badge else None,
                "summary": lines[idx].strip() if idx < len(lines) else "",
            }
        )
    return entries


def check_open_source_section(lines: list[str], report: Report) -> list[dict]:
    check = "open-source"
    entries = parse_awesome_entries(lines)
    if len(entries) < 5:
        report.error(check, f"expected at least 5 open-source entries, found {len(entries)}")
    seen_urls: dict[str, int] = {}
    for entry in entries:
        repo = GITHUB_REPO_RE.match(entry["url"])
        owner_repo = f"{repo.group(1)}/{repo.group(2)}" if repo else None
        if not repo:
            report.error(check, f"'{entry['name']}' does not link to a github.com/owner/repo URL: {entry['url']}", entry["line"])
        if not entry["badge_repo"]:
            report.warn(check, f"'{entry['name']}' is missing a shields.io GitHub stars badge", entry["line"])
        elif owner_repo and entry["badge_repo"].casefold() != owner_repo.casefold():
            report.error(
                check,
                f"'{entry['name']}' links to {owner_repo} but its star badge points at {entry['badge_repo']}",
                entry["line"],
            )
        if not entry["summary"] or len(entry["summary"]) < 20:
            report.warn(check, f"'{entry['name']}' has no description line under it", entry["line"])
        key = entry["url"].rstrip("/").casefold()
        if key in seen_urls:
            report.warn(check, f"'{entry['name']}' duplicates the entry on line {seen_urls[key]}", entry["line"])
        seen_urls[key] = entry["line"]
    report.checked += len(entries)
    return entries


def check_saas_table(lines: list[str], report: Report) -> None:
    check = "saas"
    in_section, header_cols, rows = False, [], 0
    for idx, line in enumerate(lines, start=1):
        if line.startswith("## "):
            in_section = "saas" in line.casefold()
            continue
        if not in_section or not line.lstrip().startswith("|"):
            continue
        cells = split_row(line)
        if not header_cols:
            header_cols = [strip_markdown(c).casefold() for c in cells]
            required = ["platform", "pricing", "free tier"]
            for need in required:
                if not any(need in col for col in header_cols):
                    report.error(check, f"SaaS table is missing a '{need}' column (header: {header_cols})", idx)
            continue
        if all(SEPARATOR_CELL_RE.match(c) for c in cells):
            continue
        rows += 1
        name = strip_markdown(cells[0])
        if not cells[0]:
            report.error(check, "SaaS row with no platform name", idx)
        if not MARKDOWN_LINK_RE.search(cells[0]):
            report.warn(check, f"SaaS row '{name}' is not linked to a product URL", idx)
        for col_idx, cell in enumerate(cells[1:], start=1):
            if not strip_markdown(cell):
                label = header_cols[col_idx] if col_idx < len(header_cols) else f"column {col_idx+1}"
                report.error(check, f"SaaS row '{name}' has an empty '{label}' cell", idx)
    if rows == 0:
        report.error(check, "no data rows found in the SaaS comparison table")
    else:
        report.info(check, f"{rows} SaaS platforms covered")
    report.checked += rows


def check_list_conventions(lines: list[str], report: Report) -> None:
    check = "conventions"
    text = "\n".join(lines)
    for phrase in ("sorted by **GitHub Stars", "sorted by **Company Size"):
        if phrase in text:
            report.info(check, f"README makes a sorting claim that --online verifies: '{phrase}'")
    if not any(re.match(r"^#{1,6}\s", line) and "table of contents" in line.casefold() for line in lines):
        report.warn(check, "no 'Table of Contents' section found")
    if "## " not in text:
        report.error(check, "document has no H2 sections")

    # Trailing spaces: exactly two is a deliberate markdown hard break, so only
    # flag the accidental cases (one space, or three or more).
    for idx, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        trailing = len(line) - len(line.rstrip(" "))
        if trailing in (1,) or trailing >= 3:
            report.info(check, f"{trailing} trailing space(s) on line {idx} — use 2 for a hard break, else remove")


# --------------------------------------------------------------------------- #
# Online checks
# --------------------------------------------------------------------------- #
def github_api(path: str) -> dict | None:
    """Call the GitHub REST API, preferring an authenticated `gh` client."""
    gh = shutil.which("gh")
    if gh:
        proc = subprocess.run([gh, "api", path], capture_output=True, text=True)
        if proc.returncode == 0:
            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError:
                return None
        return None

    token = __import__("os").environ.get("GITHUB_TOKEN")
    req = urllib.request.Request(f"https://api.github.com/{path.lstrip('/')}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "awesome-list-readme-check")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None


def check_github_repos(entries: list[dict], report: Report) -> dict[str, int]:
    check = "github-api"
    stars: dict[str, int] = {}
    if not entries:
        report.warn(check, "no open-source entries to verify online")
    for entry in entries:
        match = GITHUB_REPO_RE.match(entry["url"])
        if not match:
            continue
        owner_repo = f"{match.group(1)}/{match.group(2)}"
        data = github_api(f"repos/{owner_repo}")
        if data is None:
            report.error(check, f"GitHub API could not resolve {owner_repo} (deleted, renamed, private, or rate-limited)", entry["line"])
            continue
        canonical = data.get("full_name", owner_repo)
        if canonical.casefold() != owner_repo.casefold():
            report.error(
                check,
                f"'{owner_repo}' now redirects to '{canonical}' — update the link and badge",
                entry["line"],
            )
        if data.get("archived"):
            report.warn(check, f"'{canonical}' is archived/read-only", entry["line"])
        if data.get("disabled"):
            report.error(check, f"'{canonical}' is a disabled repository", entry["line"])
        if data.get("stargazers_count", 0) < 100:
            report.warn(check, f"'{canonical}' has only {data.get('stargazers_count')} stars", entry["line"])
        stars[canonical.casefold()] = int(data.get("stargazers_count", 0))
        entry["stars"] = stars[canonical.casefold()]
        entry["pushed_at"] = data.get("pushed_at")
        report.checked += 1
    return stars


def check_github_sorting(entries: list[dict], report: Report) -> None:
    check = "sorting"
    verified = [e for e in entries if "stars" in e]
    if len(verified) < 2:
        report.skipped.append("star-order check (needs --online and >= 2 resolvable repos)")
        return
    if "sorted by **GitHub Stars" not in README.read_text(encoding="utf-8"):
        report.skipped.append("star-order check (README does not claim star sorting)")
        return
    for prev, curr in zip(verified, verified[1:]):
        if curr["stars"] > prev["stars"]:
            report.warn(
                check,
                f"list claims 'sorted by GitHub Stars' descending, but {curr['name']} "
                f"({curr['stars']:,}★) has more stars than {prev['name']} ({prev['stars']:,}★) above it",
                curr["line"],
            )
        report.checked += 1


def http_status(url: str) -> tuple[int | None, str]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; awesome-list-link-check)"}
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, ""
        except urllib.error.HTTPError as exc:
            if method == "GET" or exc.code not in (403, 405):
                return exc.code, exc.reason or ""
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            return None, str(getattr(exc, "reason", exc))
    return None, "no response"


def check_external_links(links: list[tuple[int, str]], report: Report) -> None:
    check = "external-links"
    seen: set[str] = set()
    for line_no, target in links:
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc in ("github.com", "img.shields.io", "star-history.dera.page", "api.github.com"):
            continue  # github.com handled by --online API checks; badges are dynamic
        if target in seen:
            continue
        seen.add(target)
        status, reason = http_status(target)
        if status is None:
            report.warn(check, f"could not reach {target} ({reason}) — sandbox may block it; re-run in CI", line_no)
        elif status >= 400:
            # Many marketing sites reject bots; treat 403/429 as inconclusive.
            if status in (403, 429):
                report.warn(check, f"{target} returned {status} (bot protection?); verify manually", line_no)
            else:
                report.error(check, f"{target} returned HTTP {status}", line_no)
        report.checked += 1


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def run(online: bool, check_external: bool) -> Report:
    report = Report()
    if not README.exists():
        report.error("files", "README.md not found at repository root")
        return report

    raw = README.read_bytes()
    lines = check_encoding(raw, report)
    if lines is None:
        return report

    headings = check_headings(lines, report)
    check_toc(lines, headings, report)
    check_tables(lines, report)
    links = check_links(lines, report)
    check_html(lines, report)
    check_open_source_section(lines, report)
    entries = parse_awesome_entries(lines)
    check_saas_table(lines, report)
    check_list_conventions(lines, report)

    for asset in ("assets/banner.svg", "assets/social-preview.gif"):
        if not (REPO_ROOT / asset).exists():
            report.error("assets", f"referenced asset missing: {asset}")
        elif (REPO_ROOT / asset).stat().st_size == 0:
            report.error("assets", f"asset is empty: {asset}")
        else:
            report.checked += 1

    if online:
        check_github_repos(entries, report)
        check_github_sorting(entries, report)

    if check_external:
        check_external_links(links, report)

    if not online:
        report.skipped.append("GitHub repo existence / archive status / star counts (run with --online)")
        report.skipped.append("star-order sorting claim (run with --online)")
    if not check_external:
        report.skipped.append("SaaS product site reachability (run with --online --check-external)")
    report.skipped.append("SaaS 'Company Size / Valuation / Revenue' figures (no machine-readable source)")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the awesome-list README.")
    parser.add_argument("--online", action="store_true", help="verify GitHub repos, badges and star ordering via the GitHub API")
    parser.add_argument("--check-external", action="store_true", help="also probe SaaS product sites for HTTP errors")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    parser.add_argument("--quiet", action="store_true", help="only print problems and the summary")
    args = parser.parse_args(argv)

    if args.check_external and not args.online:
        args.online = True

    report = run(args.online, args.check_external)
    errors, warnings = report.errors, report.warnings
    failed = bool(errors) or (args.strict and bool(warnings))

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not failed,
                    "checked": report.checked,
                    "errors": [asdict(e) for e in errors],
                    "warnings": [asdict(w) for w in warnings],
                    "skipped": report.skipped,
                },
                indent=2,
            )
        )
    else:
        for finding in report.findings:
            if args.quiet and finding.level == "info":
                continue
            print(finding.render())
        print()
        print(f"checked {report.checked} items · {len(errors)} error(s) · {len(warnings)} warning(s)")
        if report.skipped:
            print("skipped:")
            for item in report.skipped:
                print(f"  - {item}")
        if failed:
            print("\nRESULT: FAIL")
        elif warnings:
            print("\nRESULT: PASS with warnings")
        else:
            print("\nRESULT: PASS")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
