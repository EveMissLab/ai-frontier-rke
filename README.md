# AI Frontier RKE — Repository Knowledge Engine (lab)

The engine behind [evemisstechnology.com/ai-frontier/](https://evemisstechnology.com/ai-frontier/):
one open-source repository in, one grounded, verified, canonical Markdown knowledge asset out.
This is the prototype lab for the *AI Frontier Repository Knowledge Engine* series (Papers 01–08,
EVEMISS Technology, 2026). It is a working vertical slice, not a crawler and not a product.

## Pipeline (fixed order, Paper 04)

```
GitHub metadata ─► SEDB (canonical memory) ─► RepoLumen (deterministic static analysis)
   ─► GroundingBundle (namespaced, catalogued groundings with epistemic state)
   ─► bounded WorkerInputPackets ─► cheap workers via MACR (GLM-5.3-Flash: taxonomy, writer,
      verifier, critic, formatter) ─► deterministic checks + hard gates ─► canonical Markdown
   ─► (human review + RELEASE_GATES G0–G10) ─► portal page
```

Workers see packets only, never the repository or the database. Repository text is untrusted
data. Every claim in an asset cites grounding IDs that exist in its packet and carries an
epistemic status (`observed` / `inferred` / `author_claimed` / `unresolved`) that may never be
stronger than the groundings it cites.

## Run order

```powershell
# 0. deterministic analysis (RepoLumen venv), once per repository revision
& '<RepoLumen>\.venv\Scripts\python.exe' pipeline\rl_analyze.py https://github.com/<owner>/<repo> <slice>\artifacts\repolumen\pending <expected_sha_or_->
# 1. GitHub artifacts in <slice>\artifacts\github\{repo,head,languages,license,releases}.json
python pipeline\step1_register.py <slice>
python pipeline\step3_ground.py <slice>       # analysis run + groundings + bundle + packets (idempotent)
python pipeline\step4_workers.py <slice>      # real workers through MACR (glm-preflight → glm-approve → invoke)
python pipeline\step4b_revise.py <slice>      # optional critic-driven revision + independent re-verification
python pipeline\step5_validate.py <slice>     # hard gates → canonical Markdown (validated ≠ published)
python pipeline\step6_render.py <slice>       # local HTML preview
python pipeline\report.py <slice>
```

`AF_WORKER_BACKEND=mock` with `AF_SEDB_DB=<copy of the catalog>` runs a SYNTHETIC dry run on a
copied slice. Mock output is labelled SYNTHETIC everywhere and is never evidence about a model.

## Invariants

- SEDB is canonical memory; RepoLumen is deterministic cognition; workers are bounded and hold no authority.
- Immutable kinds are append-only. A run's original grounding projection is never rewritten: a newer
  projector version writes beside it (`artifacts/grounding/<run>/v1.1/`) and appends its new groundings.
- Never issue MACR authorities from pipeline code.
- Validated ≠ published. No publication event exists yet.
- Rights: explain, don't reproduce. No README mirroring, no logo reuse, static analysis only.

## Status (simonw/llm, first vertical slice)

| Run | Date | Projector / validator | Dispatches | Verifier | Gates | Result | Cost (list) |
|---|---|---|---|---|---|---|---|
| 1 | 2026-09-12 | v1 / v1.1 | 7 | 39/39, twice | 8/8 | overview **v2 validated**, unpublished | ≈0.061 USD |
| 2 | 2026-09-13 | v1 / v1.1 | 5 | fail (4 claims) | — | rejected by the gates | 0.050 USD |
| 3 | 2026-09-13 | **v1.1** / **v1.2** | 7 | 39/39 first draft; critic revision rejected by verifier 2 (38/39) | 8/8 | overview **v5 validated**, unpublished | 0.063 USD |

Details and the next steps are in [HANDOFF.md](HANDOFF.md).

## What is not in this repository

Analysis artifacts, grounding bundles, MACR task files, worker outputs and canonical drafts stay
local (`.gitignore`) until a publication event. Receipts (counts, hashes, costs, gate results) are tracked.
The SEDB catalog project lives in the SEDB repository (`projects/ai-frontier-repository-intelligence`).
