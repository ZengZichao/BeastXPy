"""Documentation contract tests.

The docs are a deliverable, so their invariants are tested like any other
feature: both languages exist and mirror each other, every relative link
resolves, every command shown actually exists, and no iteration-history token
survives in a reader-facing file. English is the authoritative text; the
Chinese page of every pair is corrected to match it.
"""

from __future__ import annotations

import argparse
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

# Reader-facing documents outside docs/en and docs/zh. Each has its Chinese
# version as a .zh-CN sibling in the same directory, so the directory's default
# rendering stays English.
SIBLING_PAIRS = [
    ("CONTRACT.md", "CONTRACT.zh-CN.md"),
    ("CONTRIBUTING.md", "CONTRIBUTING.zh-CN.md"),
    ("deploy/README.md", "deploy/README.zh-CN.md"),
    ("deploy/beagle/README.md", "deploy/beagle/README.zh-CN.md"),
    ("examples/empirical/README.md", "examples/empirical/README.zh-CN.md"),
    ("tests/golden/beauti_reference/README.md", "tests/golden/beauti_reference/README.zh-CN.md"),
]

SWITCH = re.compile(r"\[English\]\([^)]*\) \| \[中文\]\([^)]*\)")


def _md_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.md"))


def _doc_pairs() -> list[tuple[Path, Path]]:
    return [(path, ZH / path.name) for path in _md_files(EN)]


def _bilingual_pairs() -> list[tuple[Path, Path]]:
    """Every (english, chinese) document pair the reader is meant to see."""

    pairs = _doc_pairs()
    pairs.append((README_EN, README_ZH))
    pairs += [(ROOT / en, ROOT / zh) for en, zh in SIBLING_PAIRS]
    return pairs


def _switch_line_pairs() -> list[tuple[Path, Path]]:
    """Pairs headed by the standard switch line.

    The READMEs navigate differently: their Documentation line points at the
    docs index of each language, English first, rather than at each other's
    heading.
    """

    return _doc_pairs() + [(ROOT / en, ROOT / zh) for en, zh in SIBLING_PAIRS]


def _reader_facing() -> list[Path]:
    return [path for pair in _bilingual_pairs() for path in pair]


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

    def test_sibling_documents_exist_in_both_languages(self):
        for en_name, zh_name in SIBLING_PAIRS:
            assert (ROOT / en_name).is_file(), en_name
            assert (ROOT / zh_name).is_file(), zh_name


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

    def test_sibling_language_switch_links_exist(self):
        for en_name, zh_name in SIBLING_PAIRS:
            en_text = (ROOT / en_name).read_text(encoding="utf-8")
            zh_text = (ROOT / zh_name).read_text(encoding="utf-8")
            assert f"]({Path(zh_name).name})" in en_text, en_name
            assert f"]({Path(en_name).name})" in zh_text, zh_name

    def test_english_is_listed_first_in_every_switch_line(self):
        missing = [
            str(path.relative_to(ROOT))
            for pair in _switch_line_pairs()
            for path in pair
            if not SWITCH.search(path.read_text(encoding="utf-8"))
        ]
        assert not missing, f"no 'English | 中文' switch line: {missing}"

    def test_sibling_links_resolve(self):
        for en_name, zh_name in SIBLING_PAIRS:
            for name in (en_name, zh_name):
                broken = self._check([ROOT / name], ROOT)
                assert not broken, "\n".join(broken)


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
        for path in _reader_facing():
            hits = BANNED.findall(path.read_text(encoding="utf-8"))
            if hits:
                offenders.append(f"{path.relative_to(ROOT)}: {hits}")
        assert not offenders, "\n".join(offenders)


class TestVersion:
    def test_readmes_state_the_current_version(self):
        from beastxpy import __version__

        assert __version__ in README_EN.read_text(encoding="utf-8")
        assert __version__ in README_ZH.read_text(encoding="utf-8")

    def test_no_other_beastxpy_version(self):
        from beastxpy import __version__

        pattern = re.compile(r"BeastXPy[ ]+(\d+\.\d+\.\d+)")
        for path in _reader_facing():
            for match in pattern.finditer(path.read_text(encoding="utf-8")):
                assert match.group(1) == __version__, f"{path.relative_to(ROOT)}: {match.group(1)}"


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
        for en_path, zh_path in _bilingual_pairs():
            if blocks(en_path) != blocks(zh_path):
                mismatched.append(str(en_path.relative_to(ROOT)))
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
        for en_path, zh_path in _bilingual_pairs():
            label = str(en_path.relative_to(ROOT))
            en_rows = self._table_rows(en_path.read_text(encoding="utf-8"))
            zh_rows = self._table_rows(zh_path.read_text(encoding="utf-8"))
            if len(en_rows) != len(zh_rows):
                mismatched.append(f"{label}: row count {len(en_rows)} != {len(zh_rows)}")
                continue
            for index, (en_row, zh_row) in enumerate(zip(en_rows, zh_rows), start=1):
                en_tokens = [code.findall(cell) for cell in en_row]
                zh_tokens = [code.findall(cell) for cell in zh_row]
                if en_tokens != zh_tokens:
                    mismatched.append(f"{label}: table row {index} code cells differ")
        assert not mismatched, "\n".join(mismatched)

    def test_heading_counts_match_between_languages(self):
        """Translation may not add or drop a section on either side."""

        def headings(path: Path) -> list[str]:
            return re.findall(r"^#{1,6} ", path.read_text(encoding="utf-8"), re.M)

        mismatched = [
            str(en_path.relative_to(ROOT))
            for en_path, zh_path in _bilingual_pairs()
            if len(headings(en_path)) != len(headings(zh_path))
        ]
        assert not mismatched, f"heading counts differ: {mismatched}"


class TestCliReferenceMatchesParser:
    """`docs/cli.md` says its tables come from the argument parser; hold it to that.

    A flag that is not registered, a required flag presented as optional, or a
    value set that differs from the parser's own is a documentation defect: the
    reader copies the line and the CLI refuses it.
    """

    @staticmethod
    def _subparsers() -> dict:
        for action in _parser()._actions:
            if isinstance(action, argparse._SubParsersAction):
                return action.choices
        raise AssertionError("no subcommands registered")

    @staticmethod
    def _sections(text: str) -> dict:
        chunks = re.split(r"^### `beastxpy ", text, flags=re.M)[1:]
        return {chunk.split("`", 1)[0]: chunk for chunk in chunks}

    @staticmethod
    def _options(subparser) -> list:
        """(labels, value tokens, required, siblings) for every registered option."""

        grouped = {}
        for group in subparser._mutually_exclusive_groups:
            if not group.required:
                continue
            labels = [frozenset(action.option_strings) for action in group._group_actions]
            for action, own in zip(group._group_actions, labels):
                grouped[id(action)] = {label for other in labels if other != own for label in other}
        out = []
        for action in subparser._actions:
            if action.dest == "help" or not action.option_strings:
                continue
            if action.nargs == 0:
                values = {"flag"}
            else:
                raw = action.metavar
                if raw is None and action.choices:
                    raw = ",".join(action.choices)
                values = set(re.split(r"[|,]", raw.strip("{}"))) if raw else set()
            out.append(
                (
                    set(action.option_strings),
                    values,
                    bool(action.required),
                    grouped.get(id(action), set()) - set(action.option_strings),
                )
            )
        return out

    @staticmethod
    def _row(cell: str) -> set:
        labels = set()
        for token in re.findall(r"`([^`]+)`", cell):
            labels.update(part.strip().strip("{}") for part in token.split(","))
        return {label for label in labels if label}

    @pytest.mark.parametrize("path", [EN / "cli.md", ZH / "cli.md"])
    def test_argument_tables_match_the_parser(self, path):
        subs = self._subparsers()
        failures = []
        for name, body in self._sections(path.read_text(encoding="utf-8")).items():
            table = re.search(r"\*\*(?:Arguments|参数)\*\*\n\n(.*?)(?:\n\n\*\*|\Z)", body, re.S)
            if not table:
                failures.append(f"{name}: no Arguments table")
                continue
            options = self._options(subs[name])
            positionals = {
                (a.metavar or a.dest.upper()).strip("[]")
                for a in subs[name]._actions
                if a.dest != "help" and not a.option_strings
            }
            seen = set()
            for line in table.group(1).splitlines():
                if not line.strip().startswith("|") or line.strip().startswith("|---"):
                    continue
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) < 3 or cells[0].strip("`") in {"Argument", "参数"}:
                    continue
                labels, required = self._row(cells[0]), cells[2]
                if labels & positionals:  # positional argument row
                    continue
                match = [o for o in options if o[0] == labels]
                if not match:
                    failures.append(f"{name}: {sorted(labels)} is not a registered option")
                    continue
                seen.add(frozenset(labels))
                _, values, is_required, siblings = match[0]
                stated_yes = bool(re.match(r"\s*(?:yes|是)", required))
                if stated_yes != (is_required or bool(siblings)):
                    failures.append(
                        f"{name}: {sorted(labels)} required={is_required} but table says "
                        f"{required!r}"
                    )
                elif siblings and not any(sibling in required for sibling in siblings):
                    failures.append(
                        f"{name}: {sorted(labels)} is one of a required choice but the table "
                        f"does not name {sorted(siblings)}"
                    )
                if values and self._row(cells[1]) != values:
                    failures.append(
                        f"{name}: {sorted(labels)} value {sorted(self._row(cells[1]))} "
                        f"!= parser {sorted(values)}"
                    )
            for labels, _values, _req, _sib in options:
                if frozenset(labels) not in seen:
                    failures.append(f"{name}: {sorted(labels)} missing from the table")
        assert not failures, "\n".join(failures)

    @pytest.mark.parametrize("path", [EN / "cli.md", ZH / "cli.md"])
    def test_required_options_are_not_bracketed_in_the_synopsis(self, path):
        subs = self._subparsers()
        failures = []
        for name, body in self._sections(path.read_text(encoding="utf-8")).items():
            synopsis = re.search(r"\*\*(?:Synopsis|用法)\*\*\n\n```\n(.*?)```", body, re.S)
            if not synopsis:
                failures.append(f"{name}: no synopsis block")
                continue
            text = re.sub(r"\s+", " ", synopsis.group(1))
            bare = re.sub(r"\[[^\]]*\]", "", text)
            for labels, _values, required, _siblings in self._options(subs[name]):
                present = any(label in text for label in labels)
                if not present:
                    failures.append(f"{name}: {sorted(labels)} absent from the synopsis")
                if required and not any(label in bare for label in labels):
                    failures.append(f"{name}: {sorted(labels)} is required but bracketed")
        assert not failures, "\n".join(failures)

    @pytest.mark.parametrize("path", [EN / "cli.md", ZH / "cli.md"])
    def test_examples_use_registered_flags(self, path):
        subs = self._subparsers()
        failures = []
        for block in FENCE.findall(path.read_text(encoding="utf-8")):
            joined = re.sub(r"\\\s*\n\s*", " ", block)
            for line in joined.splitlines():
                match = re.match(r"\s*beastxpy ([a-z][a-z-]*)\b(.*)", line)
                if not match or match.group(1) not in subs:
                    continue
                known = {
                    label
                    for action in subs[match.group(1)]._actions
                    for label in action.option_strings
                }
                for flag in re.findall(r"(?<=\s)--[a-z][a-z-]*(?![\w-])", match.group(2)):
                    if flag not in known:
                        failures.append(f"{match.group(1)}: {flag} is not registered")
        assert not failures, "\n".join(failures)


class TestNoPersonalPaths:
    def test_no_windows_drive_letters_or_home_dirs(self):
        # A drive letter only counts at the start of a path, so "file://" and
        # "$PWD:/work" are not false positives.
        pattern = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]|/Users/|/home/")
        offenders = []
        for path in _reader_facing():
            for match in pattern.finditer(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.relative_to(ROOT)}: {match.group(0)}")
        assert not offenders, "\n".join(offenders)
