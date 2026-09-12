"""Deterministic MockProvider for the worker loop (Paper 05 §156: MockProvider is an MVP adapter).

SYNTHETIC. Nothing here is a model. The mock derives its "draft" mechanically
from the grounding packet so that the orchestration, deterministic validators,
verifier/critic plumbing, canonical Markdown commit, SEDB provenance records and
the page render can be exercised while the real provider route is blocked.
Every record it produces is labelled `model_provider = "mock"` and the canonical
Markdown carries a SYNTHETIC disclosure. Mock output must never be published or
mistaken for GLM output.
"""
from __future__ import annotations

import json

from af_common import canonical_bytes, sha256_bytes, utc_now, write_json

MODEL_PROVIDER, MODEL_NAME = "mock", "deterministic-mock/v1"


def _input(task: dict, name: str) -> str:
    return next(i["content"] for i in task["inputs"] if i["name"] == name)


def mock_taxonomy(packet: dict) -> dict:
    cands = packet["rule_based_candidate_categories"]
    primary = "cli-tools" if "cli-tools" in cands else cands[0]
    return {"primary": primary, "secondary": [c for c in ("artificial-intelligence", "llm-serving") if c in cands][:2],
            "confidence": 0.5, "evidence": ["meta_description", "meta_topics"], "uncertainty": "SYNTHETIC mock assignment derived from rule-based candidates only.",
            "taxonomy_review_required": True}


def mock_writer(packet: dict) -> dict:
    meta = {g["id"]: g for g in packet["platform_metadata"]}
    files = packet["important_files"]
    entries = packet["entrypoints"]
    roles = packet["module_roles_by_static_call_degree"]
    claims, sections = [], []
    def claim(cid, sid, text, refs, status):
        claims.append({"claim_id": cid, "section_id": sid, "text": text, "grounding_refs": refs, "epistemic_status": status})
        return cid
    c1 = claim("c1", "what_it_is", f"The repository owner describes it as: {meta['meta_description']['text']}.", ["meta_description"], "author_claimed")
    c2 = claim("c2", "what_it_is", f"The platform reports {meta['meta_primary_language']['text']} as the primary language and the topics {meta['meta_topics']['text']}.", ["meta_primary_language", "meta_topics"], "observed")
    c3 = claim("c3", "what_it_is", f"The analyzer inventoried {packet['files']['count']} files under the top-level directories {', '.join(packet['files']['top_level_directories'])}.", ["summary_repository"], "inferred")
    e = entries[0]
    c4 = claim("c4", "how_it_starts", f"{e['path']} contains a Python __main__ execution guard.", [e["id"]], "observed")
    c5 = claim("c5", "how_it_starts", f"A bounded static execution path leads from {packet['execution_paths_static'][0]['entrypoint']} into the CLI module.", [packet["execution_paths_static"][0]["id"]], "inferred")
    top = roles[0]
    c6 = claim("c6", "structure", f"{top['path']} has the highest static call degree ({top['incoming_calls']} incoming, {top['outgoing_calls']} outgoing) and is classified as a {top['role']}.", ["architecture_reconstruction"], "inferred")
    c7 = claim("c7", "structure", f"Project-level important files include {', '.join(f['path'] for f in files[:4])}.", [f["id"] for f in files[:4]], "observed")
    deps = packet["dependencies_partial"]
    rle = {e["type"]: e["id"] for e in packet.get("repository_level_evidence", [])}
    c8 = claim("c8", "dependencies_and_tests", f"Dependency evidence covers only {deps[0]['source_path']} ({len(deps)} records); pyproject.toml dependency tables are not parsed by this analyzer version.", [rle.get("manifest_summary", "ev_manifest_1")], "observed")
    c9 = claim("c9", "dependencies_and_tests", f"{packet['tests']['count']} test-like files were detected.", [rle.get("test_summary", "ev_tests_1")], "observed")
    c10 = claim("c10", "limits_of_this_analysis", f"{packet['relation_counts']['by_resolution'].get('external_or_unresolved', 0)} static relations remain external or unresolved; runtime behaviour is not established.", ["architecture_reconstruction"], "inferred")
    sections = [
        {"id": "what_it_is", "heading": "What it is", "markdown": f"{claims[0]['text']} {claims[1]['text']} {claims[2]['text']}", "claim_ids": [c1, c2, c3]},
        {"id": "how_it_starts", "heading": "How it starts", "markdown": f"{claims[3]['text']} {claims[4]['text']}", "claim_ids": [c4, c5]},
        {"id": "structure", "heading": "Structure at a glance", "markdown": f"{claims[5]['text']} {claims[6]['text']}", "claim_ids": [c6, c7]},
        {"id": "dependencies_and_tests", "heading": "Dependencies and tests", "markdown": f"{claims[7]['text']} {claims[8]['text']}", "claim_ids": [c8, c9]},
        {"id": "limits_of_this_analysis", "heading": "Limits of this analysis", "markdown": claims[9]["text"], "claim_ids": [c10]},
    ]
    return {"status": "draft_ready", "asset_type": "overview", "title": "SYNTHETIC MOCK: simonw/llm repository overview",
            "summary": "SYNTHETIC mock draft assembled mechanically from grounding-packet facts to exercise the pipeline; not model-written.",
            "summary_claim_ids": [c1, c4], "sections": sections, "claims": claims,
            "uncertainties": ["Mock run: no model judgement was applied."], "questions": []}


def mock_revision(draft: dict, fixes: list[dict]) -> dict:
    """Targeted revision: remove the claims the fixes name; rebuild section text from remaining claims."""
    drop = {f["target"] for f in fixes if f.get("target", "").startswith("c")}  # the mock cannot reword; every targeted claim is dropped
    claims = [c for c in draft["claims"] if c["claim_id"] not in drop]
    by_id = {c["claim_id"]: c for c in claims}
    sections = []
    for s in draft["sections"]:
        ids = [c for c in s["claim_ids"] if c in by_id]
        if not ids:
            continue
        sections.append(dict(s, claim_ids=ids, markdown=" ".join(by_id[c]["text"] for c in ids)))
    return dict(draft, claims=claims, sections=sections, summary_claim_ids=[c for c in draft["summary_claim_ids"] if c in by_id],
                uncertainties=draft.get("uncertainties", []) + [f"Mock targeted revision removed claims: {sorted(drop)}"] if drop else draft.get("uncertainties", []))


def mock_verifier(packet: dict, draft: dict) -> dict:
    ids = set()
    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("id"), str): ids.add(o["id"])
            for k, v in o.items():
                if k == "evidence_ids": ids.update(v)
                walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    walk(packet); ids.update({"summary_repository", "architecture_reconstruction"})
    checks = []
    for c in draft["claims"]:
        ok = all(r in ids for r in c["grounding_refs"])
        checks.append({"claim_id": c["claim_id"], "status": "supported" if ok else "unsupported", "grounding_ok": ok, "epistemic_ok": True, "notes": "mock: ID existence only"})
    unsupported = [c["claim_id"] for c in checks if c["status"] != "supported"]
    return {"status": "pass" if not unsupported else "fail", "claim_checks": checks, "unsupported_claims": unsupported, "revision_conflicts": [], "knowledge_leakage": [],
            "grounding_errors": [], "hallucinated_names": [], "rights_issues": [], "required_fixes": [{"target": u, "action": "remove", "instruction": "mock"} for u in unsupported],
            "summary": "SYNTHETIC mock verification: grounding ID existence only; no semantic support check."}


def mock_critic(draft: dict) -> dict:
    return {"status": "reviewed", "scores": {"structure": 0.5, "redundancy": 0.5, "coherence": 0.5, "scope_discipline": 0.5, "beginner_readability": 0.5},
            "issues": [{"severity": "low", "section_id": None, "issue": "mock run", "suggestion": "no judgement applied"}], "overclaim_flags": [], "wording_suggestions": [], "no_new_technical_facts": True}


def mock_seo(draft: dict) -> dict:
    return {"title_candidates": ["SYNTHETIC MOCK: simonw/llm repository overview"], "meta_description": "Synthetic mock metadata produced to exercise the AI Frontier pipeline; replaced by model output when the provider route is reconciled and rerun.", "keywords": ["mock"]}


def dispatch(task: dict, task_dir: Path, *, attempt: int, log) -> dict:  # same signature as macr_worker.dispatch
    task_dir.mkdir(parents=True, exist_ok=True)
    write_json(task_dir / f"{task['task_id']}.mock.json", task)
    contract = task["goal"]
    started = utc_now()
    if "taxonomy" in task["task_id"]:
        out = mock_taxonomy(json.loads(_input(task, "taxonomy_packet")))
    elif "verifier" in task["task_id"]:
        out = mock_verifier(json.loads(_input(task, "grounding_packet")), json.loads(_input(task, "draft")))
    elif "critic" in task["task_id"]:
        out = mock_critic(json.loads(_input(task, "draft")))
    elif "seo" in task["task_id"]:
        out = mock_seo(json.loads(_input(task, "validated_draft")))
    elif "revision" in task["task_id"]:
        out = mock_revision(json.loads(_input(task, "previous_draft")), json.loads(_input(task, "required_fixes")))
    else:
        out = mock_writer(json.loads(_input(task, "grounding_packet")))
    raw = canonical_bytes(out)
    log(f"  [MOCK] synthetic output for {task['task_id']} ({len(raw)} bytes)")
    return {"task_id": task["task_id"], "attempt": attempt, "provider_id": "mock", "model_provider": MODEL_PROVIDER, "model_name": MODEL_NAME,
            "started_at": started, "completed_at": utc_now(), "stages": [{"stage": "mock", "exit": 0, "result": "synthetic"}], "status": "candidate_success",
            "answer_sha256": sha256_bytes(raw), "answer_repaired": False, "output": out, "cost": {"known_cost_usd": 0.0, "note": "mock: no provider call"},
            "provider_meta": {"mock": True}, "input_sha256": sha256_bytes(canonical_bytes(task["inputs"])), "latency_seconds": 0.0,
            "macr_candidate_id": None, "conservative_cost_ceiling_usd": 0.0, "request_bytes": len(canonical_bytes(task))}


from pathlib import Path  # noqa: E402  (used in the signature above)
