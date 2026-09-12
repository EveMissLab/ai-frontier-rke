"""Assemble the measured report for one vertical slice from its receipts.

Usage: python report.py <slice_slug>
"""
from __future__ import annotations

import json
import sys

from af_common import load_json, open_store, slice_dir


def main(slug: str) -> int:
    s = slice_dir(slug)
    reg = load_json(s / "registration-receipt.json")
    gr = load_json(s / "grounding-receipt.json")
    wr = load_json(s / "workers-receipt.json")
    val = load_json(s / "validation-receipt.json") if (s / "validation-receipt.json").exists() else None
    ar = load_json(s / gr["manifest_ref"].split("/", 1)[1].replace("semantic-manifest.json", "analysis-receipt.json"))
    store = open_store()
    stats = store.stats()
    chain = store.provenance_chain(val["asset_revision_id"]) if val else None

    runs = wr["runs"]
    def cost_of(r):
        c = r.get("cost") or {}
        for k in ("known_cost_usd", "cost_usd", "usd", "estimated_usd"):
            if isinstance(c.get(k), (int, float)):
                return c[k]
        return None
    total_known = sum(c for c in (cost_of(r) for r in runs) if c is not None)
    total_ceiling = sum(r.get("conservative_cost_ceiling_usd") or 0 for r in runs)
    total_latency = sum(r.get("latency_seconds") or 0 for r in runs)

    L = []
    L.append(f"# AI Frontier RKE — vertical slice 001 report{' (SYNTHETIC MOCK RUN)' if wr.get('synthetic') else ''}\n")
    L.append(f"Repository: `{reg['repository_id']}` · revision `{reg['commit_sha']}` · slice `{slug}` · worker backend `{wr.get('backend', 'macr')}` / `{wr.get('model', '')}`\n")
    if wr.get("synthetic"):
        L.append("> **SYNTHETIC.** Every worker output below was produced by the deterministic mock provider, not by a model. "
                 "It exists only to exercise orchestration, validators, canonical commit and provenance. Nothing here may be published or cited as GLM output.\n")
    L.append("## What ran\n")
    L.append("| Stage | Result |\n|---|---|")
    L.append(f"| GitHub → SEDB registration | {reg['sedb_write']['created_entities']} entities (repository, revision, snapshot, {len(reg['candidate_categories'])} rule-based category candidates, license `{reg['license']['status']}` {reg['license']['spdx']}, taxonomy v1 seed) |")
    L.append(f"| RepoLumen 0.10 deterministic analysis | {ar['elapsed_seconds']} s wall, manifest {ar['manifest_bytes']:,} bytes, schema valid = {ar['schema_valid']}, revision match = {ar['revision_matches_expected']} |")
    persisted = (f"persisted to SEDB ({gr['sedb_write']['created_entities']:,} entities / {gr['sedb_write']['written_cells']:,} cells)"
                 if gr.get("analysis_run_mode", "new") == "new" else "already in SEDB (existing analysis run reused, projections regenerated)")
    L.append(f"| GroundingBundle projection | {gr['grounding_bundle_bytes']:,} bytes, {gr['counts']['groundings']:,} namespaced groundings {persisted} |")
    L.append(f"| Worker packets | overview {gr['packets']['overview']['bytes']:,} bytes ({gr['bounded_context_ratio']*100:.2f}% of the manifest), taxonomy {gr['packets']['taxonomy']['bytes']:,} bytes |")
    L.append(f"| MACR / GLM-5.3-Flash dispatches | {len(runs)} attempts, {sum(1 for r in runs if r['status']=='candidate_success')} candidate_success; outcome `{wr.get('outcome')}` |")
    if val:
        L.append(f"| Hard publication gates | {'PASS' if val['hard_gate_pass'] else 'FAIL'} ({', '.join(k for k,g in val['gates'].items() if g['passed'])}; failed: {', '.join(k for k,g in val['gates'].items() if not g['passed']) or 'none'}) |")
        L.append(f"| Canonical Markdown | `{val['canonical_source_ref']}` · sha256 `{val['canonical_source_sha256'][:16]}…` · {val['canonical_bytes']:,} bytes · {val['claims']} claims, {val['supported_claims']} verifier-supported · publication_status `{val['publication_status']}` |")
    L.append("")
    L.append("## Worker runs\n")
    L.append("| # | role | attempt | status | latency s | conservative ceiling USD | known cost USD | candidate | summary |\n|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(runs, 1):
        L.append(f"| {i} | {r['role']} | {r['attempt']} | {r['status']}{(' ('+r['failure']+')') if r.get('failure') else ''} | {r.get('latency_seconds')} | {r.get('conservative_cost_ceiling_usd')} | {cost_of(r)} | `{(r.get('candidate') or '')[:8]}` | {json.dumps(r.get('summary'), ensure_ascii=False)[:160] if r.get('summary') else ''} |")
    L.append(f"\nTotals: latency {total_latency:.1f} s · conservative ceilings {total_ceiling:.4f} USD · known cost {total_known:.4f} USD (unknown-after-dispatch rows are not zero).\n")
    if wr.get("taxonomy"):
        L.append(f"Taxonomy (model-proposed, not validated): primary `{wr['taxonomy']['primary']}`, secondary {wr['taxonomy']['secondary']}, confidence {wr['taxonomy']['confidence']}, review_required {wr['taxonomy']['review_required']}.\n")
    L.append("## Deterministic checks and verifier\n")
    L.append(f"- deterministic check on first draft: {wr.get('deterministic_check_1')}")
    if wr.get("deterministic_check_2"):
        L.append(f"- deterministic check on revision: {wr.get('deterministic_check_2')}")
    L.append(f"- verifier verdict: {json.dumps(wr.get('verifier_verdict'), ensure_ascii=False)}\n")
    if val:
        L.append("## Gates\n")
        for k, g in val["gates"].items():
            L.append(f"- **{k}**: {'passed' if g['passed'] else 'FAILED'} — {json.dumps(g['details'], ensure_ascii=False)[:300]}")
        L.append("")
        L.append("## Provenance chain (FINAL_HANDOFF final verification questions)\n")
        L.append("```json\n" + json.dumps(chain, ensure_ascii=False, indent=1) + "\n```\n")
    L.append("## SEDB catalog state\n")
    L.append("```json\n" + json.dumps(stats, ensure_ascii=False, indent=1) + "\n```\n")
    (s / "REPORT.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(s / "REPORT.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
