# AI Frontier RKE — Repository Knowledge Engine (lab)

The engine behind [evemisstechnology.com/ai-frontier/](https://evemisstechnology.com/ai-frontier/):
one open-source repository in, one grounded, verified, canonical Markdown knowledge asset out.
This is the prototype lab for the *AI Frontier Repository Knowledge Engine* series (Papers 01–08,
EVEMISS Technology, 2026). It is a working vertical slice, not a crawler and not a product.

| | |
|---|---|
| Product status | **lab / prototype** — one repository (simonw/llm) end to end; nothing published yet |
| Version | 0.1.1-lab (projector v1.1, validators v1.2, contracts v1.2, catalog projection history) |
| Canonical website | https://evemisstechnology.com/ai-frontier/ |
| Maintainer | Neo.K (許筌崴), EVEMISS TECHNOLOGY CO., LTD. — kakon77777@evemisslab.com |
| Issues | bug reports and questions welcome; feature requests are triaged against the RKE series, not first-come |

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

Component locations (SEDB, MACR, RepoLumen) come from `AF_*_ROOT` environment variables, else
`pipeline/local_paths.json` (git-ignored), else sibling directories of this lab.

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
| 4 | 2026-09-14 | v1.1 / v1.2, contracts **v1.2** | 7 | 37/39 → verifier-driven revision (2 wording overclaims) → 39/39 | 8/8 | overview **v6 validated**, unpublished | 0.060 USD |

Details and the next steps are in [HANDOFF.md](HANDOFF.md); changes in [CHANGELOG.md](CHANGELOG.md).

## AI contribution, provenance and generated artifacts

- The knowledge assets this pipeline produces are **model-generated** (GLM-5.3-Flash through MACR) from
  deterministic evidence, checked by deterministic validators and an independent model verifier, and
  reviewed by a human before any publication. Every asset revision records its analysis run, grounding
  bundle hash, worker run IDs and validator version in the SEDB catalog.
- Synthetic (mock) outputs are labelled SYNTHETIC and are never reported as model output.
- Pipeline code in this repository was written with AI assistance (Claude Code) under the maintainer's
  direction and review; commits carry a `Co-Authored-By` trailer when that is the case.
- Machine-readable AI content/rights declarations follow the AICL + AIRS/AILP `/ai/` layer used across
  EveMissLab sites; this repository does not define its own.

## What is not in this repository

Analysis artifacts, grounding bundles, packets, MACR task files, worker outputs and canonical drafts stay
local (`.gitignore`) until a publication event. Receipts (counts, hashes, costs, gate results) are tracked.
The SEDB catalog project lives in the SEDB repository (`projects/ai-frontier-repository-intelligence`).

## 關於本專案 (About & License)

本專案由 **一言諾科技有限公司 (EVEMISS TECHNOLOGY CO., LTD.)** 研發與維護。
* **系統架構師 / 作者：** Neo.K (許筌崴)
* **營運總部：** 台灣 台北市 (Taipei City, Taiwan)
* **商業與授權聯繫：** kakon77777@evemisslab.com

本專案採用 [MIT License](LICENSE) 開源授權。
我們鼓勵任何形式的學術探討、商業應用與代碼修改，但所有衍生版本與散佈行為，均必須保留原作者出處與授權聲明。

> **免責與專利保留聲明：**
> 本開源釋出僅針對當前代碼與邏輯結構。EVEMISS TECHNOLOGY 保留未來進階演算模組與相關架構之專利申請權利。
