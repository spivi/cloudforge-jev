# cloudforge + Jev sidecar

A trainer already has [cloudforge](https://github.com/spivi/forge-x-labs): a
local lab generator with a machine-checkable answer key. `cloudforge grade`
matches guessed node ids as a subsequence of the labeled path.

Students also write a paragraph. That text is not a node-id list. This sidecar
sits **outside** cloudforge and uses [TypeSafe Jev](https://docs.typesafe.ai)
to judge whether the paragraph names the same entry, identity hop, and sink.

cloudforge stays hermetic and no TypeSafe dependency lands in the OSS product;
the code here owns the thresholds and Jev returns the probabilities.

## What Jev is doing

One request, four atomic questions over the same state:

| Question | Primitive | Meaning |
|---|---|---|
| `names_entry` | Noul | Same initial access as the labeled path? |
| `names_identity_hop` | Noul | Same hop that grants the access: the first identity after the entry, or the misconfigured resource when the path has none (a public bucket, a shared snapshot) |
| `names_sink` | Noul | Same sensitive data sink? |
| `completeness` | Score | Missed / partial / full chain |

The three names come from node types, not positions: an owning account as the entry means public, unauthenticated access; an external account is named as such. A semantic hit is all three Nouls at or above 0.7. A Noul between 0.4 and 0.6
flags instructor review. That composition is ordinary Python.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export CLOUDFORGE_ROOT=/path/to/forge-x-labs
.venv/bin/pip install -e "$CLOUDFORGE_ROOT"   # only needed when the sidecar generates the lab
# TYPESAFE_API_KEY in .env
```

## Run

`--lab` is required unless you pass `--generate`. The sidecar fails closed: no
Jev call, no lab write, until you say which one you want.

Matching writeup, against a pack `cloudforge lab` already wrote:

```bash
.venv/bin/python judge_lab.py \
  --lab out/demo-lab \
  --rationale samples/match.txt
```

Wrong risk:

```bash
.venv/bin/python judge_lab.py \
  --lab out/demo-lab \
  --rationale samples/miss.txt
```

No pack yet:

```bash
.venv/bin/python judge_lab.py \
  --generate \
  --cloudforge-root "$CLOUDFORGE_ROOT" \
  --rationale samples/match.txt
```

`--lab` reuses a pack `cloudforge lab` already wrote. `--generate` writes one
with cloudforge first.

## What comes back

Both samples, run against the same `ci_cd_iam_chain` lab (GitHub Actions OIDC,
`iam:PassRole` to a runtime role, `customer-exports`):

| Signal | `samples/match.txt` | `samples/miss.txt` |
|---|---|---|
| names entry | 0.94 | 0.05 |
| names identity hop | 0.86 | 0.02 |
| names sink | 0.82 | 0.06 |
| completeness (0 to 2) | 1.59, full chain | 0.01, different risk |
| semantic hit | yes | no |
| instructor review | no | no |

The miss names a public bucket and no IAM chain. Jev did not split the
difference; the numbers went to the rule the code already had.

The numbers are from one run. A second run of the same text moves them by a few
hundredths, and by more on borderline answers; that is what the review band is for.

Without `TYPESAFE_API_KEY` the sidecar exits with an error and the lab is
untouched.
