# Request: reconcile the GLM provider-admission circuit (MACR runtime schema 8)

**From:** Splice (Claude Code), AI Frontier RKE vertical slice, 2026-09-12
**To:** Neo.K / MACR operator (Codex maintains MACR)
**Blocker:** every `glm_flash_worker` dispatch is rejected before any network call with
`provider_admission_reconciliation_required`.

## Observed state (read-only, `admission-status --provider glm_flash_worker`)

```text
circuit_state            open
last_signal              unknown_after_dispatch
counts.reconciliation_required   1
counts.completed         69
project_active_counts    4737df6a…3e51 → active_units 1
```

The stuck request is `c69ef535-49c3-4220-95c4-5aa7d2933ef9` (run `989fd0ee-439f-46a2-b5b8-b88238ba4d6f`,
lane `routine`, requested 2026-09-11T08:35:38Z, terminal 2026-09-11T08:37:56Z). Accounting shows one
`unknown_after_dispatch` row for that window with `failure_code=ConnectionResetError`,
`failure_stage=provider_execution`, known cost 0.0 USD (not zero cost — unreconciled). It belongs to the
EveAtelier session of 2026-09-11, not to this slice.

Codex's checkpoint `docs/checkpoints/MACR-v0.7.0a0-grok-admission-context-2026-09-12.md` records the same
request and says it "was deliberately left untouched"; billing reconciliation "require[s] separate
operator action".

## Why I did not resolve it myself

`ProviderAdmissionKernel.resolve_reconciliation(request_id, reference, resolution_evidence_digest=…)`
requires an `AuthorizationReference` issued for plane `provider_admission_reconciliation` /
task type `provider_admission_resolution` (see `tests/test_provider_admission.py`, `source_kind
"operator_reconciliation"`). No `macr.ps1` subcommand exposes this; the README states that no ordinary
CLI command can self-authorize such a transition. Issuing that authority from my own script would bypass
the boundary MACR exists to enforce, so I stopped at the gate.

## What is needed

1. A decision on the 2026-09-11 request: was the Z.ai call billed? (billing reconciliation evidence →
   `resolution_evidence_digest`).
2. Either an operator reconciliation through the intended path, or a new CLI subcommand from Codex
   (`admission-reconcile --provider glm_flash_worker --request-id … --evidence-digest …`).
3. Then rerun, from `D:\Ai\work together\AI-Frontier-RKE-Lab`:
   `python pipeline\step4_workers.py slice-001-simonw-llm` → `step5_validate.py` → `step6_render.py` →
   `report.py`. The two task approvals created today (taxonomy, writer) stay valid for 2 days and are
   reused automatically.

## Also observed (not blocking)

`macr.ps1 capability-status --provider glm_flash_worker` fails with `DispatchLeaseError`: the status
reader in `scheduler.read_queue_tier_status` accepts runtime schema versions 5–7 only, while the runtime
database is at schema 8. Status-only; `glm-preflight`/`glm-approve`/`invoke` are unaffected.
