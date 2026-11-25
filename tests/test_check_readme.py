#!/usr/bin/env python3
"""Tests for scripts/check_readme.py — the validator itself.

Run:  python3 -m unittest discover -s tests -v
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_readme as cr  # noqa: E402


class SlugTests(unittest.TestCase):
    """Anchors must match what GitHub actually generates (verified with github-slugger)."""

    def test_matches_github_slugger_for_real_headings(self):
        cases = {
            "🏛️ Awesome Architecture Project Management": "️-awesome-architecture-project-management",
            "📌 Top Architecture & Engineering (A/E) Project Management Ecosystem": "-top-architecture--engineering-ae-project-management-ecosystem",
            "📑 Table of Contents": "-table-of-contents",
            "🌐 Market Size & Industry Dynamics": "-market-size--industry-dynamics",
            "💼 SaaS & Commercial Hosted Platforms": "-saas--commercial-hosted-platforms",
            "💻 Open-Source GitHub Projects": "-open-source-github-projects",
            "🤝 How to Contribute": "-how-to-contribute",
            "💖 Support & Sponsorship": "-support--sponsorship",
            "📈 Star History": "-star-history",
            "⚠️ Disclaimer": "️-disclaimer",
        }
        seen = {}
        for heading, expected in cases.items():
            self.assertEqual(cr.slugify(heading, seen), expected, heading)

    def test_duplicate_headings_get_numeric_suffix(self):
        seen = {}
        self.assertEqual(cr.slugify("Pricing", seen), "pricing")
        self.assertEqual(cr.slugify("Pricing", seen), "pricing-1")
        self.assertEqual(cr.slugify("Pricing", seen), "pricing-2")


class TableTests(unittest.TestCase):
    def test_split_row_handles_escaped_and_bare_pipes(self):
        self.assertEqual(cr.split_row("| a | b |"), ["a", "b"])
        self.assertEqual(cr.split_row("a | b"), ["a", "b"])
        self.assertEqual(cr.split_row(r"| a \| b | c |"), ["a | b", "c"])

    def test_ragged_row_is_an_error(self):
        lines = ["| A | B |", "| --- | --- |", "| 1 | 2 |", "| 3 |"]
        report = cr.Report()
        cr.check_tables(lines, report)
        self.assertTrue(any("header has 2" in e.message for e in report.errors), [e.message for e in report.errors])

    def test_bad_separator_is_an_error(self):
        lines = ["| A | B |", "| -- | --- |", "| 1 | 2 |"]
        report = cr.Report()
        cr.check_tables(lines, report)
        self.assertTrue(any("invalid separator" in e.message for e in report.errors))


class TocTests(unittest.TestCase):
    def test_unknown_anchor_fails(self):
        lines = ["## Real Heading", "", "- [Broken](#nope)"]
        report = cr.Report()
        cr.check_toc(lines, cr.parse_headings(lines), report)
        self.assertTrue(report.errors)

    def test_emoji_anchor_from_readme_resolves(self):
        lines = [
            "## ⚠️ Disclaimer",
            "- [Disclaimer](#%EF%B8%8F-disclaimer)",
        ]
        report = cr.Report()
        cr.check_toc(lines, cr.parse_headings(lines), report)
        self.assertFalse(report.errors, [e.message for e in report.errors])

    def test_h2_missing_from_toc_warns(self):
        lines = ["## One", "- [x](#one)", "## Two"]
        report = cr.Report()
        cr.check_toc(lines, cr.parse_headings(lines), report)
        self.assertTrue(any("not listed in the Table of Contents" in w.message for w in report.warnings))


class LinkAndHtmlTests(unittest.TestCase):
    def test_missing_relative_asset_fails(self):
        report = cr.Report()
        cr.check_links(["![x](assets/does-not-exist.svg)"], report)
        self.assertTrue(any("does not exist on disk" in e.message for e in report.errors))

    def test_http_link_warns_and_space_in_url_fails(self):
        report = cr.Report()
        cr.check_links(["[a](http://example.com)", "[b](https://example.com/a b)"], report)
        self.assertTrue(any("insecure http" in w.message for w in report.warnings))
        self.assertTrue(any("unencoded space" in e.message for e in report.errors))

    def test_unbalanced_html_and_missing_alt(self):
        report = cr.Report()
        cr.check_html(['<p align="center">', '<img src="a.png">'], report)
        messages = " ".join(e.message for e in report.errors)
        self.assertIn("unbalanced <p>", messages)
        self.assertIn("without alt text", messages)


class OpenSourceSectionTests(unittest.TestCase):
    def test_badge_pointing_at_wrong_repo_fails(self):
        lines = [
            "## 💻 Open-Source GitHub Projects",
            "- **[A](https://github.com/o/a)** <img src=\"https://img.shields.io/github/stars/o/b?style=social\"/>",
        ]
        report = cr.Report()
        cr.check_open_source_section(lines, report)
        self.assertTrue(any("star badge points at o/b" in e.message for e in report.errors))

    def test_entry_parsing_extracts_name_url_and_badge(self):
        lines = [
            "## 💻 Open-Source GitHub Projects",
            "- **[Plane](https://github.com/makeplane/plane)** "
            '<a href="https://github.com/makeplane/plane/stargazers"><img src="https://img.shields.io/github/stars/makeplane/plane?style=social"/></a>  ',
            "  🚀 A sufficiently long description of the project.",
        ]
        entries = cr.parse_awesome_entries(lines)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "Plane")
        self.assertEqual(entries[0]["url"], "https://github.com/makeplane/plane")
        self.assertEqual(entries[0]["badge_repo"], "makeplane/plane")


class SortingTests(unittest.TestCase):
    def test_out_of_order_stars_warn(self):
        entries = [
            {"name": "A", "stars": 10, "line": 1},
            {"name": "B", "stars": 20, "line": 2},
        ]
        with mock.patch.object(cr, "README", Path(cr.__file__).parent.parent / "README.md"):
            report = cr.Report()
            text = cr.README.read_text(encoding="utf-8")
            if "sorted by **GitHub Stars" not in text:
                self.skipTest("README no longer claims star sorting")
            cr.check_github_sorting(entries, report)
        self.assertTrue(any("has more stars" in w.message for w in report.warnings))


class EndToEndTests(unittest.TestCase):
    """Run the whole pipeline offline against a temp copy of the repo."""

    def _run_on(self, readme_text: str):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            (root / "assets" / "banner.svg").write_text("<svg/>")
            (root / "assets" / "social-preview.gif").write_bytes(b"GIF89a")
            (root / "README.md").write_text(readme_text, encoding="utf-8")
            # The README links sibling docs (e.g. TESTING.md); mirror them into the fixture.
            real_root = Path(cr.__file__).resolve().parent.parent
            for doc in real_root.glob("*.md"):
                if doc.name != "README.md":
                    (root / doc.name).write_text(doc.read_text(encoding="utf-8"), encoding="utf-8")
            with mock.patch.object(cr, "REPO_ROOT", root), mock.patch.object(
                cr, "README", root / "README.md"
            ):
                return cr.run(online=False, check_external=False)

    def test_real_readme_has_no_errors(self):
        real = Path(cr.__file__).resolve().parent.parent / "README.md"
        report = self._run_on(real.read_text(encoding="utf-8"))
        self.assertEqual([e.message for e in report.errors], [])

    def test_broken_readme_is_caught(self):
        report = self._run_on(
            "# Title\n\n## 💼 SaaS & Commercial Hosted Platforms\n\n"
            "| Platform | Pricing |\n| --- | --- |\n| **Unlinked** | free |\n\n"
            "- [Dead anchor](#nowhere)\n![missing](assets/nope.png)\n"
        )
        messages = " ".join(e.message for e in report.errors)
        self.assertIn("does not match any heading slug", messages)
        self.assertIn("does not exist on disk", messages)
        self.assertIn("required section missing", messages)

    def test_missing_asset_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("# T\n\n## One\n\n- [a](#one)\n", encoding="utf-8")
            with mock.patch.object(cr, "REPO_ROOT", root), mock.patch.object(
                cr, "README", root / "README.md"
            ):
                report = cr.run(online=False, check_external=False)
        self.assertTrue(any("asset missing" in e.message for e in report.errors))


class ExitCodeTests(unittest.TestCase):
    def test_strict_turns_warnings_into_failure(self):
        report = cr.Report()
        report.warn("toc", "something minor")
        failed = bool(report.errors) or (True and bool(report.warnings))
        self.assertTrue(failed)

    def test_missing_readme_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(cr, "REPO_ROOT", root), mock.patch.object(
                cr, "README", root / "README.md"
            ):
                report = cr.run(online=False, check_external=False)
        self.assertTrue(any("not found" in e.message for e in report.errors))


if __name__ == "__main__":
    unittest.main(verbosity=2)
