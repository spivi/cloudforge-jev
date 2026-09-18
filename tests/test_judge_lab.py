"""Unit tests for _path_hops, _state and _verdict. Offline: no Jev call."""

from __future__ import annotations

from pathlib import Path

import pytest

import judge_lab
from judge_lab import _path_hops, _state, _verdict

# ---------------------------------------------------------------------------
# _path_hops
# ---------------------------------------------------------------------------


def test_path_hops_identity_chain():
    nodes = [
        {"id": "n1", "type": "PublicEndpoint", "name": "public-endpoint"},
        {"id": "n2", "type": "IAMRole", "name": "DeployRole"},
        {"id": "n3", "type": "S3Bucket", "name": "customer-exports"},
    ]
    hops = _path_hops(nodes, ["n1", "n2", "n3"], [])
    assert hops == {
        "entry": "public-endpoint",
        "hop": "DeployRole",
        "hop_kind": "identity",
        "sink": "customer-exports",
        "sink_kind": "data",
        "chain": ["public-endpoint", "DeployRole", "customer-exports"],
    }


def test_path_hops_resource_with_critical_finding():
    nodes = [
        {"id": "n1", "type": "PublicEndpoint", "name": "public-endpoint"},
        {"id": "n2", "type": "SqsQueue", "name": "orders-queue"},
        {"id": "n3", "type": "S3Bucket", "name": "customer-exports"},
    ]
    findings = [
        {
            "severity": "critical",
            "resource_ids": ["n2"],
            "ground_truth": "queue policy allows a wildcard principal",
        }
    ]
    hops = _path_hops(nodes, ["n1", "n2", "n3"], findings)
    assert hops["hop"] == "orders-queue: queue policy allows a wildcard principal"
    assert hops["hop_kind"] == "resource"


def test_path_hops_owning_account_entry():
    nodes = [
        {"id": "n1", "type": "Account", "name": "prod-account-1"},
        {"id": "n2", "type": "S3Bucket", "name": "customer-exports"},
    ]
    hops = _path_hops(nodes, ["n1", "n2"], [])
    assert hops["entry"] == "anyone on the internet, unauthenticated"
    assert hops["sink"] == "customer-exports"


def test_path_hops_external_account_entry():
    nodes = [
        {"id": "n1", "type": "Account", "name": "acct-999"},
        {"id": "n2", "type": "S3Bucket", "name": "customer-exports"},
    ]
    hops = _path_hops(nodes, ["n1", "n2"], [])
    assert hops["entry"] == "the external account acct-999"


def test_path_hops_single_node():
    nodes = [{"id": "n1", "type": "IAMRole", "name": "DeployRole"}]
    hops = _path_hops(nodes, ["n1"], [])
    assert hops == {
        "entry": "DeployRole",
        "hop": "",
        "hop_kind": "identity",
        "sink": "DeployRole",
        "sink_kind": "data",
        "chain": ["DeployRole"],
    }


def test_path_hops_missing_ids_are_skipped():
    nodes = [
        {"id": "n1", "type": "PublicEndpoint", "name": "public-endpoint"},
        {"id": "n2", "type": "IAMRole", "name": "DeployRole"},
        {"id": "n3", "type": "S3Bucket", "name": "customer-exports"},
    ]
    with_missing = _path_hops(nodes, ["n1", "does-not-exist", "n2", "n3"], [])
    without_missing = _path_hops(nodes, ["n1", "n2", "n3"], [])
    assert with_missing == without_missing


def test_path_hops_findings_none():
    nodes = [
        {"id": "n1", "type": "PublicEndpoint", "name": "public-endpoint"},
        {"id": "n2", "type": "SqsQueue", "name": "orders-queue"},
        {"id": "n3", "type": "S3Bucket", "name": "customer-exports"},
    ]
    hops = _path_hops(nodes, ["n1", "n2", "n3"], None)
    assert hops["hop"] == "orders-queue"
    assert hops["hop_kind"] == "resource"


def test_path_hops_hop_equal_target_collapses_to_resource_question():
    """A two-node path (external account into the trusted role, developer role into the
    admin role): the hop and the target are the same node, so the hop question is not
    the identity again, it is the misconfiguration that opens the access."""
    nodes = [
        {"id": "n1", "type": "Account", "name": "acct-external"},
        {"id": "n2", "type": "IAMRole", "name": "role-shared"},
    ]
    findings = [
        {
            "severity": "critical",
            "resource_ids": ["n1", "n2"],
            "ground_truth": "An external dummy account is trusted into a role that can read data.",
        }
    ]
    hops = _path_hops(nodes, ["n1", "n2"], findings, sink_kind="role", target="n2", hop="n2")
    assert hops["hop_kind"] == "resource"
    assert hops["hop"] == (
        "role-shared: An external dummy account is trusted into a role that can read data."
    )
    assert hops["sink"] == "role-shared"
    assert hops["sink_kind"] == "role"
    assert hops["chain"] == ["acct-external", "role-shared"]


def test_path_hops_prefers_key_hop_and_target_over_type_fallback():
    """A grade key's ``hop``/``target`` win over the type-aware derivation, even when a
    type-aware reader would have picked a different middle node."""
    nodes = [
        {"id": "n1", "type": "PublicEndpoint", "name": "public-endpoint"},
        {"id": "n2", "type": "IAMRole", "name": "DecoyRole"},
        {"id": "n3", "type": "SqsQueue", "name": "orders-queue"},
        {"id": "n4", "type": "S3Bucket", "name": "customer-exports"},
    ]
    hops = _path_hops(nodes, ["n1", "n2", "n3", "n4"], [], sink_kind="data", target="n4", hop="n3")
    assert hops["hop"] == "orders-queue"
    assert hops["hop_kind"] == "resource"
    assert hops["sink"] == "customer-exports"
    assert hops["sink_kind"] == "data"


# ---------------------------------------------------------------------------
# _state
# ---------------------------------------------------------------------------


def test_state_ground_truth_has_only_the_expected_keys(lab_pack: Path):
    state = _state(lab_pack, "some writeup")
    assert set(state["ground_truth"].keys()) == {
        "entry_name",
        "hop_name",
        "hop_kind",
        "sink_name",
        "sink_kind",
        "chain",
        "explanation",
    }


def test_state_rationale_is_stripped(lab_pack: Path):
    state = _state(lab_pack, "  the writeup with padding  \n")
    assert state["student"]["rationale"] == "the writeup with padding"
    assert set(state["ground_truth"].keys()) == {
        "entry_name",
        "hop_name",
        "hop_kind",
        "sink_name",
        "sink_kind",
        "chain",
        "explanation",
    }


# ---------------------------------------------------------------------------
# _verdict
# ---------------------------------------------------------------------------


class _FakeNoul:
    def __init__(self, value: float) -> None:
        self.noul = value


class _FakeScore:
    def __init__(self, value: float) -> None:
        self.score = value


class _FakeResponse:
    def __init__(self, entry: float, hop: float, sink: float, completeness: float) -> None:
        self.nouls = {
            "names_entry": _FakeNoul(entry),
            "names_identity_hop": _FakeNoul(hop),
            "names_sink": _FakeNoul(sink),
        }
        self.scores = {"depth": _FakeScore(completeness)}


def test_verdict_match_sample():
    verdict = _verdict(_FakeResponse(0.94, 0.86, 0.82, 1.59), max_level=3)
    assert verdict["semantic_hit"] is True
    assert verdict["needs_review"] is False
    assert verdict["depth_label"] == (
        "Names the entry, the hop that grants the access, and what is reached"
    )


def test_verdict_miss_sample():
    verdict = _verdict(_FakeResponse(0.05, 0.02, 0.06, 0.01), max_level=3)
    assert verdict["semantic_hit"] is False
    assert verdict["needs_review"] is False
    assert verdict["depth_label"] == "Names a different risk, or none of the critical hops"


def test_verdict_borderline_case_flags_review():
    verdict = _verdict(_FakeResponse(0.9, 0.5, 0.9, 1.0), max_level=3)
    assert verdict["semantic_hit"] is False
    assert verdict["needs_review"] is True


def test_verdict_exact_threshold_is_a_hit():
    verdict = _verdict(_FakeResponse(0.7, 0.7, 0.7, 2.0), max_level=3)
    assert verdict["semantic_hit"] is True
    assert verdict["needs_review"] is False


@pytest.mark.parametrize("boundary", [0.4, 0.6])
def test_verdict_review_band_is_strict_inequality(boundary: float):
    verdict = _verdict(_FakeResponse(0.9, boundary, 0.9, 1.0), max_level=3)
    assert verdict["needs_review"] is False


@pytest.mark.parametrize(
    "score,label_index",
    [
        (0.49, 0),
        (0.5, 0),
        (1.49, 1),
        (1.5, 2),
        (2.49, 2),
        (2.5, 2),
        (3.49, 3),
        (3.5, 3),
        (4.49, 3),
    ],
)
def test_verdict_completeness_rounding(score: float, label_index: int):
    labels = (
        "Names a different risk, or none of the critical hops",
        "Names the sink or the entry but not the connecting hop",
        "Names the entry, the hop that grants the access, and what is reached",
        "Also walks the chain between them, in order, with what makes each hop possible",
    )
    verdict = _verdict(_FakeResponse(0.1, 0.1, 0.1, score), max_level=3)
    assert verdict["depth_label"] == labels[label_index]
    assert verdict["depth_level"] == label_index


def test_verdict_completeness_clamps_above_two():
    verdict = _verdict(_FakeResponse(0.1, 0.1, 0.1, 6.0), max_level=3)
    assert verdict["depth_label"] == (
        "Also walks the chain between them, in order, with what makes each hop possible"
    )
    assert verdict["depth_level"] == 3
    assert verdict["depth_raw"] == 6.0


def test_verdict_short_path_caps_at_depth_two():
    """A labeled path of three nodes or fewer has no chain to walk beyond the hop
    already required at depth 2, so max_level caps it there even on a high raw score."""
    verdict = _verdict(_FakeResponse(0.98, 0.95, 0.96, 3.5), max_level=2)
    assert verdict["depth_level"] == 2
    assert verdict["depth_max"] == 2
    assert verdict["depth_raw"] == 3.5
    assert verdict["depth_label"] == (
        "Names the entry, the hop that grants the access, and what is reached"
    )


def test_sink_question_wording_follows_the_sink_kind() -> None:
    data = judge_lab._questions("identity", "data")["names_sink"].instructions
    role = judge_lab._questions("identity", "role")["names_sink"].instructions
    assert "sensitive data sink" in data
    assert "what the attacker reaches" in role


def test_chain_is_kept_for_the_cap_but_not_sent_to_jev(monkeypatch, lab_pack) -> None:  # type: ignore[no-untyped-def]
    sent: dict[str, object] = {}

    class _Client:
        def __init__(self, **_: object) -> None: ...
        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_: object) -> None: ...
        def system_one(self, state: dict[str, object], _questions: object) -> object:
            sent.update(state)
            raise RuntimeError("stop here")

    monkeypatch.setattr(judge_lab, "TypeSafeClient", _Client)
    monkeypatch.setenv("TYPESAFE_API_KEY", "x")
    monkeypatch.setattr(
        judge_lab.sys,
        "argv",
        ["judge_lab.py", "--lab", str(lab_pack), "--rationale", str(lab_pack / "r.txt")],
    )
    (lab_pack / "r.txt").write_text("a paragraph")
    try:
        judge_lab.main()
    except RuntimeError:
        pass
    assert "chain" not in sent["ground_truth"]  # type: ignore[operator]
