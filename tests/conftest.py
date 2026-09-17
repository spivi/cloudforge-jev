"""Shared fixtures: a tiny cloudforge instructor pack, built in tmp_path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def lab_pack(tmp_path: Path) -> Path:
    """A minimal cloudforge lab: one path (entry, identity hop, sink) and one
    critical finding whose resource_ids land on the sink.
    """
    lab_dir = tmp_path / "demo-lab"
    instructor = lab_dir / "instructor"
    instructor.mkdir(parents=True)

    graph = {
        "nodes": [
            {"id": "entry-1", "type": "Account", "name": "prod-account-1"},
            {"id": "hop-1", "type": "IAMRole", "name": "DeployRole"},
            {"id": "sink-1", "type": "S3Bucket", "name": "customer-exports"},
        ]
    }
    ground_truth_paths = {
        "paths": [
            {
                "nodes": ["entry-1", "hop-1", "sink-1"],
                "explanation": "DeployRole reaches the customer-exports bucket.",
            }
        ]
    }
    expected_findings = {
        "findings": [
            {
                "severity": "critical",
                "resource_ids": ["sink-1"],
                "ground_truth": "bucket policy allows DeployRole to read all objects",
            }
        ]
    }

    (instructor / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    (instructor / "ground_truth_paths.json").write_text(
        json.dumps(ground_truth_paths), encoding="utf-8"
    )
    (instructor / "expected_findings.json").write_text(
        json.dumps(expected_findings), encoding="utf-8"
    )
    return lab_dir
