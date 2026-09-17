"""main() fails closed: every exit happens before any Jev call. Offline only."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import judge_lab


class _NoNetworkClient:
    """Stands in for TypeSafeClient; construction means a test regressed."""

    def __init__(self, *args, **kwargs) -> None:
        raise AssertionError("must not construct a TypeSafeClient before the fail-closed checks")


@pytest.fixture(autouse=True)
def _no_dotenv_no_network(monkeypatch):
    # load_dotenv() would otherwise pull the repo's real .env back in.
    monkeypatch.setattr(judge_lab, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(judge_lab, "TypeSafeClient", _NoNetworkClient)


def _rationale_file(tmp_path: Path) -> Path:
    path = tmp_path / "writeup.txt"
    path.write_text("some writeup", encoding="utf-8")
    return path


def test_missing_api_key_exits_1_before_touching_the_lab(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    def _boom(args):
        raise AssertionError("must not touch the lab without an API key")

    monkeypatch.setattr(judge_lab, "_ensure_lab", _boom)
    rationale = _rationale_file(tmp_path)
    monkeypatch.setattr(sys, "argv", ["judge_lab.py", "--rationale", str(rationale)])

    assert judge_lab.main() == 1


def test_lab_omitted_without_generate_exits_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    rationale = _rationale_file(tmp_path)
    monkeypatch.setattr(sys, "argv", ["judge_lab.py", "--rationale", str(rationale)])

    with pytest.raises(SystemExit) as excinfo:
        judge_lab.main()

    assert excinfo.value.code == 2
    out = capsys.readouterr().out
    assert "pass --lab <pack>, or --generate to write one with cloudforge" in out


def test_lab_missing_instructor_files_exits_2_naming_the_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    rationale = _rationale_file(tmp_path)
    missing_lab = tmp_path / "nonexistent-lab"
    monkeypatch.setattr(
        sys,
        "argv",
        ["judge_lab.py", "--lab", str(missing_lab), "--rationale", str(rationale)],
    )

    with pytest.raises(SystemExit) as excinfo:
        judge_lab.main()

    assert excinfo.value.code == 2
    out = capsys.readouterr().out.replace("\n", "")
    assert "graph.json" in out
    assert str(missing_lab).replace("\n", "") in out
