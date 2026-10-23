"""Documentation contract tests.

The docs are a deliverable, so their invariants are tested like any other
feature: both languages exist and mirror each other, every relative link
resolves, every command shown actually exists, and no iteration-history token
survives in a reader-facing file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from beastxpy.main import _parser

ROOT = Path(__file__).resolve().parent.parent
EN = ROOT / "docs" / "en"
ZH = ROOT / "docs" / "zh"
README_EN = ROOT / "README.md"
README_ZH = ROOT / "README.zh-CN.md"

EXPECTED_PAGES = {
    "index",
    "installation",
    "quickstart",
    "configuration",
    "cli",
    "validation",
    "native-beagle",
    "pipelines",
    "assessment",
    "reporting",
    "compare",
    "reproducibility",
    "extensions",
    "container",
    "troubleshooting",
    "api",
    "development",
    "release",
    "limitations",
    "faq",
    "references",
}

# Tokens that would reveal how the project was built rather than what it does.
BANNED = re.compile(r"(?:\bP[0-3]\b|BXP-[A-Z]|overnight|remediation|夜间|review 0\d)")

FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _md_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.md"))


def _commands() -> set[str]:
    parser = _parser()
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            return set(action.choices)
    raise AssertionError("no subcommands registered")


class TestStructure:
    def test_both_trees_have_the_expected_pages(self):
        en = {p.stem for p in _md_files(EN)}
        zh = {p.stem for p in _md_files(ZH)}
        assert en == EXPECTED_PAGES, sorted(EXPECTED_PAGES - en)
        assert zh == EXPECTED_PAGES, sorted(EXPECTED_PAGES - zh)

    def test_readmes_exist(self):
        assert README_EN.is_file()
        assert README_ZH.is_file()


class TestLinks:
    def _check(self, files: list[Path], base: Path) -> list[str]:
        broken = []
        for path in files:
            text = path.read_text(encoding="utf-8")
            for target in LINK.findall(text):
                if target.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                resolved = (path.parent / target.split("#", 1)[0]).resolve()
                if not resolved.exists():
                    broken.append(f"{path.relative_to(base)} -> {target}")
        return broken

    def test_doc_links_resolve(self):
        broken = self._check(_md_files(EN), ROOT) + self._check(_md_files(ZH), ROOT)
        assert not broken, "\n".join(broken)

    def test_readme_links_resolve(self):
        broken = []
        for readme in (README_EN, README_ZH):
            text = readme.read_text(encoding="utf-8")
            for target in LINK.findall(text):
                if target.startswith(("http://", "https://", "#")):
                    continue
                if not (ROOT / target.split("#", 1)[0]).resolve().exists():
                    broken.append(f"{readme.name} -> {target}")
        assert not broken, "\n".join(broken)

    def test_language_switch_links_exist(self):
        for lang_dir, other in ((EN, ZH), (ZH, EN)):
            for path in _md_files(lang_dir):
                text = path.read_text(encoding="utf-8")
                assert f"../{other.name}/{path.name}" in text, path.name


class TestCommandsExist:
    def test_every_documented_command_is_registered(self):
        registered = _commands()
        seen = set()
        for path in _md_files(EN):
            text = path.read_text(encoding="utf-8")
            for block in FENCE.findall(text):
                for match in re.finditer(
                    r"^\s*(?:python -m )?beastxpy ([a-z][a-z-]*)", block, re.M
                ):
                    seen.add(match.group(1))
        seen.discard("container")  # also appears as a filename word
        unknown = {name for name in seen if name not in registered}
        assert not unknown, f"documented but not registered: {sorted(unknown)}"

    def test_all_commands_are_documented(self):
        text = (EN / "cli.md").read_text(encoding="utf-8")
        # The heading form is "### `beastxpy <name>`".
        missing = [name for name in _commands() if f"### `beastxpy {name}`" not in text]
        assert not missing, sorted(missing)


class TestNoIterationTraces:
    @pytest.mark.parametrize("path", [README_EN, README_ZH])
    def test_readme_is_clean(self, path):
        hits = BANNED.findall(path.read_text(encoding="utf-8"))
        assert not hits, f"{path.name}: {hits}"

    def test_docs_are_clean(self):
        offenders = []
        for path in _md_files(EN) + _md_files(ZH):
            hits = BANNED.findall(path.read_text(encoding="utf-8"))
            if hits:
                offenders.append(f"{path.name}: {hits}")
        assert not offenders, "\n".join(offenders)


class TestVersion:
    def test_readmes_state_the_current_version(self):
        from beastxpy import __version__

        assert __version__ in README_EN.read_text(encoding="utf-8")
        assert __version__ in README_ZH.read_text(encoding="utf-8")

    def test_no_other_beastxpy_version(self):
        from beastxpy import __version__

        pattern = re.compile(r"BeastXPy[ ]+(\d+\.\d+\.\d+)")
        for path in _md_files(EN) + _md_files(ZH):
            for match in pattern.finditer(path.read_text(encoding="utf-8")):
                assert match.group(1) == __version__, f"{path.name}: {match.group(1)}"


class TestBilingualParity:
    def test_code_blocks_match_between_languages(self):
        """Commands and code are identical; only prose differs.

        The single allowed difference is the ``--lang`` value, which is en in
        the English tree and zh in the Chinese tree.
        """

        def blocks(path: Path) -> list[str]:
            text = path.read_text(encoding="utf-8")
            return [
                re.sub(r"--lang (en|zh)", "--lang <lang>", b.strip()) for b in FENCE.findall(text)
            ]

        mismatched = []
        for en_path in _md_files(EN):
            zh_path = ZH / en_path.name
            if blocks(en_path) != blocks(zh_path):
                mismatched.append(en_path.name)
        assert not mismatched, f"code blocks differ: {mismatched}"

    @staticmethod
    def _table_rows(text: str) -> list[list[str]]:
        rows: list[list[str]] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not (stripped.startswith("|") and stripped.endswith("|")):
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell):
                continue
            rows.append(cells)
        return rows

    def test_parameter_tables_match_between_languages(self):
        """Tables have the same shape and the same inline-code tokens.

        Prose may be translated, but a flag, key, default or command in a
        table cell is an interface fact and must appear in both languages.
        """
        mismatched = []
        code = re.compile(r"`([^`]+)`")
        for en_path in _md_files(EN):
            zh_path = ZH / en_path.name
            en_rows = self._table_rows(en_path.read_text(encoding="utf-8"))
            zh_rows = self._table_rows(zh_path.read_text(encoding="utf-8"))
            if len(en_rows) != len(zh_rows):
                mismatched.append(f"{en_path.name}: row count {len(en_rows)} != {len(zh_rows)}")
                continue
            for index, (en_row, zh_row) in enumerate(zip(en_rows, zh_rows), start=1):
                en_tokens = [code.findall(cell) for cell in en_row]
                zh_tokens = [code.findall(cell) for cell in zh_row]
                if en_tokens != zh_tokens:
                    mismatched.append(f"{en_path.name}: table row {index} code cells differ")
        assert not mismatched, "\n".join(mismatched)


class TestNoPersonalPaths:
    def test_no_windows_drive_letters_or_home_dirs(self):
        # A drive letter only counts at the start of a path, so "file://" and
        # "$PWD:/work" are not false positives.
        pattern = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]|/Users/|/home/")
        offenders = []
        for path in _md_files(EN) + _md_files(ZH) + [README_EN, README_ZH]:
            for match in pattern.finditer(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.name}: {match.group(0)}")
        assert not offenders, "\n".join(offenders)
