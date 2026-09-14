# AI Frontier RKE Lab — handoff for the next session

**State on 2026-09-12 evening (Splice / Claude Code):** first vertical slice built and tested end to end on
`simonw/llm` with the REAL worker layer (GLM-5.3-Flash through MACR, 7 dispatches, verifier 39/39 twice,
8/8 hard gates, canonical overview v2 validated, ≈0.06 USD). `step4b_revise.py` runs the critic-driven
targeted revision + second verification. Nothing is published. Neo's decisions today: SEDB coupling across
projects is acceptable for now (enterprise version separates it later); GitHub tidy-up starts
this month; after MACR is fixed, run the real worker loop a few more times as a prototype;
then a new, cheaper conversation takes over the website.

## What lives where

| Thing | Path |
|---|---|
| Pipeline scripts | `pipeline/` (`rl_analyze.py`, `step1_register.py`, `step3_ground.py`, `step4_workers.py`, `step5_validate.py`, `step6_render.py`, `report.py`, `macr_worker.py`, `mock_worker.py`, `prompts.py`, `af_common.py`) |
| Real slice | `slice-001-simonw-llm/` (artifacts, packets, receipts, `REPORT.md`, `canonical/overview.md` v2 + `preview.html/png`; `worker_runs/` holds the 7 successful GLM runs and the 4 earlier blocked attempts) |
| Synthetic slice | `slice-001-simonw-llm-MOCK/` (`canonical/overview.md`, `preview.html`, `preview.png`, `REPORT.md`) — SYNTHETIC, never publish |
| SEDB catalog | `<SEDB repo>\projects\ai-frontier-repository-intelligence\` (config/store/cli/tests/README/VERIFY; sqlite git-ignored; committed as `d4f18d0`, README `464925a`) |
| Mock catalog copy | `mock/ai-frontier-mock.sqlite` (disposable) |
| RepoLumen cache | `repolumen-cache/` |
| Blocker note | `REQUEST_FOR_OPERATOR_GLM_ADMISSION_RECONCILIATION.md` |
| Concept | RKE series Papers 01–08 + FINAL_HANDOFF/RELEASE_GATES/ATTRIBUTION templates, the RKE series package (Neo.K's research archive, not in this repository) |
| Public page | evemisstechnology.com `/ai-frontier/` (repo `kakon77777-commits/evemiss-technology`, commit `3aab685`) — portal skeleton only, no repository pages yet |

## Run order (from this directory)

```powershell
# 0. deterministic analysis (RepoLumen venv), only for a new repo/revision
& '<RepoLumen>\.venv\Scripts\python.exe' pipeline\rl_analyze.py https://github.com/<owner>/<repo> <slice>\artifacts\repolumen\pending <expected_sha_or_->
# 1. GitHub artifacts must exist in <slice>\artifacts\github\{repo,head,languages,license,releases}.json
python pipeline\step1_register.py <slice>
python pipeline\step3_ground.py <slice>          # analysis run + groundings + bundle + packets (idempotent, reuses an existing run)
python pipeline\step4_workers.py <slice>         # real GLM through MACR (needs the circuit closed)
python pipeline\step5_validate.py <slice>        # hard gates → canonical Markdown (validated ≠ published)
python pipeline\step6_render.py <slice>          # local HTML preview
python pipeline\report.py <slice>
```

Synthetic dry run (never against the real catalog):
`$env:AF_WORKER_BACKEND='mock'; $env:AF_SEDB_DB='<lab>\mock\ai-frontier-mock.sqlite'`, then the same commands on a copied slice directory (see how `slice-001-simonw-llm-MOCK` was made in the session log: copy the slice, drop `worker_runs`, `tasks`, `canonical`, receipts of steps 4–5).

## Repeating the prototype run (MACR's GLM circuit is closed as of 2026-09-12 22:30 +08:00)

1. `& '<MACR>\scripts\macr.ps1' admission-status --provider glm_flash_worker` → expect `circuit_state: closed`, `reconciliation_required: 0`.
2. `python pipeline\step4_workers.py slice-001-simonw-llm` (task approvals created 2026-09-12 are reused; if expired, the script re-approves). Expect ~6 dispatches, conservative ceilings ≈ 0.03–0.04 USD each, latency minutes each (reasoning max).
3. `step5` → `step6` → `report`. Read `slice-001-simonw-llm/REPORT.md`: verifier claim table, deterministic failures, gates.
4. Prototype repetitions ("測試幾次"): rerun step 4 into fresh slice copies (e.g. `slice-001-simonw-llm-run2`) to see GLM variance: claim counts, unsupported claims, leakage flags, cost. Same packet hash each time; compare `worker_runs/*.json`.
5. Only after human review would a repository page be published; release gates G0–G10 in `RELEASE_GATES.md` decide, not this lab.

## Run 2 (2026-09-13 00:42 +08:00, same packet) — rejected by the gates, as designed

`slice-001-simonw-llm-run2`: writer draft 1 had 5 deterministic failures (4 claims marked `observed`
on execution-path evidence, 1 citing the phantom `readme.md`) → targeted revision fixed all 5 → verifier
failed it anyway: 2 claims (c30, c31) still upgraded execution paths to `observed` while also citing an
observed file ID (the deterministic check only requires one observed grounding), and 2 claims (c23,
c38) rested on packet sections that carry no citable IDs (`dependencies_partial`,
`analyzer_limitations`). The writer budget (2) was spent on the deterministic fix, so no verifier-driven
revision could run → `escalation_required`, asset revision v3 recorded as failed, nothing validated.
Taxonomy attempt 1 omitted the required `taxonomy_review_required` field → `schema_invalid`. Cost
0.050 USD, 727 s. Verifier found no leakage, no hallucinated names, no rights issues.

Yield so far: 1 validated out of 2 real runs. Fixes already applied for the next run: writer budget 3
(one attempt reserved for deterministic fixes), taxonomy gets one retry on a schema envelope failure.

Fixes for the next session (projector, needs a grounding-projection version bump to
`ai-frontier-grounding/v1.1` and a new analysis-run projection, do not edit the existing bundle):
- give citable IDs to packet sections the writers keep needing: `dependencies_partial` (per record),
  `analyzer_limitations` (per item), `module_roles` (per module), `relation_counts`;
- tighten the deterministic epistemic check: a claim marked `observed` that cites any execution path or
  reconstruction ID needs an explicit observed anchor for the observed part, or is downgraded;
- drop `important_2` (phantom `readme.md`) from packets by checking important files against the inventory.

## Invariants the next session must keep

- SEDB is canonical memory; RepoLumen is deterministic cognition; workers see packets only, never the repo or the database.
- Immutable kinds are append-only; step 3 reuses an existing analysis run rather than rewriting it.
- Repository text is untrusted data; packets are scrubbed for local paths and credentials before MACR sees them.
- Never issue MACR authorities from your own code; use `glm-preflight` → `glm-approve` → `glm-preflight` → `invoke-glm.ps1`.
- Mock output is SYNTHETIC and labelled; it is not evidence about GLM.
- Validated ≠ published. No publication event exists yet.

## Known analyzer issues (RepoLumen 0.10, recorded, not patched)

phantom `readme.md` important file on NTFS (case-insensitive match); `project.actual_capabilities` describes the analyzer; `claims.readme_claims` are code lines; dependencies only from requirements-style files. The projector excludes or flags all four.

## Next build items after the real run works

architecture asset (ArchitectureSelector → `writer/architecture/v1`), source walkthrough, Weekly Frontier data path (metadata snapshots exist), portal routes on the Astro site reading view models from the catalog, canary of 5–10 repositories.

## 2026-09-13 (new website session, Opus 5) — projector v1.1, validator v1.2, run 3 validated

The three "fixes for the next session" above are done (`pipeline.v1-backup-2026-09-12/` holds the code before them):

- **Projector `ai-frontier-grounding/v1.1`** (`step3_ground.py`): citable IDs with catalog state for
  dependency records `dep_*` (observed), analyzer limitations `lim_*` (observed, `analyzer_documentation`),
  module roles `role_*` (inferred) and `relation_counts` (observed); important files whose path is not in
  the analyzed inventory are excluded from catalog and packets (phantom `important_2` = `readme.md`), also
  from teaching-claim evidence lists; the packet ID note lists the new prefixes. A run's original
  projection is never rewritten: v1.1 is written to `artifacts/grounding/<run>/v1.1/`, and step 3 writes
  every grounding again so the store proves the superset (real catalog: +55 created, 15,006 unchanged,
  0 conflicts). The run record keeps its v1 ref/hash (immutable; `af_counts.groundings` is stale by design);
  the projection actually used is recorded in `grounding-receipt.json` (`projection_version`,
  `run_recorded_grounding_bundle_sha256`) and on every worker/asset row through the bundle hash.
- **Deterministic checks** (`step4_workers.py`): an `observed` claim may cite observed groundings only
  (any inferred/author_claimed ref → downgrade fix); a packet ID without a catalog state is a failure.
- **Contracts v1.1** (`prompts.py`: `writer/overview/v1.1`, `writer/overview-revision/v1.1`,
  `verifier/grounding/v1.1`): EPISTEMIC_RULES and verifier check 3 state the mixed-citation rule and the
  new ID families. v1 text is preserved in the recorded task inputs of runs 1–2. Use `WRITER_V`,
  `REVISION_V`, `VERIFIER_V` from `prompts.py`, never string literals.
- **Validator `af-validators/v1.2`** (`step5_validate.py`): the "best" wording gate matches superlative or
  marketing use only; the hedge "at best" (which the packet itself uses in `lim_4`) no longer trips it.
  Run 3's first validation (v4) was rejected by the v1.1 gate on exactly that hedge and stays recorded as failed.

**Run 3 (`slice-001-simonw-llm-run3`, same packet content as runs 1–2 plus the v1.1 IDs):** 7 dispatches,
all `candidate_success`, 838 s provider latency, 0.063 USD list (0.032 promotional). Taxonomy `cli-tools`
(+ artificial-intelligence, developer-tools), 0.74, no review. Writer draft 1: 39 claims (observed 26,
inferred 10, unresolved 2, author_claimed 1), 13 of them cite the new `dep_*`/`lim_*`/`role_*`/
`relation_counts` IDs; deterministic checks 0 failures; verifier 1 **pass 39/39**, no leakage, no
hallucinated names, no rights issues. Critic: structure 0.75, coherence 0.80, readability 0.55, 3 overclaim
flags (static paths stated as fact). Critic-driven revision (8 fixes) was **rejected by verifier 2**
(38/39: c25 attribution partially supported, c24 dangling pointer) → the verified draft 1 was kept, as
designed. Step 5 (v1.2): 8/8 gates → `assetrev_…_overview_v5` **validated, unpublished**. Yield: 2 validated
out of 3 real runs. Variance note: run 1's critic revision was accepted, run 3's was rejected — the revision
step adds risk without a clear quality gain on this packet; keep collecting before changing the budget.

**SYNTHETIC check** (mock backend on a mock catalog copy) passed end to end before the real run: 55 new
groundings, packet 181 IDs all with catalog state, phantom rejected as unknown, mock asset v3.

**Next items (in order):**
1. Writer contract v1.2: include the forbidden-wording list in the writer/revision contracts so the gate
   is never reached for wording; consider giving the critic the same list.
2. SEDB schema follow-up (SEDB repo, small additive change): an `af_grounding_projection` kind
   (run id, projection version, bundle ref, sha256, counts) so projection history lives in the catalog,
   not only in receipts.
3. Architecture asset (`writer/architecture/v1`, ArchitectureSelector packet); then the second repository;
   then portal routes reading view models from the catalog; then the 5–10-repository canary.
4. GitHub: this lab is now a local git repository (initial commit 2026-09-13). The remote waits for Neo's
   organization account; artifacts, worker outputs and canonical drafts are git-ignored (validated ≠ published).

## 2026-09-14 — projection history in the catalog, contracts v1.2, run 4 validated

- **Catalog contract**: `af_grounding_projection` (immutable) in the SEDB project (`aeb2d2e`, 8 tests): one row per
  analysis run × projector version with version, bundle ref/sha/bytes, counts, `af_supersedes_projection_id`.
  Step 3 writes it (`proj_<run>_v1_1`) and backfilled `proj_<run>_v1` from the run row; a rerun at the same
  version must reproduce the same bundle hash or step 3 refuses. Real catalog: v1 = 15,007, v1.1 = 15,061.
- **Contracts v1.2** (`writer/overview/v1.2`, `writer/overview-revision/v1.2`, `critic/structure/v1.1`): the
  gate's `FORBIDDEN_WORDING` now lives in `prompts.py` (step 5 imports it) and WORDING_RULES is in the
  writer/revision contracts; the critic flags rejected wording as an issue.
- **Run 4** (`slice-001-simonw-llm-run4`): 7 dispatches, 708 s, 0.060 USD list. Verifier 1 fail 37/39 — two
  *semantic* superlatives the verifier read as overclaims ("highest by static call degree", "the most
  referenced core modules") → targeted revision (2 fixes, 59 s) → verifier 2 pass 39/39; critic readability 0.70,
  scope 0.90, 1 overclaim flag, no wording issues; step 5 v1.2 → `…overview_v6` **validated, unpublished**.
  16/39 claims cite v1.1 IDs. Budget after step 4: writer 2/3, verifier 2/2 → no critic revision possible.
- **Yield 3/4 real runs** (v2, v5, v6 validated; run 2 rejected). Cost band 0.05–0.06 USD, 9–14 min per run.

**Next items (in order):** architecture asset (ArchitectureSelector packet + `writer/architecture/v1`,
verifier reuse) → second repository (needs GitHub artifacts + RepoLumen run; pick one with pyproject-only
dependencies to exercise the analyzer limitation) → portal routes on the Astro site reading view models from
the catalog (`/ai-frontier/repository/<owner>/<repo>/`) → Weekly Frontier data path → 5–10-repository canary.
Publication of any asset still needs human review + RELEASE_GATES G0–G10.

## 2026-09-14 (later) — the page is the deliverable: view model + publication event + portal route

Neo's calibration: the daily work is https://evemisstechnology.com/ai-frontier/ itself, not the lab. So:

- `step7_viewmodel.py <slice>` → `<slice>/canonical/repository-view.json` from the validated canonical
  Markdown (front matter + sections), receipts, taxonomy names; notices v0.1 verbatim; `publication.status`
  stays `unpublished`.
- `step8_publish.py <slice> --approved-by "Name" --via "how"` → writes `PUBLICATION_APPROVAL.json`
  (page-level gates G5/G6/G7/G9 + human content review), records `af_publication_event` and the asset's
  `published` status in the catalog, and drops the view model into the site
  (`AF_SITE_ROOT/src/data/ai-frontier/repositories/<owner>--<repo>.json`, `AF_SITE_ROOT` in
  `local_paths.json`). Then build + push the site. `--unpublish` reverses it (ledger keeps history).
- Site (`kakon77777-commits/evemiss-technology` `38a9e3b`): route `/ai-frontier/repository/[owner]/[repo]`
  EN + zh-TW, portal listing + status line; only published view models render; 29 pages until the first
  publication. Verified locally with run 4's view as a preview (31 pages, metadata, hreflang, sitemap,
  375 px no page overflow, no console errors); the preview file was removed before commit.

**First publication candidate:** run 4 `assetrev_…_overview_v6` (simonw/llm). Waiting for Neo's review of
the rendered preview. On his go: `step8_publish.py slice-001-simonw-llm-run4 --approved-by "Neo.K" --via
"chat 2026-09-14"` → `cd <site> && git add src/data && git commit && git push`.

**Then, in page terms:** second repository page (needs a new slice end to end), category pages (route per
taxonomy slug, listing published repositories), Weekly Frontier data path, canary batch.

**2026-09-14 14:29 +08:00 — published.** Neo approved after reviewing the rendered preview; event
`pub_assetrev_asset_repo_github_622352364_overview_v6_20260914T062749_published`; site commit `934a658`;
live at https://evemisstechnology.com/ai-frontier/repository/simonw/llm/ (EN + zh-TW), portal lists 1 guide.
Yield: 4 real runs → 1 published page. Next page-level step: a second repository, end to end.
