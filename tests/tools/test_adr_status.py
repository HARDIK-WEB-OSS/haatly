import re
import shutil
import subprocess
from pathlib import Path

import pytest

ADR_DIRECTORY = Path(__file__).resolve().parents[2] / "docs" / "adr"
SHA = r"[0-9a-f]{7,40}"
STATUS_LINE = re.compile(
    rf"^- Status: (?:Proposed|Accepted|Implemented \((?P<shas>{SHA}(?:, {SHA})*)\))(?:[ .;,].*)?$"
)
ADRS = sorted(ADR_DIRECTORY.glob("[0-9][0-9][0-9][0-9]-*.md"))


def status_of(path: Path) -> re.Match[str]:
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("- Status:")
    ]
    assert len(lines) == 1, f"{path.name} needs exactly one status line"
    match = STATUS_LINE.match(lines[0])
    assert match is not None, f"{path.name} has an unknown status: {lines[0]}"
    return match


def commit_exists(sha: str) -> bool:
    git = shutil.which("git")
    assert git is not None
    probe = [git, "cat-file", "-e", f"{sha}^{{commit}}"]
    return subprocess.run(probe, cwd=ADR_DIRECTORY, check=False).returncode == 0  # noqa: S603


def test_there_are_adrs_to_check() -> None:
    assert len(ADRS) >= 16


@pytest.mark.parametrize("path", ADRS, ids=lambda path: path.name[:4])
def test_every_adr_states_proposed_accepted_or_implemented(path: Path) -> None:
    status_of(path)


@pytest.mark.parametrize("path", ADRS, ids=lambda path: path.name[:4])
def test_implemented_adrs_name_commits_that_exist(path: Path) -> None:
    shas = status_of(path).group("shas")
    for sha in [] if shas is None else shas.split(", "):
        assert commit_exists(sha), f"{path.name} names {sha}, which is not a commit"


def test_the_status_pattern_rejects_vague_claims() -> None:
    for line in (
        "- Status: Implemented",
        "- Status: Implemented (soon)",
        "- Status: Accepted-ish",
        "- Status: Done",
    ):
        assert STATUS_LINE.match(line) is None
    assert STATUS_LINE.match("- Status: Implemented (0719c34, 5cd6d01). Notes follow.")
