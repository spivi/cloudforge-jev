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
DEPTH_LABELS = (
    "Names a different risk, or none of the critical hops",
    "Names the sink or the entry but not the connecting hop",
    "Names the entry, the hop that grants the access, and what is reached",
    "Also walks the chain between them, in order, with what makes each hop possible",
)
DEPTH_SHORT_PATH_MAX = 2  # a labeled path of 3 nodes or fewer has no chain to walk
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
    chain = state["ground_truth"]["chain"]  # type: ignore[index]
    max_level = DEPTH_SHORT_PATH_MAX if len(chain) <= 3 else len(DEPTH_LABELS) - 1
    _print(_verdict(response, max_level))
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
    hops = _path_hops(
        graph["nodes"],
        list(primary["nodes"]),
        findings["findings"],
        sink_kind=primary.get("sink_kind"),
        target=primary.get("target"),
        hop=primary.get("hop"),
    )
    return {
        "student": {"rationale": rationale.strip()},
        "ground_truth": {
            "explanation": primary["explanation"],
            "entry_name": hops["entry"],
            "hop_name": hops["hop"],
            "hop_kind": hops["hop_kind"],
            "sink_name": hops["sink"],
            "sink_kind": hops["sink_kind"],
            "chain": hops["chain"],
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
    nodes: list[dict[str, object]],
    path_ids: list[str],
    findings: list[dict[str, object]] | None = None,
    sink_kind: str | None = None,
    target: str | None = None,
    hop: str | None = None,
) -> dict[str, object]:
    """The things a writeup must name, chosen by node type rather than position.

    entry: the first node; an owning account means public, unauthenticated access, an
    external account is named as such. hop: the grade key's ``hop`` when given, else the
    first identity after the entry, or, when the path has none (public bucket, shared
    snapshot, wildcard queue), the misconfigured resource itself, described by its
    critical finding when the instructor pack has one ("SQS queue policy allows wildcard
    principal ..."). sink/target: the grade key's ``target`` when given, else the last
    node. When the hop lands on the same node as the target (a two-node path: an external
    account into the trusted role, a developer role into the admin role), asking about the
    hop would repeat the sink question, so it collapses into the resource question instead:
    the misconfiguration that opens the access. sink_kind labels the target's kind (data,
    secret, key, role, image, queue, snapshot, database, vault) and defaults to "data" for
    a pack written before the field existed. chain is every path node's name, in order, for
    the depth ladder's "walks the intermediate hops in order" level.
    """
    by_id = {str(n["id"]): n for n in nodes}
    chain_ids = [i for i in path_ids if i in by_id]
    if not chain_ids:
        return {
            "entry": "",
            "hop": "",
            "hop_kind": "identity",
            "sink": "",
            "sink_kind": sink_kind or "data",
            "chain": [],
        }
    entry = by_id[chain_ids[0]]
    sink_id = target if target and target in by_id else chain_ids[-1]
    sink = by_id[sink_id]
    entry_name = str(entry["name"])
    if entry["type"] == "Account":
        if entry_name.startswith("prod-account"):
            entry_name = "anyone on the internet, unauthenticated"
        else:
            entry_name = f"the external account {entry_name}"
    hop_id: str | None = None
    if len(chain_ids) >= 2:
        hop_id = hop if hop and hop in by_id else None
        if hop_id is None:
            middle_ids = chain_ids[1:-1]
            identity_id = next(
                (i for i in middle_ids if by_id[i]["type"] in IDENTITY_TYPES), None
            )
            if identity_id is not None:
                hop_id = identity_id
            elif middle_ids:
                hop_id = middle_ids[0]
            else:
                hop_id = sink_id  # two-node path: the hop collapses onto the target
    if hop_id is None:
        hop_name, hop_kind = "", "identity"
    elif hop_id == sink_id:
        hop_kind = "resource"
        hop_name = str(sink["name"])
        finding_text = _finding_ground_truth(chain_ids, findings)
        if finding_text:
            hop_name = f"{hop_name}: {finding_text}"
    elif by_id[hop_id]["type"] in IDENTITY_TYPES:
        hop_name, hop_kind = str(by_id[hop_id]["name"]), "identity"
    else:
        hop_kind = "resource"
        hop_name = str(by_id[hop_id]["name"])
        finding_text = _finding_ground_truth(chain_ids, findings)
        if finding_text:
            hop_name = f"{hop_name}: {finding_text}"
    return {
        "entry": entry_name,
        "hop": hop_name,
        "hop_kind": hop_kind,
        "sink": str(sink["name"]),
        "sink_kind": sink_kind or "data",
        "chain": [str(by_id[i]["name"]) for i in chain_ids],
    }


def _finding_ground_truth(
    path_ids: list[str], findings: list[dict[str, object]] | None
) -> str:
    """The first critical/high finding on the path, in the instructor's own words."""
    for finding in findings or []:
        on_path = any(r in path_ids for r in finding.get("resource_ids", []))
        if on_path and finding.get("severity") in ("critical", "high") and finding.get("ground_truth"):
            return str(finding["ground_truth"])
    return ""


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
                "Does `student.rationale` identify what the attacker reaches, "
                "named in `ground_truth.sink_name`?"
            ),
        ),
        "depth": Score(
            instructions=(
                "How far does `student.rationale` walk the chain in `ground_truth.chain`, "
                "relative to `ground_truth.explanation`?"
            ),
            criteria=list(DEPTH_LABELS),
        ),
    }


def _verdict(response: object, max_level: int) -> dict[str, object]:
    nouls = response.nouls  # type: ignore[attr-defined]
    score = response.scores["depth"]  # type: ignore[attr-defined]
    entry = float(nouls["names_entry"].noul)
    hop = float(nouls["names_identity_hop"].noul)
    sink = float(nouls["names_sink"].noul)
    raw = float(score.score)
    idx = max(0, min(round(raw), max_level))
    uncertain = any(UNCERTAIN_LOW < p < UNCERTAIN_HIGH for p in (entry, hop, sink))
    return {
        "names_entry": entry,
        "names_identity_hop": hop,
        "names_sink": sink,
        "depth_raw": raw,
        "depth_level": idx,
        "depth_max": max_level,
        "depth_label": DEPTH_LABELS[idx],
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
        "depth",
        f"{verdict['depth_level']} of {verdict['depth_max']} ({verdict['depth_label']})",
    )
    table.add_row("semantic hit", "yes" if verdict["semantic_hit"] else "no")
    table.add_row("instructor review", "yes" if verdict["needs_review"] else "no")
    console.print(table)


if __name__ == "__main__":
    raise SystemExit(main())
