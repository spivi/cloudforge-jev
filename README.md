# cloudforge + Jev sidecar

A trainer already has [cloudforge](https://github.com/spivi/forge-x-labs): a
local lab generator with a machine-checkable answer key. `cloudforge grade`
matches guessed node ids as a subsequence of the labeled path.

Students also write a paragraph. That text is not a node-id list. This sidecar
sits **outside** cloudforge and uses [TypeSafe Jev](https://docs.typesafe.ai)
to judge whether the paragraph names the same entry, identity hop, and sink.

cloudforge stays hermetic. No TypeSafe dependency lands in the OSS product.
Code here owns the thresholds. Jev returns probabilities.

## What Jev is doing

One request, four atomic questions over the same state:

| Question | Primitive | Meaning |
|---|---|---|
| `names_entry` | Noul | Same initial access as the labeled path? |
| `names_identity_hop` | Noul | Same identity / role hop? |
| `names_sink` | Noul | Same sensitive data sink? |
| `completeness` | Score | Missed / partial / full chain |

A semantic hit is all three Nouls at or above 0.7. A Noul between 0.4 and 0.6
flags instructor review. That composition is ordinary Python.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export CLOUDFORGE_ROOT=/path/to/forge-x-labs
# TYPESAFE_API_KEY in .env
```

## Run

Matching writeup:

```bash
.venv/bin/python judge_lab.py \
  --cloudforge-root "$CLOUDFORGE_ROOT" \
  --rationale samples/match.txt
```

Wrong risk:

```bash
.venv/bin/python judge_lab.py \
  --cloudforge-root "$CLOUDFORGE_ROOT" \
  --lab out/demo-lab \
  --rationale samples/miss.txt
```

`--lab` reuses a pack `cloudforge lab` already wrote. Omit it and the sidecar
generates one.

## Why this is the fit

- Exact grading stays in OSS (`cloudforge grade`).
- The hard part is semantic: did the student describe the same chain in prose?
- Jev is a typed decision, not a generated explanation.
- Fail-soft: no key, no sidecar. The lab still works.
