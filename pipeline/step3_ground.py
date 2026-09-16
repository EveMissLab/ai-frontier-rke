"""Step 3 — RepoLumenSEDBAdapter (bounded): manifest → analysis run → GroundingBundle → SEDB.

Paper 04 contract: validate manifest → hash artifact → project a deterministic
GroundingBundle → namespace every grounding ID → map epistemic state (keeping
the original provenance) → commit typed records. External providers are
disabled; nothing here calls a model.

Also builds the task-bounded WorkerInputPackets (OverviewSelector,
TaxonomySelector) so workers never see the whole repository (Paper 04 §68-73).

Usage: python step3_ground.py <slice_slug>
"""
from __future__ import annotations

import json
import shutil
import sys
from collections import Counter
from pathlib import Path

from af_common import (
    analysis_run_id, canonical_bytes, load_json, open_store, rel_ref, sha256_bytes, sha256_file,
    slice_dir, utc_now, write_json,
)

ENGINE, ENGINE_VERSION, MANIFEST_SCHEMA = "RepoLumen", "0.10", "0.10"
ANALYSIS_CONFIG = {"engine": ENGINE, "version": ENGINE_VERSION, "mode": "deterministic",
                   "audience": "developer", "external_provider": "disabled", "history": False}


def analysis_config(m: dict) -> tuple[dict, str]:
    """Result-affecting analyzer configuration for this manifest and its hash. Bounded analyses (RepoLumen
    limits.py, 2026-09-14) carry `uncertainty.analysis_bounds`; unbounded 0.10 manifests do not, so their
    identity is unchanged."""
    cfg = dict(ANALYSIS_CONFIG)
    bounds = (m.get("uncertainty") or {}).get("analysis_bounds")
    if bounds:
        cfg["bounds"] = bounds
    return cfg, sha256_bytes(canonical_bytes(cfg))


CONFIG_HASH = sha256_bytes(canonical_bytes(ANALYSIS_CONFIG))  # unbounded-manifest hash (kept for reference)

# RepoLumen teaching provenance → SEDB epistemic state (Paper 04 §51).
EPISTEMIC = {"verified": "observed", "strongly_inferred": "inferred", "weakly_inferred": "inferred",
             "author_claim": "author_claimed", "unresolved": "unresolved"}

# Analyzer limitations that apply to every v0.10 run (RepoLumen README, Paper 04 §104).
ANALYZER_LIMITATIONS = [
    "static analysis only: reflection, runtime dependency injection, dynamic imports, monkey-patching, generated code, framework runtime wiring and dynamic dispatch are not resolved",
    "install/run/build inference is partial; installation commands are not verified",
    "dependency records come only from requirements-style manifests parsed by v0.10 (pyproject.toml dependency tables are not parsed)",
    "README claim extraction in v0.10 is line-based and may capture code lines instead of prose claims; treat every README claim as author_claimed at best",
    "project.actual_capabilities / non_capabilities in the manifest describe the analyzer, not the analyzed repository, and are excluded from repository evidence",
]
CAPABILITY_PROFILE = {"python": "strong_static", "javascript": "bounded_heuristic", "other": "structural_only"}

# Grounding projection version (Paper 04 §47). v1.1 (2026-09-13): citable IDs for dependency records
# (dep_*), analyzer limitations (lim_*), module roles (role_*) and relation_counts; important files whose
# path is not in the analyzed inventory are excluded from the catalog and packets (RepoLumen 0.10 reports
# a case-variant phantom `readme.md` on NTFS). A run's original projection is never rewritten: a newer
# projection is written beside it under its own version directory and its new groundings are appended.
# v1.2 (2026-09-14): bundle uncertainties carry the analyzer's skipped_files / relation_truncations (bounded
# analysis), and bounded manifests get an extra analyzer-limitation grounding; analysis identity includes
# the analyzer bounds. Existing runs get a v1.2 projection row beside v1 / v1.1.
BUNDLE_VERSION = "ai-frontier-grounding/v1.2"
PROJECTION_DIR = "v1.2"


def compact_symbol(s):
    return {"id": s["id"], "type": s.get("type"), "symbol": s.get("symbol"), "path": s.get("path"),
            "start_line": s.get("start_line"), "end_line": s.get("end_line"), "observation": s.get("observation"),
            "provenance": s.get("provenance"), "confidence": s.get("confidence"), "excerpt": s.get("excerpt")}


def compact_relation(r):
    return {"id": r["id"], "type": r.get("type"), "source_path": r.get("source_path"), "source_symbol": r.get("source_symbol"),
            "target": r.get("target"), "target_path": r.get("target_path"), "target_symbol": r.get("target_symbol"),
            "resolution": r.get("resolution"), "line": r.get("line"), "provenance": r.get("provenance"), "confidence": r.get("confidence")}


def compact_block(b, ann):
    return {"id": b["id"], "path": b.get("path"), "start_line": b.get("start_line"), "end_line": b.get("end_line"),
            "symbol": b.get("symbol"), "kind": b.get("kind") or b.get("block_kind"),
            "annotation": None if ann is None else {"summary": ann.get("summary"), "why_it_exists": ann.get("why_it_exists"),
                                                     "provenance": ann.get("provenance"), "confidence": ann.get("confidence"),
                                                     "evidence_ids": ann.get("evidence_ids", [])}}


def build_bundle(m: dict, repo_id: str, rev_id: str, run_id: str, metadata: dict) -> dict:
    a, c = m["architecture"], m["code_map"]
    annotations = {x["block_id"]: x for x in m.get("annotations", [])}
    files = sorted(m["knowledge_cache"]["file_changes"]["added"])
    top_dirs = Counter((f.split("/")[0] if "/" in f else "(root)") for f in files)
    inventory = set(files)

    def in_inventory(path):  # a file, or a directory containing inventory files (subsys_* cite directories)
        return bool(path) and (path in inventory or any(f.startswith(path.rstrip("/") + "/") for f in files))

    important_all = c.get("important_files", [])
    important_kept = [f for f in important_all if in_inventory(f.get("path"))]
    excluded_important = [{"excluded_id": f["id"], "path": f.get("path"), "reason": "path not in the analyzed file inventory (analyzer matched a case variant on a case-insensitive filesystem)"}
                          for f in important_all if not in_inventory(f.get("path"))]
    excluded_ids = {x["excluded_id"] for x in excluded_important}
    role_items = [dict(r, id=f"role_{i}") for i, r in enumerate(a.get("reconstruction", {}).get("module_roles", []), 1)]
    dep_items = [dict(d, id=f"dep_{i}") for i, d in enumerate(c.get("dependencies", []), 1)]
    limitations = list(ANALYZER_LIMITATIONS)
    bounds = (m.get("uncertainty") or {}).get("analysis_bounds")
    if bounds:
        limitations.append(f"bounded analysis (bounds {bounds.get('bounds_version')}): files over {bounds.get('max_source_file_bytes', 0) // 1000} kB, minified bundles and vendored directories are inventoried but not parsed; relation extraction is capped at {bounds.get('max_relations_per_file')} per file and {bounds.get('max_relations_total')} per repository (see uncertainties.skipped_files / relation_truncations)")
    lim_items = [{"id": f"lim_{i}", "text": t} for i, t in enumerate(limitations, 1)]
    teaching = m["teaching"]
    sections = teaching["sections"] if isinstance(teaching["sections"], list) else list(teaching["sections"].values())
    teaching_claims = [dict(cl, section=sec.get("id")) for sec in sections for cl in sec.get("claims", [])]

    # External evidence domain: platform metadata (Paper 04 §107-108, separate provenance).
    meta_groundings = [
        {"id": "meta_description", "type": "repository_metadata", "epistemic_status": "author_claimed", "text": metadata.get("description"), "origin": "github_api"},
        {"id": "meta_homepage", "type": "repository_metadata", "epistemic_status": "author_claimed", "text": metadata.get("homepage"), "origin": "github_api"},
        {"id": "meta_topics", "type": "repository_metadata", "epistemic_status": "observed", "text": ", ".join(metadata.get("topics", [])), "origin": "github_api"},
        {"id": "meta_primary_language", "type": "repository_metadata", "epistemic_status": "observed", "text": metadata.get("language"), "origin": "github_api"},
        {"id": "meta_language_bytes", "type": "repository_metadata", "epistemic_status": "observed", "text": json.dumps(metadata.get("languages"), sort_keys=True), "origin": "github_api"},
        {"id": "meta_license", "type": "repository_metadata", "epistemic_status": "observed", "text": metadata.get("license_spdx"), "origin": "github_api+repository_file"},
        {"id": "meta_latest_release", "type": "repository_metadata", "epistemic_status": "observed", "text": f"{metadata.get('latest_release_tag')} published {metadata.get('latest_release_at')}", "origin": "github_api"},
        {"id": "meta_stars", "type": "repository_metadata", "epistemic_status": "observed", "text": f"{metadata.get('stars')} stargazers at {metadata.get('observed_at')}", "origin": "github_api"},
    ]

    bundle = {
        "bundle_version": BUNDLE_VERSION,
        "repository_id": repo_id,
        "revision_id": rev_id,
        "analysis_run_id": run_id,
        "analyzer": {"name": ENGINE, "version": ENGINE_VERSION, "manifest_schema_version": m.get("schema_version"),
                     "mode": "deterministic", "external_provider": "disabled", "capability_profile": CAPABILITY_PROFILE},
        "source": {k: v for k, v in m["source"].items() if k != "local_path"},
        "repository_summary": {
            "title": m["project"].get("title"),
            "purpose_inferred_by_analyzer": m["project"].get("purpose"),
            "thirty_second_explanation_inferred_by_analyzer": m["rendered"].get("thirty_second_explanation"),
            "maturity_signals": m["project"].get("maturity_signals", []),
            "file_count": len(files),
            "top_level_directories": dict(sorted(top_dirs.items())),
            "language_hint": m["source"].get("language") or metadata.get("language"),
        },
        "platform_metadata": meta_groundings,
        "important_files": important_kept,
        "entrypoints": a.get("entrypoints", []),
        "modules": a.get("modules", []),
        "module_role_ids": role_items,
        "architecture": {
            "reconstruction": a.get("reconstruction", {}),
            "execution_flow": a.get("execution_flow", []),
            "data_flow": a.get("data_flow", []),
            "resolved_relation_summary": a.get("resolved_relation_summary", []),
            "relation_counts": {"id": "relation_counts", "total": len(a.get("relations", [])),
                                "by_resolution": dict(Counter(r.get("resolution") for r in a.get("relations", []))),
                                "by_type": dict(Counter(r.get("type") for r in a.get("relations", [])))},
        },
        "execution_paths": a.get("execution_paths", []),
        "dependencies": c.get("dependencies", []),
        "dependency_records": dep_items,
        "tests": c.get("tests", []),
        "files": files,
        "symbols": [compact_symbol(s) for s in c.get("symbols", [])],
        "semantic_blocks": [compact_block(b, annotations.get(b["id"])) for b in c.get("semantic_blocks", [])],
        "relations": [compact_relation(r) for r in a.get("relations", [])],
        "evidence": m.get("evidence", []),
        "teaching_claims": teaching_claims,
        "teaching_sections": [{"id": s.get("id"), "title": s.get("title"), "claim_ids": [cl["id"] for cl in s.get("claims", [])]} for s in sections],
        "lessons": m["lessons"].get("chapters", []),
        "runtime_hints_partial": m.get("runtime", {}),
        "readme_claims_author_claimed_low_quality": m["claims"].get("readme_claims", []),
        "uncertainties": {
            "analyzer": m.get("uncertainty", {}).get("unresolved", []),
            "unresolved_relation_count": m.get("uncertainty", {}).get("unresolved_relation_count"),
            "analyzer_limitations": lim_items,
            "excluded_important_files": excluded_important,
            "skipped_files": (m.get("uncertainty") or {}).get("skipped_files", []),
            "relation_truncations": (m.get("uncertainty") or {}).get("relation_truncations", []),
            "teaching_warnings": teaching.get("warnings", []),
        },
    }
    # Grounding catalog (Paper 04 §47): every citable ID with its type and epistemic state.
    catalog = {}
    for e in bundle["evidence"]:
        if e["id"] in excluded_ids:  # v1.1: a phantom path is not citable (the evidence list itself is never edited)
            continue
        catalog[e["id"]] = {"type": "evidence:" + str(e.get("type") or e.get("evidence_kind")), "epistemic_status": EPISTEMIC.get(e.get("provenance"), "unresolved"),
                            "source_provenance": e.get("provenance"), "path": e.get("path"), "start_line": e.get("start_line"), "end_line": e.get("end_line"), "symbol": e.get("symbol"), "text": e.get("observation")}
    for r in bundle["relations"]:
        catalog[r["id"]] = {"type": "relation:" + str(r.get("type")), "epistemic_status": EPISTEMIC.get(r.get("provenance"), "unresolved"),
                            "source_provenance": r.get("provenance"), "path": r.get("source_path"), "start_line": r.get("line"), "end_line": r.get("line"), "symbol": r.get("source_symbol"), "text": f"{r.get('type')} {r.get('target')} ({r.get('resolution')})"}
    for b in bundle["semantic_blocks"]:
        ann = b.get("annotation") or {}
        catalog[b["id"]] = {"type": "semantic_block", "epistemic_status": EPISTEMIC.get(ann.get("provenance"), "observed"),
                            "source_provenance": ann.get("provenance") or "verified", "path": b.get("path"), "start_line": b.get("start_line"), "end_line": b.get("end_line"), "symbol": b.get("symbol"), "text": ann.get("summary")}
    for p in bundle["execution_paths"]:
        catalog[p["id"]] = {"type": "execution_path", "epistemic_status": "inferred", "source_provenance": "strongly_inferred",
                            "path": p.get("entrypoint_path"), "start_line": None, "end_line": None, "symbol": p.get("entrypoint_symbol"), "text": f"bounded static path from {p.get('entrypoint_path')}:{p.get('entrypoint_symbol')} ({len(p.get('steps', []))} steps, {p.get('terminal_reason')})"}
    for cl in bundle["teaching_claims"]:
        catalog[cl["id"]] = {"type": "teaching_claim", "epistemic_status": EPISTEMIC.get(cl.get("provenance"), "unresolved"), "source_provenance": cl.get("provenance"),
                             "path": None, "start_line": None, "end_line": None, "symbol": None, "text": cl.get("text")}
    for g in meta_groundings:
        catalog[g["id"]] = {"type": g["type"], "epistemic_status": g["epistemic_status"], "source_provenance": g["origin"], "path": None, "start_line": None, "end_line": None, "symbol": None, "text": g["text"]}
    catalog["summary_repository"] = {"type": "summary_repository", "epistemic_status": "inferred", "source_provenance": "strongly_inferred", "path": None, "start_line": None, "end_line": None, "symbol": None, "text": bundle["repository_summary"]["purpose_inferred_by_analyzer"]}
    for d in bundle["dependency_records"]:
        catalog[d["id"]] = {"type": "dependency_record", "epistemic_status": "observed", "source_provenance": "verified", "path": d.get("source_path"), "start_line": d.get("line"), "end_line": d.get("line"), "symbol": d.get("name"),
                            "text": f"{d.get('name')} {d.get('version') or ''} ({d.get('scope')}) declared in {d.get('source_path')}"}
    for r in bundle["module_role_ids"]:
        catalog[r["id"]] = {"type": "module_role", "epistemic_status": "inferred", "source_provenance": "strongly_inferred", "path": r.get("path"), "start_line": None, "end_line": None, "symbol": None,
                            "text": f"{r.get('role')} ({r.get('incoming_calls')} incoming, {r.get('outgoing_calls')} outgoing static calls; {r.get('evidence_basis')})"}
    for l in bundle["uncertainties"]["analyzer_limitations"]:
        catalog[l["id"]] = {"type": "analyzer_limitation", "epistemic_status": "observed", "source_provenance": "analyzer_documentation", "path": None, "start_line": None, "end_line": None, "symbol": None, "text": l["text"]}
    rc = bundle["architecture"]["relation_counts"]
    catalog["relation_counts"] = {"type": "relation_counts", "epistemic_status": "observed", "source_provenance": "verified", "path": None, "start_line": None, "end_line": None, "symbol": None,
                                  "text": f"{rc['total']} static relations; by resolution {json.dumps(rc['by_resolution'], sort_keys=True)}; by type {json.dumps(rc['by_type'], sort_keys=True)}"}
    catalog["architecture_reconstruction"] = {"type": "architecture_reconstruction", "epistemic_status": "inferred", "source_provenance": "strongly_inferred", "path": None, "start_line": None, "end_line": None, "symbol": None, "text": "graph-derived architecture reconstruction (static)"}
    bundle["groundings"] = catalog
    return bundle


def sample_files(files: list[str], n: int = 40) -> list[str]:
    """Bounded file sample for the packet: the top-level directory holding the most source files
    (tests/docs excluded when something else exists), in path order. v1.1.1: was hard-coded to `llm/`."""
    from collections import Counter as _C
    src = [f for f in files if "/" in f and not f.split("/")[0].lower().startswith(("test", "doc", ".github", "example"))]
    pool = src or [f for f in files if "/" in f] or files
    top = _C(f.split("/")[0] for f in pool).most_common(1)[0][0]
    return [f for f in files if f.startswith(top + "/")][:n]


def overview_packet(bundle: dict, license_state: dict) -> dict:
    """OverviewSelector (Paper 04 §73): purpose, description, important files, entrypoints,
    high-level architecture, uncertainty. Deterministic and bounded."""
    recon = bundle["architecture"]["reconstruction"]
    excluded = {x["excluded_id"] for x in bundle["uncertainties"]["excluded_important_files"]}  # v1.1: phantom IDs never enter the packet, not even via teaching-claim evidence lists
    roles = sorted(bundle["module_role_ids"], key=lambda r: -(r.get("incoming_calls", 0) + r.get("outgoing_calls", 0)))[:10]
    paths = [{"id": p["id"], "entrypoint": f"{p['entrypoint_path']}:{p.get('entrypoint_symbol')}", "steps": [
        f"{s.get('source_path')}:{s.get('source_symbol') or ''} -> {s.get('target_path') or s.get('boundary_target') or s.get('target_symbol')}:{s.get('target_symbol') or ''} ({s.get('resolution')})" for s in p.get("steps", [])[:6]],
        "terminal_reason": p.get("terminal_reason"), "truncated": p.get("truncated")} for p in bundle["execution_paths"][:6]]
    entry_symbols = {}
    for path in {r["path"] for r in roles}:
        syms = [s for s in bundle["symbols"] if s["path"] == path and s.get("type") in ("class", "function")]
        entry_symbols[path] = [{"id": s["id"], "type": s["type"], "symbol": s["symbol"], "lines": f"{s['start_line']}-{s['end_line']}"} for s in syms[:12]]
    return {
        "task": "write_overview_asset",
        "packet_version": "overview-selector/v1",
        "repository": {"canonical_source_url": bundle["source"]["repository"], "analyzed_revision": bundle["source"]["revision"],
                       "analyzer": bundle["analyzer"], "license_state": license_state},
        "platform_metadata": bundle["platform_metadata"],
        "repository_summary": bundle["repository_summary"],
        "important_files": [{"id": f["id"], "path": f["path"], "observation": f["observation"], "provenance": f["provenance"]} for f in bundle["important_files"]],
        "repository_level_evidence": [{"id": e["id"], "type": e.get("type"), "path": e.get("path"), "observation": e.get("observation"), "provenance": e.get("provenance"), "excerpt": (e.get("excerpt") or "")[:300] or None}
                                      for e in bundle["evidence"] if e["id"].startswith(("ev_", "subsys_"))],
        "entrypoints": [{"id": e["id"], "path": e["path"], "symbol": e.get("symbol"), "observation": e.get("observation"), "excerpt": e.get("excerpt"), "provenance": e.get("provenance")} for e in bundle["entrypoints"]],
        "modules": bundle["modules"],
        "module_roles_by_static_call_degree": [{"id": r["id"], "path": r["path"], "role": r["role"], "incoming_calls": r["incoming_calls"], "outgoing_calls": r["outgoing_calls"], "evidence_basis": r.get("evidence_basis")} for r in roles],
        "core_flow_static": recon.get("core_flow", [])[:12],
        "boundaries_static": recon.get("boundaries", [])[:12],
        "execution_paths_static": paths,
        "selected_symbols_by_file": entry_symbols,
        "resolved_relation_summary_sample": bundle["architecture"]["resolved_relation_summary"][:20],
        "relation_counts": bundle["architecture"]["relation_counts"],
        "dependencies_partial": bundle["dependency_records"],
        "tests": {"count": len(bundle["tests"]), "sample": bundle["tests"][:10]},
        "files": {"count": len(bundle["files"]), "top_level_directories": bundle["repository_summary"]["top_level_directories"], "sample": sample_files(bundle["files"])},
        "teaching_claims": [{"id": c["id"], "section": c.get("section"), "text": c["text"], "evidence_ids": [e for e in c.get("evidence_ids", []) if e not in excluded], "provenance": c.get("provenance"), "status": c.get("status")} for c in bundle["teaching_claims"]],
        "runtime_hints_partial": bundle["runtime_hints_partial"],
        "uncertainties": bundle["uncertainties"],
        "grounding_id_note": "Cite only IDs that appear in this packet: important_*, entry_*, py_*, exec_*, claim_*, meta_*, dep_* (dependency records), lim_* (analyzer limitations), role_* (module roles, inferred), relation_counts, summary_repository, architecture_reconstruction, subsys_*, ev_*.",
    }


def architecture_packet(bundle: dict, license_state: dict) -> dict:
    """ArchitectureSelector (Paper 04 §73; asset type `architecture`): entrypoints, static execution paths
    with their steps, module roles by static call degree, the reconstruction's core flow and boundaries,
    the resolved-relation summary, relation counts, subsystem evidence and the analyzer's limits. No
    symbol-level excerpts beyond entrypoints; the writer explains shape and control flow, not code."""
    recon = bundle["architecture"]["reconstruction"]
    roles = sorted(bundle["module_role_ids"], key=lambda r: -(r.get("incoming_calls", 0) + r.get("outgoing_calls", 0)))[:20]
    paths = [{"id": p["id"], "entrypoint": f"{p['entrypoint_path']}:{p.get('entrypoint_symbol')}", "terminal_reason": p.get("terminal_reason"), "truncated": p.get("truncated"), "cycle_detected": p.get("cycle_detected"),
              "steps": [f"{s.get('source_path')}:{s.get('source_symbol') or ''} -> {s.get('target_path') or s.get('boundary_target') or s.get('target_symbol')}:{s.get('target_symbol') or ''} ({s.get('resolution')})" for s in p.get("steps", [])[:8]]}
             for p in bundle["execution_paths"][:10]]
    excluded = {x["excluded_id"] for x in bundle["uncertainties"]["excluded_important_files"]}
    return {
        "task": "write_architecture_asset",
        "packet_version": "architecture-selector/v1",
        "repository": {"canonical_source_url": bundle["source"]["repository"], "analyzed_revision": bundle["source"]["revision"], "analyzer": bundle["analyzer"], "license_state": license_state},
        "platform_metadata": [g for g in bundle["platform_metadata"] if g["id"] in ("meta_description", "meta_primary_language", "meta_topics")],
        "repository_summary": bundle["repository_summary"],
        "important_files": [{"id": f["id"], "path": f["path"], "observation": f["observation"], "provenance": f["provenance"]} for f in bundle["important_files"]],
        "subsystems": [{"id": e["id"], "path": e.get("path"), "observation": e.get("observation"), "provenance": e.get("provenance")} for e in bundle["evidence"] if e["id"].startswith("subsys_")],
        "modules": bundle["modules"],
        "entrypoints": [{"id": e["id"], "path": e["path"], "symbol": e.get("symbol"), "observation": e.get("observation"), "excerpt": e.get("excerpt"), "provenance": e.get("provenance")} for e in bundle["entrypoints"]],
        "module_roles_by_static_call_degree": [{"id": r["id"], "path": r["path"], "role": r["role"], "incoming_calls": r["incoming_calls"], "outgoing_calls": r["outgoing_calls"], "evidence_basis": r.get("evidence_basis")} for r in roles],
        "reconstruction_overview_static": recon.get("overview"),
        "core_flow_static": recon.get("core_flow", [])[:16],
        "boundaries_static": recon.get("boundaries", [])[:20],
        "execution_paths_static": paths,
        "execution_flow_notes": bundle["architecture"]["execution_flow"][:8],
        "data_flow_notes": bundle["architecture"]["data_flow"][:8],
        "resolved_relation_summary_sample": bundle["architecture"]["resolved_relation_summary"][:30],
        "relation_counts": bundle["architecture"]["relation_counts"],
        "dependencies_partial": bundle["dependency_records"],
        "tests": {"count": len(bundle["tests"]), "sample": bundle["tests"][:8]},
        "files": {"count": len(bundle["files"]), "top_level_directories": bundle["repository_summary"]["top_level_directories"]},
        "teaching_claims": [{"id": c["id"], "section": c.get("section"), "text": c["text"], "evidence_ids": [e for e in c.get("evidence_ids", []) if e not in excluded], "provenance": c.get("provenance"), "status": c.get("status")} for c in bundle["teaching_claims"]],
        "uncertainties": bundle["uncertainties"],
        "grounding_id_note": "Cite only IDs that appear in this packet: important_*, entry_*, py_*, exec_*, claim_*, meta_*, dep_* (dependency records), lim_* (analyzer limitations), role_* (module roles, inferred), relation_counts, summary_repository, architecture_reconstruction, subsys_*, ev_*.",
    }


def taxonomy_packet(bundle: dict, candidates: list[str], taxonomy: list[dict]) -> dict:
    return {
        "task": "classify_repository_taxonomy_v1",
        "packet_version": "taxonomy-selector/v1",
        "repository": {"canonical_source_url": bundle["source"]["repository"], "analyzed_revision": bundle["source"]["revision"]},
        "platform_metadata": bundle["platform_metadata"],
        "repository_summary": bundle["repository_summary"],
        "important_files": [f["path"] for f in bundle["important_files"]],
        "entrypoints": [f"{e['path']}:{e.get('symbol')}" for e in bundle["entrypoints"]],
        "module_roles": [{"path": r["path"], "role": r["role"]} for r in bundle["architecture"]["reconstruction"].get("module_roles", [])[:10]],
        "dependencies_partial": [d["name"] for d in bundle["dependencies"]],
        "rule_based_candidate_categories": candidates,
        "taxonomy_v1": taxonomy,
    }


def main(slug: str) -> int:
    sdir = slice_dir(slug)
    reg = load_json(sdir / "registration-receipt.json")
    pending = sdir / "artifacts" / "repolumen" / "pending"
    repo_id, rev_id, sha = reg["repository_id"], reg["revision_id"], reg["commit_sha"]
    # Two-phase artifact commit: temp → hash → validate → durable → SEDB (Paper 04 §131).
    # Re-runs are idempotent: once the artifact is durable, the pending copy no longer exists.
    durable = sdir / "artifacts" / "repolumen" / repo_id / sha / ENGINE_VERSION
    durable.mkdir(parents=True, exist_ok=True)
    manifest_path = durable / "semantic-manifest.json"
    receipt = load_json((pending if (pending / "analysis-receipt.json").exists() else durable) / "analysis-receipt.json")
    assert receipt["schema_valid"], "manifest failed schema validation; refusing to commit"
    assert receipt["revision"] == reg["commit_sha"], "analyzed revision differs from registered revision"
    if not manifest_path.exists():
        shutil.move(str(pending / "semantic-manifest.json"), str(manifest_path))
        shutil.move(str(pending / "analysis-receipt.json"), str(durable / "analysis-receipt.json"))
    assert sha256_file(manifest_path) == receipt["manifest_sha256"], "artifact hash drift"
    m = load_json(manifest_path)
    # Analysis identity: repository, revision, analyzer version and result-affecting configuration —
    # which, for bounded manifests, includes the analyzer's bounds (uncertainty.analysis_bounds).
    analysis_cfg, config_hash = analysis_config(m)
    run_id = analysis_run_id(repo_id, sha, ENGINE_VERSION, config_hash)

    gh = load_json(sdir / "artifacts" / "github" / "repo.json")
    languages = load_json(sdir / "artifacts" / "github" / "languages.json")
    metadata = {"description": gh.get("description"), "homepage": gh.get("homepage"), "topics": gh.get("topics", []),
                "language": gh.get("language"), "languages": languages, "license_spdx": reg["license"]["spdx"],
                "latest_release_tag": None, "latest_release_at": None, "stars": gh.get("stargazers_count"), "observed_at": reg["observed_at"]}
    rel = load_json(sdir / "artifacts" / "github" / "releases.json")
    if isinstance(rel, list) and rel:
        metadata["latest_release_tag"], metadata["latest_release_at"] = rel[0].get("tag_name"), rel[0].get("published_at")

    # License agreement check between GitHub detection and RepoLumen detection.
    rl_license = m["source"].get("license")
    license_state = {"status": reg["license"]["status"], "spdx": reg["license"]["spdx"], "repolumen_detected": rl_license,
                     "agreement": (rl_license == reg["license"]["spdx"])}
    if not license_state["agreement"]:
        license_state["status"] = "unresolved"

    bundle = build_bundle(m, repo_id, rev_id, run_id, metadata)
    bdir = sdir / "artifacts" / "grounding" / run_id / PROJECTION_DIR
    bundle_hash = write_json(bdir / "grounding-bundle.json", bundle)
    bundle_bytes = (bdir / "grounding-bundle.json").stat().st_size

    counts = {k: len(v) for k, v in bundle.items() if isinstance(v, list)}
    counts["groundings"] = len(bundle["groundings"])
    store = open_store()
    # Analysis identity reuse (Paper 04 §132): same repository/revision/analyzer/config → the existing
    # passed run is canonical; re-runs only regenerate projections and packets.
    existing = store.get(run_id)
    analysis_mode = "reuse_existing"
    # Projection history (catalog contract 2026-09-14): one immutable af_grounding_projection row per
    # (analysis run, projector version). The run row keeps its original projection; a newer projector
    # version adds groundings beside it. The same version producing different bytes is a projector bug.
    proj_id = f"proj_{run_id}_{PROJECTION_DIR.replace('.', '_')}"
    proj_existing = store.get(proj_id)
    if proj_existing is not None:
        assert proj_existing["values"].get("af_grounding_bundle_sha256") == bundle_hash, "grounding projection changed at the same projector version; bump BUNDLE_VERSION"
    proj_records, supersedes = [], None
    if existing is not None:
        assert existing["values"].get("af_artifact_sha256") == receipt["manifest_sha256"], "existing analysis run points at a different manifest"
        if existing["values"].get("af_grounding_bundle_sha256") != bundle_hash:
            orig_ref = existing["values"].get("af_grounding_bundle_ref")
            orig_path = Path(__file__).resolve().parents[1] / orig_ref
            orig_version = load_json(orig_path)["bundle_version"] if orig_path.exists() else "ai-frontier-grounding/v1"
            assert orig_version != BUNDLE_VERSION, "grounding projection changed for an existing analysis run at the same projector version; bump BUNDLE_VERSION"
            supersedes = f"proj_{run_id}_{orig_version.rsplit('/', 1)[1].replace('.', '_')}"
            if store.get(supersedes) is None:  # backfill the run's original projection from its own immutable row
                proj_records.append({"entity_id": supersedes, "kind": "af_grounding_projection", "label": f"{orig_version} · {run_id}", "values": {
                    "af_analysis_run_id": run_id, "af_repository_id": repo_id, "af_revision_id": rev_id, "af_projection_version": orig_version,
                    "af_grounding_bundle_ref": orig_ref, "af_grounding_bundle_sha256": existing["values"]["af_grounding_bundle_sha256"],
                    "af_grounding_bundle_bytes": orig_path.stat().st_size if orig_path.exists() else None, "af_counts": existing["values"].get("af_counts"),
                    "af_supersedes_projection_id": None, "af_provenance_source": "repolumen", "af_created_at": existing["values"].get("af_created_at")}})
            analysis_mode = f"reuse_existing_new_projection (run recorded {orig_version}, now {BUNDLE_VERSION})"
        records = []
    else:
        analysis_mode = "new"
        records = [{"entity_id": run_id, "kind": "af_analysis_run", "label": f"{ENGINE} {ENGINE_VERSION} · {reg['commit_sha'][:12]}", "values": {
        "af_repository_id": repo_id, "af_revision_id": rev_id, "af_engine": ENGINE, "af_engine_version": ENGINE_VERSION,
        "af_manifest_schema_version": m.get("schema_version"), "af_analysis_mode": receipt["knowledge_cache"]["analysis_mode"],
        "af_external_provider": "disabled", "af_analysis_status": "passed", "af_analysis_config_hash": config_hash, "af_analysis_config": analysis_cfg,
        "af_artifact_ref": rel_ref(manifest_path), "af_artifact_sha256": receipt["manifest_sha256"], "af_artifact_bytes": receipt["manifest_bytes"],
        "af_started_at": receipt["started_at"], "af_elapsed_seconds": receipt["elapsed_seconds"],
        "af_grounding_bundle_ref": rel_ref(bdir / "grounding-bundle.json"), "af_grounding_bundle_sha256": bundle_hash,
        "af_capability_profile": CAPABILITY_PROFILE, "af_counts": counts, "af_limitations": ANALYZER_LIMITATIONS,
        "af_provenance_source": "repolumen", "af_created_at": receipt["started_at"],
    }}]
    # Existing runs: every grounding is written again; identical rows are no-ops (the store proves the new
    # projection is a superset), new IDs are appended, any drift in an old row is an ImmutableConflict.
    for local_id, g in (bundle["groundings"].items() if (existing is None or analysis_mode != "reuse_existing") else ()):
        records.append({"entity_id": f"gnd_{run_id}_{local_id}", "kind": "af_grounding", "label": f"{local_id} ({g['type']})", "values": {
            "af_analysis_run_id": run_id, "af_repository_id": repo_id, "af_revision_id": rev_id,
            "af_grounding_type": g["type"], "af_origin_engine": "github_api" if local_id.startswith("meta_") else ENGINE,
            "af_origin_local_id": local_id, "af_source_path": g.get("path"), "af_start_line": g.get("start_line"), "af_end_line": g.get("end_line"),
            "af_symbol": g.get("symbol"), "af_epistemic_status": g["epistemic_status"], "af_source_provenance": g.get("source_provenance"),
            "af_summary_text": (g.get("text") or "")[:400] or None, "af_provenance_source": "repolumen",
        }})
    if proj_existing is None:
        proj_records.append({"entity_id": proj_id, "kind": "af_grounding_projection", "label": f"{BUNDLE_VERSION} · {run_id}", "values": {
            "af_analysis_run_id": run_id, "af_repository_id": repo_id, "af_revision_id": rev_id, "af_projection_version": BUNDLE_VERSION,
            "af_grounding_bundle_ref": rel_ref(bdir / "grounding-bundle.json"), "af_grounding_bundle_sha256": bundle_hash, "af_grounding_bundle_bytes": bundle_bytes,
            "af_counts": counts, "af_supersedes_projection_id": supersedes, "af_provenance_source": "repolumen", "af_created_at": utc_now()}})
    records.extend(proj_records)
    if not license_state["agreement"] and store.get(f"lic_{rev_id}_repolumen") is None:  # immutable; re-runs must not rewrite it
        records.append({"entity_id": f"lic_{rev_id}_repolumen", "kind": "af_license_record", "label": "license disagreement", "values": {
            "af_repository_id": repo_id, "af_revision_id": rev_id, "af_detected_spdx": rl_license, "af_license_status": "unresolved",
            "af_license_source": "repolumen", "af_license_policy": "block_source_reproduction", "af_provenance_source": "repolumen", "af_observed_at": utc_now()}})

    result = store.write(records, source="repolumen", confidence=None) if records else None

    taxonomy = [{"slug": r["values"]["af_category_slug"], "name": r["values"]["af_display_name"], "parent": r["values"]["af_parent_category_id"]}
                for r in store.find("af_category")]
    packets = sdir / "packets"
    ov = overview_packet(bundle, license_state)
    tx = taxonomy_packet(bundle, reg["candidate_categories"], taxonomy)
    ar = architecture_packet(bundle, license_state)
    ov_hash = write_json(packets / "overview.json", ov)
    tx_hash = write_json(packets / "taxonomy.json", tx)
    ar_hash = write_json(packets / "architecture.json", ar)

    out = {"analysis_run_id": run_id, "projection_version": BUNDLE_VERSION, "projection_id": proj_id, "supersedes_projection_id": supersedes, "manifest_ref": rel_ref(manifest_path), "manifest_sha256": receipt["manifest_sha256"],
           "run_recorded_grounding_bundle_sha256": (existing or {"values": {}})["values"].get("af_grounding_bundle_sha256", bundle_hash),
           "excluded_important_files": bundle["uncertainties"]["excluded_important_files"],
           "grounding_bundle_ref": rel_ref(bdir / "grounding-bundle.json"), "grounding_bundle_sha256": bundle_hash, "grounding_bundle_bytes": bundle_bytes,
           "counts": counts, "license_state": license_state, "analysis_run_mode": analysis_mode,
           "sedb_write": (result.__dict__ if result else {"created_entities": 0, "unchanged_entities": 0, "updated_entities": 0, "written_cells": 0, "note": "existing analysis run reused"}),
           "packets": {"overview": {"sha256": ov_hash, "bytes": (packets / "overview.json").stat().st_size},
                       "taxonomy": {"sha256": tx_hash, "bytes": (packets / "taxonomy.json").stat().st_size},
                       "architecture": {"sha256": ar_hash, "bytes": (packets / "architecture.json").stat().st_size}},
           "bounded_context_ratio": round((packets / "overview.json").stat().st_size / receipt["manifest_bytes"], 5)}
    write_json(sdir / "grounding-receipt.json", out)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
