"""Unit tests for _path_hops, _state and _verdict. Offline: no Jev call."""

from __future__ import annotations

from pathlib import Path

import pytest

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
        self.scores = {"completeness": _FakeScore(completeness)}


def test_verdict_match_sample():
    verdict = _verdict(_FakeResponse(0.94, 0.86, 0.82, 1.59))
    assert verdict["semantic_hit"] is True
    assert verdict["needs_review"] is False
    assert verdict["completeness_label"] == "Names the entry, the identity hop, and the sink"


def test_verdict_miss_sample():
    verdict = _verdict(_FakeResponse(0.05, 0.02, 0.06, 0.01))
    assert verdict["semantic_hit"] is False
    assert verdict["needs_review"] is False
    assert verdict["completeness_label"] == "Names a different risk, or none of the critical hops"


def test_verdict_borderline_case_flags_review():
    verdict = _verdict(_FakeResponse(0.9, 0.5, 0.9, 1.0))
    assert verdict["semantic_hit"] is False
    assert verdict["needs_review"] is True


def test_verdict_exact_threshold_is_a_hit():
    verdict = _verdict(_FakeResponse(0.7, 0.7, 0.7, 2.0))
    assert verdict["semantic_hit"] is True
    assert verdict["needs_review"] is False


@pytest.mark.parametrize("boundary", [0.4, 0.6])
def test_verdict_review_band_is_strict_inequality(boundary: float):
    verdict = _verdict(_FakeResponse(0.9, boundary, 0.9, 1.0))
    assert verdict["needs_review"] is False


@pytest.mark.parametrize(
    "score,label_index",
    [
        (0.49, 0),
        (0.5, 0),
        (1.49, 1),
        (1.5, 2),
    ],
)
def test_verdict_completeness_rounding(score: float, label_index: int):
    labels = (
        "Names a different risk, or none of the critical hops",
        "Names the sink or the entry but not the connecting hop",
        "Names the entry, the identity hop, and the sink",
    )
    verdict = _verdict(_FakeResponse(0.1, 0.1, 0.1, score))
    assert verdict["completeness_label"] == labels[label_index]


def test_verdict_completeness_clamps_above_two():
    verdict = _verdict(_FakeResponse(0.1, 0.1, 0.1, 3.0))
    assert verdict["completeness_label"] == "Names the entry, the identity hop, and the sink"
    assert verdict["completeness"] == 3.0
