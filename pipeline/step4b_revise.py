"""Step 4b — targeted revision within the Paper 05 budget + independent re-verification.

Two sources of fixes, both bounded by the step-4 budgets (writer 3 = 2 + one reserved for a
deterministic-check revision, verifier 2):

  critic   (default) — step 4 ended with a verifier pass but the critic flagged overclaims or
             medium/high wording issues; the revision is re-verified before it replaces the draft.
  verifier — step 4 ended with a verifier FAIL after its single in-loop revision was already spent
             on deterministic fixes; the verifier's own required_fixes drive one more revision.

If the revised draft fails deterministic checks or the second verifier, the previous draft is
kept (and stays unvalidated if it was), and the attempt is recorded as rejected. No budget
extension exists here: an exhausted budget prints budget_exhausted and exits 2 — that is the
escalation, a decision for a person.

Usage: python step4b_revise.py <slice_slug> [asset_type] [--source critic|verifier]
"""
from __future__ import annotations

import json
import sys

from af_common import asset_paths, load_json, open_store, rel_ref, slice_dir, utc_now, write_json
from macr_worker import build_task, dispatch, validate_output, worker_records
from prompts import ASSET_CONTRACTS, VERIFIER_V
from step4_workers import WRITER_MAX, VERIFIER_MAX, deterministic_checks


def main(slug: str, asset_type: str = "overview", source: str = "critic") -> int:
    sdir = slice_dir(slug)
    ap = asset_paths(sdir, asset_type)
    reg, gr = load_json(sdir / "registration-receipt.json"), load_json(sdir / "grounding-receipt.json")
    wr = load_json(ap["workers_receipt"])
    final = load_json(ap["runs_dir"] / "final-draft-bundle.json")
    ov_packet = load_json(ap["packet"])
    bundle = load_json(sdir / gr["grounding_bundle_ref"].split("/", 1)[1])
    catalog, files = bundle["groundings"], set(bundle["files"])
    repo_id, rev_id, run_id, bundle_sha = reg["repository_id"], reg["revision_id"], gr["analysis_run_id"], gr["grounding_bundle_sha256"]
    revision_contract = ASSET_CONTRACTS[asset_type][1]
    if final["writer_attempts"] >= WRITER_MAX or final["verifier_attempts"] >= VERIFIER_MAX:
        print(json.dumps({"status": "budget_exhausted", "writer_attempts": final["writer_attempts"], "verifier_attempts": final["verifier_attempts"]})); return 2
    if source == "verifier":
        verdict = final.get("verifier") or {}
        if verdict.get("status") == "pass":
            print(json.dumps({"status": "nothing_to_revise", "reason": "verifier already passed; use --source critic"})); return 0
        fixes = list(verdict.get("required_fixes", []))
    else:
        critic = final.get("critic") or {}
        fixes = [{"target": f["claim_id"], "action": "reword", "instruction": f"Critic overclaim flag: {f['reason']} Hedge or downgrade the wording; keep the same grounding refs."} for f in critic.get("overclaim_flags", [])]
        fixes += [{"target": i["section_id"], "action": "reword", "instruction": f"Critic ({i['severity']}): {i['issue']} Suggestion: {i['suggestion']} Do not add technical facts."}
                  for i in critic.get("issues", []) if i.get("section_id") and i.get("severity") in ("medium", "high")]
    if not fixes:
        print(json.dumps({"status": "nothing_to_revise"})); return 0

    logf = open(ap["log"], "a", encoding="utf-8")
    def log(msg):
        line = f"[{utc_now()}] {msg}"; print(line, flush=True); logf.write(line + "\n"); logf.flush()
    store = open_store()
    sedb_records = []
    packet_text = json.dumps(ov_packet, ensure_ascii=False, indent=1)
    draft = final["draft"]

    def run_worker(role, contract_version, inputs, attempt, suffix, max_attempts):
        task_id = f"af-{slug}-{ap['task_prefix']}{suffix}-{attempt:02d}"
        task = build_task(task_id, contract_version, inputs)
        log(f"{role} attempt {attempt}: dispatch {task_id} ({sum(len(i[1]) for i in inputs)} input chars)")
        rec = dispatch(task, sdir / "tasks", attempt=attempt, log=log)
        rec["budget_max_cost_usd"] = task["constraints"]["max_cost_usd"]; rec["role"], rec["contract_version"] = role, contract_version
        errs = validate_output(contract_version, rec["output"]) if rec.get("output") else ["no output"]
        rec["schema_errors"] = errs
        if rec.get("output") and errs:
            rec["status"] = "schema_invalid"
        out_path = ap["runs_dir"] / f"{suffix}-{attempt:02d}.json"
        write_json(out_path, rec); rec["output_ref"] = rel_ref(out_path)
        log(f"{role} attempt {attempt}: status={rec['status']} failure={rec.get('failure')} latency={rec.get('latency_seconds')}s cost={(rec.get('cost') or {}).get('currency_cost_usd')}")
        summary = None
        if rec.get("output"):
            o = rec["output"]; summary = {k: o.get(k) for k in ("status",) if k in o}
            if "claims" in o: summary["claims"] = len(o["claims"]); summary["sections"] = [s["id"] for s in o.get("sections", [])]
            if "claim_checks" in o:
                summary["claim_checks"] = {s: sum(1 for c in o["claim_checks"] if c["status"] == s) for s in ("supported", "partially_supported", "unsupported", "unresolved", "conflicting")}
                summary["required_fixes"] = len(o.get("required_fixes", []))
        sedb_records.extend(worker_records("af", f"task_{task_id}", rec, role=role, contract_version=contract_version, repository_id=repo_id, revision_id=rev_id,
                                           analysis_run_id=run_id, bundle_sha256=bundle_sha, asset_type=asset_type, max_attempts=max_attempts, structured_result=summary))
        wr["runs"].append({"role": role, "attempt": attempt, "task_id": task_id, "status": rec["status"], "failure": rec.get("failure"), "latency_seconds": rec.get("latency_seconds"),
                           "worker_run_entity_id": rec.get("worker_run_entity_id"), "cost": rec.get("cost"), "conservative_cost_ceiling_usd": rec.get("conservative_cost_ceiling_usd"),
                           "candidate": rec.get("macr_candidate_id"), "summary": summary, "output_ref": rec["output_ref"]})
        write_json(ap["workers_receipt"], wr)
        return rec

    log(f"{source}-driven targeted revision ({asset_type}): {len(fixes)} fixes; budget used writer {final['writer_attempts']}/{WRITER_MAX}, verifier {final['verifier_attempts']}/{VERIFIER_MAX}")
    r = run_worker("writer", revision_contract, [("grounding_packet", packet_text), ("previous_draft", json.dumps(draft, ensure_ascii=False, indent=1)),
                                                 ("required_fixes", json.dumps(fixes, ensure_ascii=False, indent=1))], final["writer_attempts"] + 1, "writer-revision", WRITER_MAX)
    outcome = {"status": "revision_rejected", "kept": "previous draft", "source": source}
    if r["status"] == "candidate_success":
        det = deterministic_checks(r["output"], ov_packet, catalog, files)
        write_json(ap["runs_dir"] / f"deterministic-check-{final['writer_attempts'] + 1:02d}.json", det)
        wr[f"deterministic_check_{final['writer_attempts'] + 1}"] = {"ok": det["ok"], "failures": det["failures"][:20]}
        log(f"deterministic checks on revision: ok={det['ok']} failures={len(det['failures'])}")
        if det["ok"]:
            v = run_worker("verifier", VERIFIER_V, [("grounding_packet", packet_text), ("draft", json.dumps(r["output"], ensure_ascii=False, indent=1))], final["verifier_attempts"] + 1, "verifier", VERIFIER_MAX)
            if v["status"] == "candidate_success" and v["output"]["status"] == "pass":
                final.update({"draft": r["output"], "draft_run_ref": r["output_ref"], "verifier": v["output"], "deterministic": det, "hard_gate_pass": True,
                              "writer_attempts": final["writer_attempts"] + 1, "verifier_attempts": final["verifier_attempts"] + 1, f"{source}_revision_applied": fixes,
                              "critic_on_previous_draft": source == "verifier"})
                wr["verifier_verdict"] = {"status": "pass", "summary": v["output"].get("summary"), "unsupported": v["output"].get("unsupported_claims"), "leakage": v["output"].get("knowledge_leakage"), "fixes": 0}
                outcome = {"status": "revision_accepted", "claims": len(r["output"]["claims"]), "source": source}
            else:
                final.update({"writer_attempts": final["writer_attempts"] + 1, "verifier_attempts": final["verifier_attempts"] + 1, f"{source}_revision_rejected_by": "verifier"})
                outcome["reason"] = "second verifier did not pass the revision"
        else:
            final.update({"writer_attempts": final["writer_attempts"] + 1, f"{source}_revision_rejected_by": "deterministic_checks"})
            outcome["reason"] = det["failures"][:5]
    else:
        final.update({"writer_attempts": final["writer_attempts"] + 1, f"{source}_revision_rejected_by": r.get("failure") or r["status"]})
    final["worker_run_ids"] = [x["worker_run_entity_id"] for x in wr["runs"] if x.get("worker_run_entity_id")]
    write_json(ap["runs_dir"] / "final-draft-bundle.json", final)
    wr[f"{source}_revision"] = outcome; wr["outcome"] = "hard_gate_pass" if final["hard_gate_pass"] else "escalation_required"
    write_json(ap["workers_receipt"], wr)
    res = store.write(sedb_records, source="worker")
    log(f"SEDB: {res}"); log(f"{source} revision outcome: {outcome}")
    print(json.dumps(outcome, ensure_ascii=False))
    return 0 if final["hard_gate_pass"] else 3


if __name__ == "__main__":
    _args = [a for a in sys.argv[1:] if not a.startswith("--")]
    _src = sys.argv[sys.argv.index("--source") + 1] if "--source" in sys.argv else "critic"
    raise SystemExit(main(_args[0], _args[1] if len(_args) > 1 and _args[1] not in ("critic", "verifier") else "overview", _src))
