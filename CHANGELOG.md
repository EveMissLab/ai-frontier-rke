# Changelog

## 0.1.1-lab — 2026-09-14

- Catalog: `af_grounding_projection` kind (SEDB project commit `aeb2d2e`) — one immutable row per
  analysis run × projector version; step 3 writes it and backfills the run's original v1 projection.
- Contracts v1.2 (writer, revision) carry the release gate's wording list; critic v1.1 flags it; the list
  lives in `prompts.py` and step 5 imports it.
- Run 4 on simonw/llm: overview v6 validated (unpublished) after one verifier-driven revision, 0.060 USD.

## 0.1.0-lab — 2026-09-13

- Projector `ai-frontier-grounding/v1.1`: citable IDs for dependency records, analyzer limitations,
  module roles and relation counts; phantom important files excluded by inventory check; a run's
  original projection is never rewritten.
- Deterministic checks: an `observed` claim may cite observed groundings only; packet IDs without a
  catalog state fail.
- Worker contracts v1.1 (writer, revision, verifier) state the mixed-citation rule.
- Validators v1.2: the "best" wording gate matches superlative/marketing use only.
- Run 3 on simonw/llm: overview v5 validated (unpublished), 39/39 claims supported, 0.063 USD.
- Component locations configurable (`AF_*_ROOT`, `pipeline/local_paths.json`).

## 2026-09-12

- First vertical slice (simonw/llm): SEDB catalog project, RepoLumen adapter, grounding bundle v1,
  bounded packets, MACR/GLM worker loop, hard gates, canonical Markdown, local render. Run 1 validated
  overview v2 (unpublished); run 2 rejected by the gates.
