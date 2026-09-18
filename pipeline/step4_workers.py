"""Step 4 — cheap-model worker loop through MACR (Writer → Verifier → targeted revision → Critic → SEO), plus the taxonomy classifier.

Budgets (Paper 05 §29, MVP): writer 2 attempts (initial + one targeted revision),
verifier 2, critic 1, formatter/SEO 1, taxonomy 1. Hard stop after that: the
asset is marked `escalation_required`, nothing is committed as canonical.

Deterministic checks run BEFORE the model verifier (Paper 05 §42): grounding ID
existence, epistemic upgrade, section/claim consistency, hallucinated path
names, quote length.

Usage: python step4_workers.py <slice_slug>
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import os
import signal

from af_common import asset_paths, load_json, open_store, rel_ref, slice_dir, utc_now, write_json
from macr_worker import build_task, validate_output, worker_records
from prompts import ASSET_CONTRACTS, CRITIC_V, REVISION_V, VERIFIER_V, WRITER_V, contract_hash, GUIDE_PHRASES, SEO_V

# Paper 05 §29 MVP budget (writer 2, verifier 2) with one extra writer attempt reserved for a
# deterministic-check revision, so a verifier-driven or critic-driven revision is still possible
# after it (run 2 on 2026-09-12 exhausted the writer budget on deterministic fixes alone).
WRITER_MAX, VERIFIER_MAX = 3, 2
BACKEND = os.environ.get("AF_WORKER_BACKEND", "macr")  # "macr" (real GLM through MACR) or "mock" (SYNTHETIC, pipeline exercise only)
if BACKEND == "mock":
    from mock_worker import dispatch, MODEL_NAME as BACKEND_MODEL
else:
    from macr_worker import dispatch, MODEL_NAME as BACKEND_MODEL


# Graceful stop (MACR maintainer guidance, 2026-09-14): a CTRL_BREAK_EVENT reaches this process and its MACR
# child together. The child finishes or unwinds and releases its lease in its own `finally`; we only refuse to
# start the next dispatch. Never kill the child.
STOP_REQUESTED = False


class BatchStopped(Exception):
    pass


def _on_break(signum, frame):
    global STOP_REQUESTED
    STOP_REQUESTED = True


if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, _on_break)


def packet_ids(packet: dict) -> set[str]:
    ids = {"summary_repository", "architecture_reconstruction"}
    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("id"), str):
                ids.add(o["id"])
            for k, v in o.items():
                if k == "evidence_ids" and isinstance(v, list):
                    ids.update(x for x in v if isinstance(x, str))
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(packet)
    return ids


def deterministic_checks(draft: dict, packet: dict, catalog: dict, files: set[str]) -> dict:
    """Paper 05 §74 deterministic validators on a writer draft. Returns {ok, failures, fixes}."""
    ids = packet_ids(packet)
    failures, fixes = [], []
    claims = {c["claim_id"]: c for c in draft.get("claims", [])}
    section_ids = {s["id"] for s in draft.get("sections", [])}
    for cid, c in claims.items():
        refs = c.get("grounding_refs") or []
        if not refs:
            failures.append(f"{cid}: no grounding_refs"); fixes.append({"target": cid, "action": "remove", "instruction": "claim has no grounding refs; remove it or attach IDs that exist in the packet"}); continue
        unknown = [r for r in refs if r not in ids]
        if unknown:
            failures.append(f"{cid}: unknown grounding ids {unknown}"); fixes.append({"target": cid, "action": "add_grounding", "instruction": f"grounding ids {unknown} do not exist in the packet; cite only packet IDs or remove the claim"})
        nocat = [r for r in refs if r in ids and r not in catalog]  # v1.1: every citable ID must carry a catalog state (Paper 04 §47)
        if nocat:
            failures.append(f"{cid}: grounding ids {nocat} have no catalog state"); fixes.append({"target": cid, "action": "add_grounding", "instruction": f"grounding ids {nocat} are not in the grounding catalog; cite other packet IDs or remove the claim"})
        if c.get("section_id") not in section_ids:  # 2026-09-14 smolagents: claims filed under "summary"; without a fix entry the revision could not repair it
            failures.append(f"{cid}: section_id {c.get('section_id')} not in sections"); fixes.append({"target": cid, "action": "reword", "instruction": f"section_id '{c.get('section_id')}' is not a section id; set it to the section whose body states this claim (a claim used by the summary still belongs to a section and is listed in summary_claim_ids)"})
        # epistemic upgrade check against the grounding catalog states
        states = {catalog.get(r, {}).get("epistemic_status") for r in refs if r in catalog}
        st = c.get("epistemic_status")
        # v1.1 (2026-09-13): an observed claim must rest on observed groundings only. Run 2's c30/c31 marked
        # execution paths observed while co-citing one observed file ID; the old any-observed-anchor rule let
        # that through and the model verifier then rejected it.
        weaker = sorted(f"{r} ({catalog[r].get('epistemic_status')})" for r in refs if r in catalog and catalog[r].get("epistemic_status") != "observed")
        if st == "observed" and weaker:
            failures.append(f"{cid}: marked observed but cites non-observed groundings {weaker}"); fixes.append({"target": cid, "action": "downgrade_status", "instruction": f"marked observed but cites {weaker}; downgrade epistemic_status to inferred (author_claimed if only metadata/README text remains), or drop those refs if the observed text stands without them"})
        if st in ("observed", "inferred") and states and states <= {"author_claimed"}:
            failures.append(f"{cid}: marked {st} but only author-claimed groundings cited"); fixes.append({"target": cid, "action": "downgrade_status", "instruction": "only author-claimed metadata is cited; set epistemic_status to author_claimed"})
        # hallucinated path names
        for p in re.findall(r"\b[\w./-]+\.(?:py|toml|md|txt|yml|yaml|json|sh|cfg|ini)\b", c.get("text", "")):
            p = p.strip("./")
            if p and p not in files and not any(f.endswith("/" + p) for f in files):
                failures.append(f"{cid}: path '{p}' not in the analyzed file inventory"); fixes.append({"target": cid, "action": "reword", "instruction": f"'{p}' is not a file in the analyzed revision; remove or correct it"})
    for s in draft.get("sections", []):
        missing = [c for c in s.get("claim_ids", []) if c not in claims]
        if missing:
            failures.append(f"section {s['id']}: unknown claim ids {missing}")
        if len(s.get("markdown", "").split()) > 260:
            failures.append(f"section {s['id']}: over 260 words"); fixes.append({"target": s["id"], "action": "reword", "instruction": "shorten this section to at most 180 words"})
        quotes = re.findall(r"```.*?```", s.get("markdown", ""), flags=re.S)
        for q in quotes:
            if q.count("\n") > 4:
                failures.append(f"section {s['id']}: code quote longer than 3 lines"); fixes.append({"target": s["id"], "action": "reword", "instruction": "code quotes must be at most 3 lines"})
    missing_summary = [c for c in draft.get("summary_claim_ids", []) if c not in claims]
    if missing_summary:
        failures.append(f"summary: unknown claim ids {missing_summary}")
    # dedupe fixes by target/action
    seen, uniq = set(), []
    for f in fixes:
        k = (f["target"], f["action"])
        if k not in seen:
            seen.add(k); uniq.append(f)
    return {"ok": not failures, "failures": failures, "fixes": uniq, "claims": len(claims), "packet_ids": len(ids)}


def main(slug: str, asset_type: str = "overview", seo_only: bool = False) -> int:
    sdir = slice_dir(slug)
    ap = asset_paths(sdir, asset_type)
    reg = load_json(sdir / "registration-receipt.json")
    gr = load_json(sdir / "grounding-receipt.json")
    ov_packet = load_json(ap["packet"])
    tx_packet = load_json(sdir / "packets" / "taxonomy.json")
    bundle = load_json(sdir / gr["grounding_bundle_ref"].split("/", 1)[1])
    catalog, files = bundle["groundings"], set(bundle["files"])
    repo_id, rev_id, run_id, bundle_sha = reg["repository_id"], reg["revision_id"], gr["analysis_run_id"], gr["grounding_bundle_sha256"]
    tasks_dir, runs_dir = sdir / "tasks", ap["runs_dir"]
    runs_dir.mkdir(parents=True, exist_ok=True)
    logf = open(ap["log"], "a", encoding="utf-8")
    def log(msg):
        line = f"[{utc_now()}] {msg}"; print(line, flush=True); logf.write(line + "\n"); logf.flush()

    store = open_store()
    receipt = {"slice": slug, "asset_type": asset_type, "backend": BACKEND, "model": BACKEND_MODEL, "synthetic": BACKEND == "mock", "started_at": utc_now(), "runs": [],
               "budget": {"writer": WRITER_MAX, "verifier": VERIFIER_MAX, "critic": 1, "seo": 1, "taxonomy": 1}}
    log(f"worker backend: {BACKEND} ({BACKEND_MODEL}){' — SYNTHETIC, not a model' if BACKEND == 'mock' else ''}")
    sedb_records = []

    def run_worker(role, contract_version, inputs, attempt, task_suffix, asset_type, max_attempts, validator=None):
        if STOP_REQUESTED:
            raise BatchStopped(f"stop requested before {role} attempt {attempt}")
        task_id = f"af-{slug}-{ap['task_prefix']}{task_suffix}-{attempt:02d}"  # slice-unique: repetitions of the same packet get distinct MACR tasks and SEDB task rows
        task = build_task(task_id, contract_version, inputs)
        log(f"{role} attempt {attempt}: dispatch {task_id} ({sum(len(i[1]) for i in inputs)} input chars)")
        rec = dispatch(task, tasks_dir, attempt=attempt, log=log)
        rec["budget_max_cost_usd"] = task["constraints"]["max_cost_usd"]
        rec["role"], rec["contract_version"] = role, contract_version
        schema_errors = validate_output(contract_version, rec["output"]) if rec.get("output") else ["no output"]
        rec["schema_errors"] = schema_errors
        if rec.get("output") and schema_errors:
            rec["status"] = "schema_invalid"
        if rec.get("output") and not schema_errors and validator:
            extra = validator(rec["output"])
            rec["semantic_errors"] = extra
            if extra:
                rec["status"] = "schema_invalid"
        out_path = runs_dir / f"{task_suffix}-{attempt:02d}.json"
        write_json(out_path, rec)
        rec["output_ref"] = rel_ref(out_path)
        log(f"{role} attempt {attempt}: status={rec['status']} failure={rec.get('failure')} latency={rec.get('latency_seconds')}s cost={rec.get('cost')} schema_errors={len(schema_errors) if rec.get('output') else 'n/a'}")
        summary = None
        if rec.get("output"):
            o = rec["output"]
            summary = {k: o.get(k) for k in ("status", "primary", "secondary", "confidence") if k in o}
            if "claims" in o:
                summary["claims"] = len(o["claims"]); summary["sections"] = [s["id"] for s in o.get("sections", [])]
            if "claim_checks" in o:
                summary["claim_checks"] = {s: sum(1 for c in o["claim_checks"] if c["status"] == s) for s in ("supported", "partially_supported", "unsupported", "unresolved", "conflicting")}
                summary["required_fixes"] = len(o.get("required_fixes", []))
            if "scores" in o:
                summary["scores"] = o["scores"]; summary["overclaim_flags"] = len(o.get("overclaim_flags", []))
        sedb_records.extend(worker_records("af", f"task_{task_id}", rec, role=role, contract_version=contract_version, repository_id=repo_id, revision_id=rev_id,
                                           analysis_run_id=run_id, bundle_sha256=bundle_sha, asset_type=asset_type, max_attempts=max_attempts, structured_result=summary))
        receipt["runs"].append({"role": role, "attempt": attempt, "task_id": task_id, "status": rec["status"], "failure": rec.get("failure"), "latency_seconds": rec.get("latency_seconds"),
                                "worker_run_entity_id": rec.get("worker_run_entity_id"),
                                "cost": rec.get("cost"), "conservative_cost_ceiling_usd": rec.get("conservative_cost_ceiling_usd"), "candidate": rec.get("macr_candidate_id"), "summary": summary, "output_ref": rec["output_ref"]})
        write_json(ap["workers_receipt"], receipt)
        return rec

    try:
        if seo_only:
            return _seo_only(ap, ov_packet, log, store, receipt, sedb_records, run_worker, asset_type)
        return _run(slug, sdir, reg, ov_packet, tx_packet, catalog, files, repo_id, rev_id, run_id, bundle_sha, tasks_dir, runs_dir, log, store, receipt, sedb_records, run_worker, asset_type, ap)
    except BatchStopped as stop:
        log(f"stopped by operator: {stop} — no new dispatch; in-flight MACR child was left to exit on its own")
        receipt["outcome"] = "stopped_by_operator"; receipt["completed_at"] = utc_now()
        write_json(ap["workers_receipt"], receipt)
        if sedb_records:
            store.write(sedb_records, source="worker")
        return 5


def seo_worker(run_worker, draft, ov_packet, asset_type, attempt):
    """SEO metadata worker (formatter family): title candidates, meta description, keywords. The guide type and its title phrase travel in the input (contract v1.1)."""
    payload = {"guide_type": asset_type, "guide_phrase": GUIDE_PHRASES[asset_type], "title": draft["title"], "summary": draft["summary"],
               "sections": [{"heading": s["heading"], "markdown": s["markdown"]} for s in draft["sections"]], "repository": ov_packet["repository"]["canonical_source_url"]}
    return run_worker("formatter", SEO_V, [("validated_draft", json.dumps(payload, ensure_ascii=False, indent=1))], attempt, "seo", asset_type, 1)


def _seo_only(ap, ov_packet, log, store, receipt, sedb_records, run_worker, asset_type):
    """Re-run only the SEO worker on an existing final-draft-bundle (contract bump); the draft, verifier and critic results are untouched."""
    bundle_path = ap["runs_dir"] / "final-draft-bundle.json"
    final = load_json(bundle_path)
    prior = load_json(ap["workers_receipt"]) if ap["workers_receipt"].exists() else {"runs": []}
    receipt["runs"] = list(prior.get("runs", []))
    receipt["seo_rerun_of"] = {"previous_outcome": prior.get("outcome"), "previous_seo_contract": next((r.get("contract_version") for r in reversed(receipt["runs"]) if r["role"] == "formatter"), None)}
    for k in ("outcome", "deterministic_check_1", "deterministic_check_2", "verifier_verdict"):
        if k in prior: receipt[k] = prior[k]
    attempt = sum(1 for r in receipt["runs"] if r["role"] == "formatter") + 1
    log(f"SEO-only rerun with {SEO_V} (attempt {attempt}); draft/verifier/critic unchanged")
    seo = seo_worker(run_worker, final["draft"], ov_packet, asset_type, attempt)
    if seo["status"] != "candidate_success":
        log(f"SEO rerun failed: {seo['status']} — bundle left unchanged"); receipt["completed_at"] = utc_now(); write_json(ap["workers_receipt"], receipt); store.write(sedb_records, source="worker"); return 4
    final["seo"], final["seo_contract"] = seo["output"], SEO_V
    final["worker_run_ids"] = [x["worker_run_entity_id"] for x in receipt["runs"] if x.get("worker_run_entity_id")]
    write_json(bundle_path, final)
    receipt["completed_at"] = utc_now(); write_json(ap["workers_receipt"], receipt)
    log(f"SEDB: {store.write(sedb_records, source='worker')}")
    log(f"outcome: {receipt.get('outcome')} (seo rerun ok; titles: {seo['output']['title_candidates'][:2]})")
    return 0 if receipt.get("outcome") == "hard_gate_pass" else 3


def _run(slug, sdir, reg, ov_packet, tx_packet, catalog, files, repo_id, rev_id, run_id, bundle_sha, tasks_dir, runs_dir, log, store, receipt, sedb_records, run_worker, asset_type="overview", ap=None):
    ap = ap or asset_paths(sdir, asset_type)
    writer_contract, revision_contract = ASSET_CONTRACTS[asset_type]
    # ---- taxonomy classifier (bounded discovery task; a repository property, so overview runs only)
    slugs = {t["slug"] for t in tx_packet["taxonomy_v1"]}
    def tx_validator(o):
        errs = []
        if o["primary"] not in slugs: errs.append(f"primary slug not in taxonomy: {o['primary']}")
        errs += [f"secondary slug not in taxonomy: {s}" for s in o.get("secondary", []) if s not in slugs]
        return errs
    tx = run_worker("taxonomy_classifier", "taxonomy_classifier/v1", [("taxonomy_packet", json.dumps(tx_packet, ensure_ascii=False, indent=1))], 1, "taxonomy", None, 2, tx_validator) if asset_type == "overview" else {"status": "skipped"}
    if tx["status"] == "schema_invalid":  # Paper 05 §71: one bounded retry on a schema envelope failure (run 2 omitted a required field)
        log(f"taxonomy: schema invalid ({tx.get('schema_errors')}); one retry")
        tx = run_worker("taxonomy_classifier", "taxonomy_classifier/v1", [("taxonomy_packet", json.dumps(tx_packet, ensure_ascii=False, indent=1))], 2, "taxonomy", None, 2, tx_validator)
    if tx["status"] == "candidate_success":
        o = tx["output"]
        cat_ids = {t["slug"]: f"cat_{t['slug'].replace('-', '_')}" for t in tx_packet["taxonomy_v1"]}
        wr = tx.get("worker_run_entity_id")
        for role, slug_c, conf in [("primary", o["primary"], o["confidence"])] + [("secondary", s, o["confidence"]) for s in o.get("secondary", [])]:
            sedb_records.append({"entity_id": f"rc_{repo_id}_{slug_c.replace('-', '_')}", "kind": "af_repository_category", "label": f"{reg['repository_id']} · {slug_c} ({role})", "values": {
                "af_repository_id": repo_id, "af_category_id": cat_ids[slug_c], "af_assignment_role": role, "af_confidence": conf, "af_assignment_source": "model",
                "af_validated": False, "af_taxonomy_version": "v1", "af_evidence_refs": o.get("evidence", []), "af_candidate_categories": reg["candidate_categories"],
                "af_uncertainty_note": o.get("uncertainty"), "af_worker_run_ids": [wr], "af_provenance_source": "worker", "af_observed_at": utc_now()}})
        receipt["taxonomy"] = {"primary": o["primary"], "secondary": o.get("secondary", []), "confidence": o["confidence"], "review_required": o.get("taxonomy_review_required")}

    # ---- writer loop
    packet_text = json.dumps(ov_packet, ensure_ascii=False, indent=1)
    writer_attempts = 0
    draft, draft_run = None, None
    w = run_worker("writer", writer_contract, [("grounding_packet", packet_text)], 1, "writer", asset_type, WRITER_MAX)
    writer_attempts += 1
    if w["status"] == "candidate_success":
        draft, draft_run = w["output"], w
    elif w["status"] == "schema_invalid" and writer_attempts < WRITER_MAX:
        log("writer: schema invalid; one full regeneration allowed (structure fundamentally wrong)")
        w = run_worker("writer", writer_contract, [("grounding_packet", packet_text)], 2, "writer", asset_type, WRITER_MAX)
        writer_attempts += 1
        if w["status"] == "candidate_success":
            draft, draft_run = w["output"], w
    if draft is None:
        receipt["outcome"] = "writer_failed"; receipt["completed_at"] = utc_now()
        write_json(ap["workers_receipt"], receipt); store.write(sedb_records, source="worker"); return 2

    det = deterministic_checks(draft, ov_packet, catalog, files)
    write_json(runs_dir / "deterministic-check-01.json", det)
    log(f"deterministic checks on draft: ok={det['ok']} failures={len(det['failures'])}")
    receipt["deterministic_check_1"] = {"ok": det["ok"], "failures": det["failures"][:20]}
    verifier_attempts = 0
    verdict = None
    fixes_source = None
    if not det["ok"] and writer_attempts < WRITER_MAX:
        fixes_source = {"source": "deterministic", "required_fixes": det["fixes"]}
    else:
        v = run_worker("verifier", VERIFIER_V, [("grounding_packet", packet_text), ("draft", json.dumps(draft, ensure_ascii=False, indent=1))], 1, "verifier", asset_type, VERIFIER_MAX)
        verifier_attempts += 1
        if v["status"] == "candidate_success":
            verdict = v["output"]
            if verdict["status"] != "pass" and writer_attempts < WRITER_MAX:
                fixes_source = {"source": "verifier", "required_fixes": verdict.get("required_fixes", [])}
    if fixes_source and writer_attempts < WRITER_MAX:
        log(f"targeted revision from {fixes_source['source']} ({len(fixes_source['required_fixes'])} fixes)")
        r = run_worker("writer", revision_contract, [("grounding_packet", packet_text), ("previous_draft", json.dumps(draft, ensure_ascii=False, indent=1)),
                                                                ("required_fixes", json.dumps(fixes_source["required_fixes"], ensure_ascii=False, indent=1))], 2, "writer-revision", asset_type, WRITER_MAX)
        writer_attempts += 1
        if r["status"] == "candidate_success":
            draft, draft_run = r["output"], r
            det2 = deterministic_checks(draft, ov_packet, catalog, files)
            write_json(runs_dir / "deterministic-check-02.json", det2)
            receipt["deterministic_check_2"] = {"ok": det2["ok"], "failures": det2["failures"][:20]}
            log(f"deterministic checks on revision: ok={det2['ok']} failures={len(det2['failures'])}")
            det = det2
        if verifier_attempts < VERIFIER_MAX:
            v = run_worker("verifier", VERIFIER_V, [("grounding_packet", packet_text), ("draft", json.dumps(draft, ensure_ascii=False, indent=1))], verifier_attempts + 1, "verifier", asset_type, VERIFIER_MAX)
            verifier_attempts += 1
            if v["status"] == "candidate_success":
                verdict = v["output"]
    receipt["verifier_verdict"] = None if verdict is None else {"status": verdict["status"], "summary": verdict.get("summary"), "unsupported": verdict.get("unsupported_claims"), "leakage": verdict.get("knowledge_leakage"), "fixes": len(verdict.get("required_fixes", []))}
    hard_ok = det["ok"] and verdict is not None and verdict["status"] == "pass"

    # ---- critic and SEO metadata (quality signals; never technical truth)
    critic = run_worker("critic", CRITIC_V, [("draft", json.dumps(draft, ensure_ascii=False, indent=1)), ("packet_summary", json.dumps({k: ov_packet[k] for k in ("repository_summary", "important_files", "entrypoints", "uncertainties") if k in ov_packet}, ensure_ascii=False, indent=1))], 1, "critic", asset_type, 1)
    seo = seo_worker(run_worker, draft, ov_packet, asset_type, 1)

    final = {"draft": draft, "draft_run_ref": draft_run["output_ref"], "verifier": verdict, "critic": critic.get("output"), "seo": seo.get("output"),
             "deterministic": det, "hard_gate_pass": hard_ok, "writer_attempts": writer_attempts, "verifier_attempts": verifier_attempts,
             "worker_run_ids": [x["worker_run_entity_id"] for x in receipt["runs"] if x.get("worker_run_entity_id")]}
    write_json(runs_dir / "final-draft-bundle.json", final)
    receipt["outcome"] = "hard_gate_pass" if hard_ok else "escalation_required"
    receipt["completed_at"] = utc_now()
    write_json(ap["workers_receipt"], receipt)
    res = store.write(sedb_records, source="worker")
    log(f"SEDB: {res}")
    log(f"outcome: {receipt['outcome']}")
    return 0 if hard_ok else 3


if __name__ == "__main__":
    if "--series" in sys.argv:
        os.environ["AF_SERIES"] = sys.argv[sys.argv.index("--series") + 1]
    _flag_values = {sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--series" and i + 1 < len(sys.argv)}
    _args = [a for a in sys.argv[1:] if not a.startswith("--") and a not in _flag_values]
    raise SystemExit(main(_args[0], _args[1] if len(_args) > 1 else "overview", seo_only="--seo-only" in sys.argv))
