"""§17's forbidden vocabulary. No in-scope files exist yet, so these plant files in a temp tree: the
check on the real repo proves nothing until frontend/ and the template and prompt paths exist."""

from pathlib import Path, PurePosixPath

import pytest

from tools.banned_phrases import PHRASES, REPO_ROOT, hits, in_scope


def _plant(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    "path",
    [
        "frontend/app/premiums/page.tsx",
        "frontend/components/explainer.tsx",
        "backend/app/ai/prompts.py",
        "backend/app/ai/context_builder.py",
        "backend/app/templates/brief.md",
        "backend/app/services/brief_templates.py",
        "backend/app/services/prompt_text.py",
    ],
)
def test_ui_text_templates_and_prompts_are_in_scope(path: str) -> None:
    assert in_scope(PurePosixPath(path))


@pytest.mark.parametrize(
    "path",
    [
        "CLAUDE.md",
        "docs/SPEC-DEFECTS.md",
        "docs/architecture.md",
        "backend/tests/test_banned_phrases.py",
        "backend/tools/banned_phrases.py",
        "backend/app/services/health.py",
        "frontend/node_modules/pkg/index.js",
        "frontend/.next/static/chunk.js",
        "frontend/package-lock.json",
    ],
)
def test_rule_text_and_generated_files_are_out_of_scope(path: str) -> None:
    assert not in_scope(PurePosixPath(path))


@pytest.mark.parametrize("phrase", PHRASES)
def test_each_phrase_fires(tmp_path: Path, phrase: str) -> None:
    _plant(tmp_path, "frontend/app/page.tsx", f"<p>{phrase}</p>")
    assert hits(tmp_path) == (1, [f"frontend/app/page.tsx:1: {phrase}"])


def test_match_ignores_case(tmp_path: Path) -> None:
    _plant(tmp_path, "backend/app/ai/prompts.py", 'PROMPT = "Free Money"')
    assert len(hits(tmp_path)[1]) == 1


def test_match_survives_a_line_wrap(tmp_path: Path) -> None:
    _plant(tmp_path, "frontend/app/page.tsx", "<p>\n  an arbitrage\n  opportunity\n</p>")
    assert hits(tmp_path)[1] == ["frontend/app/page.tsx:2: arbitrage opportunity"]


def test_negated_phrase_still_fails(tmp_path: Path) -> None:
    # Literal by design: recorded as known future friction for M5.5 in docs/BUILD-LOG.md.
    _plant(tmp_path, "frontend/app/page.tsx", "Returns are not guaranteed.")
    assert len(hits(tmp_path)[1]) == 1


def test_phrase_in_a_doc_does_not_fire(tmp_path: Path) -> None:
    _plant(tmp_path, "CLAUDE.md", "Never emit guaranteed.")
    _plant(tmp_path, "docs/rules.md", "Never emit free money.")
    assert hits(tmp_path) == (0, [])


def test_non_utf8_file_does_not_crash_the_scan(tmp_path: Path) -> None:
    path = tmp_path / "frontend/public/logo.bin"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe\x00guaranteed")
    assert len(hits(tmp_path)[1]) == 1


def test_real_repo_is_clean() -> None:
    assert hits(REPO_ROOT)[1] == []
