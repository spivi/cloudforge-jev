#!/usr/bin/env python3
"""Sidecar: score a student writeup with TypeSafe Jev against a cloudforge lab.

cloudforge stays hermetic. This script is not part of that product. It reads
the instructor pack that `cloudforge lab` already wrote, then asks Jev three
narrow questions. Code owns the hit threshold.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from typesafe_sdk import Noul, Score, TypeSafeClient

HIT = 0.7
UNCERTAIN_LOW = 0.4
UNCERTAIN_HIGH = 0.6
MODEL = "jev-latest"
console = Console()


def main() -> int:
    load_dotenv()
    args = _parse()
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key:
        console.print("[red]error:[/red] set TYPESAFE_API_KEY")
        return 1
    lab_dir = _ensure_lab(args)
    state = _state(lab_dir, args.rationale.read_text(encoding="utf-8"))
    with TypeSafeClient(api_key=key, model=MODEL) as client:
        hop_kind = str(state["ground_truth"]["hop_kind"])  # type: ignore[index]
        response = client.system_one(state, _questions(hop_kind))
    _print(_verdict(response))
    return 0


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Judge a free-text writeup against a cloudforge instructor pack."
    )
    parser.add_argument("--rationale", type=Path, required=True, help="Student writeup")
    parser.add_argument(
        "--lab",
        type=Path,
        help="Existing cloudforge lab directory (student/ + instructor/).",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="No --lab: write one with cloudforge instead of failing closed.",
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        default=None,
        help="If --lab is omitted, generate one with cloudforge lab.",
    )
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument(
        "--cloudforge-root",
        type=Path,
        default=Path(os.environ.get("CLOUDFORGE_ROOT", "")),
        help="Path to the cloudforge checkout. Or set CLOUDFORGE_ROOT.",
    )
    parser.add_argument("--out", type=Path, default=Path("out/demo-lab"))
    return parser.parse_args()


REQUIRED_INSTRUCTOR_FILES = (
    "graph.json",
    "ground_truth_paths.json",
    "expected_findings.json",
)


def _ensure_lab(args: argparse.Namespace) -> Path:
    if args.lab is not None:
        instructor = args.lab / "instructor"
        for name in REQUIRED_INSTRUCTOR_FILES:
            required = instructor / name
            if not required.is_file():
                console.print(f"[red]error:[/red] missing {required}")
                raise SystemExit(2)
        return args.lab
    if not args.generate:
        console.print(
            "[red]error:[/red] pass --lab <pack>, or --generate to write one with cloudforge"
        )
        raise SystemExit(2)
    root = args.cloudforge_root
    if not root or not (root / "app" / "cli.py").is_file():
        raise SystemExit("pass --cloudforge-root or set CLOUDFORGE_ROOT")
    scenario = args.scenario
    if scenario is None:
        scenario = root / "examples" / "ci_cd_iam_chain.yaml"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    out = args.out if args.out.is_absolute() else Path.cwd() / args.out
    if importlib.util.find_spec("typer") is None:
        raise SystemExit(
            "cloudforge is not installed in this interpreter; run "
            f"`pip install -e {root}` or pass --lab to an existing pack"
        )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli",
            "lab",
            str(scenario.resolve()),
            "--seed",
            str(args.seed),
            "--out",
            str(out),
        ],
        cwd=root,
        env=env,
        check=True,
    )
    return out


def _state(lab_dir: Path, rationale: str) -> dict[str, object]:
    instructor = lab_dir / "instructor"
    graph = json.loads((instructor / "graph.json").read_text())
    truth = json.loads((instructor / "ground_truth_paths.json").read_text())
    findings = json.loads((instructor / "expected_findings.json").read_text())
    primary = truth["paths"][0]
    hops = _path_hops(graph["nodes"], list(primary["nodes"]), findings["findings"])
    return {
        "student": {"rationale": rationale.strip()},
        "ground_truth": {
            "explanation": primary["explanation"],
            "entry_name": hops["entry"],
            "hop_name": hops["hop"],
            "hop_kind": hops["hop_kind"],
            "sink_name": hops["sink"],
        },
    }


IDENTITY_TYPES = frozenset(
    {
        "IAMRole",
        "IAMUser",
        "IAMGroup",
        "CICDIdentity",
        "K8sServiceAccount",
        "AzureManagedIdentity",
        "GcpServiceAccount",
        "GcpWorkloadIdentityPool",
    }
)


def _path_hops(
    nodes: list[dict[str, object]], path_ids: list[str], findings: list[dict[str, object]] | None = None
) -> dict[str, str]:
    """The three things a writeup must name, chosen by node type rather than position.

    entry: the first node; an owning account means public, unauthenticated access, an
    external account is named as such. hop: the first identity after the entry, or, when
    the path has none (public bucket, shared snapshot, wildcard queue), the misconfigured
    resource itself, described by its critical finding when the instructor pack has one
    ("SQS queue policy allows wildcard principal ..."). sink: the last node.
    """
    by_id = {str(n["id"]): n for n in nodes}
    chain = [by_id[i] for i in path_ids if i in by_id]
    if not chain:
        return {"entry": "", "hop": "", "hop_kind": "identity", "sink": ""}
    entry, sink = chain[0], chain[-1]
    entry_name = str(entry["name"])
    if entry["type"] == "Account":
        if entry_name.startswith("prod-account"):
            entry_name = "anyone on the internet, unauthenticated"
        else:
            entry_name = f"the external account {entry_name}"
    middle = chain[1:-1]
    identity = next((n for n in middle if n["type"] in IDENTITY_TYPES), None)
    if identity is not None:
        hop, kind = str(identity["name"]), "identity"
    elif middle:
        resource = middle[0]
        hop, kind = str(resource["name"]), "resource"
        for finding in findings or []:
            on_path = any(r in path_ids for r in finding.get("resource_ids", []))
            if on_path and finding.get("severity") in ("critical", "high") and finding.get("ground_truth"):
                hop = f"{resource['name']}: {finding['ground_truth']}"
                break
    else:
        hop, kind = "", "identity"
    return {"entry": entry_name, "hop": hop, "hop_kind": kind, "sink": str(sink["name"])}


def _questions(hop_kind: str) -> dict[str, Noul | Score]:
    hop_question = (
        "Does `student.rationale` describe the identity or role hop named in `ground_truth.hop_name`?"
        if hop_kind == "identity"
        else (
            "Does `student.rationale` describe the misconfiguration in "
            "`ground_truth.hop_name` as what opens the access?"
        )
    )
    return {
        "names_entry": Noul(
            instructions=(
                "Does `student.rationale` identify the same initial access "
                "as `ground_truth.entry_name`?"
            ),
        ),
        "names_identity_hop": Noul(instructions=hop_question),
        "names_sink": Noul(
            instructions=(
                "Does `student.rationale` identify the same sensitive data sink "
                "as `ground_truth.sink_name`?"
            ),
        ),
        "completeness": Score(
            instructions=(
                "How complete is `student.rationale` relative to "
                "`ground_truth.explanation`?"
            ),
            criteria=[
                "Names a different risk, or none of the critical hops",
                "Names the sink or the entry but not the connecting hop",
                "Names the entry, the identity hop, and the sink",
            ],
        ),
    }


def _verdict(response: object) -> dict[str, object]:
    nouls = response.nouls  # type: ignore[attr-defined]
    score = response.scores["completeness"]  # type: ignore[attr-defined]
    entry = float(nouls["names_entry"].noul)
    hop = float(nouls["names_identity_hop"].noul)
    sink = float(nouls["names_sink"].noul)
    labels = (
        "Names a different risk, or none of the critical hops",
        "Names the sink or the entry but not the connecting hop",
        "Names the entry, the identity hop, and the sink",
    )
    idx = min(2, max(0, round(float(score.score))))
    uncertain = any(UNCERTAIN_LOW < p < UNCERTAIN_HIGH for p in (entry, hop, sink))
    return {
        "names_entry": entry,
        "names_identity_hop": hop,
        "names_sink": sink,
        "completeness": float(score.score),
        "completeness_label": labels[idx],
        "semantic_hit": entry >= HIT and hop >= HIT and sink >= HIT,
        "needs_review": uncertain,
    }


def _print(verdict: dict[str, object]) -> None:
    table = Table(title="Jev sidecar: student writeup vs labeled chain")
    table.add_column("Signal")
    table.add_column("Value")
    table.add_row("names entry", f"{verdict['names_entry']:.3f}")
    table.add_row("names identity hop", f"{verdict['names_identity_hop']:.3f}")
    table.add_row("names sink", f"{verdict['names_sink']:.3f}")
    table.add_row(
        "completeness",
        f"{verdict['completeness']:.3f} ({verdict['completeness_label']})",
    )
    table.add_row("semantic hit", "yes" if verdict["semantic_hit"] else "no")
    table.add_row("instructor review", "yes" if verdict["needs_review"] else "no")
    console.print(table)


if __name__ == "__main__":
    raise SystemExit(main())
