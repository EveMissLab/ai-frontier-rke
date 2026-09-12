"""Step 4b — critic-driven targeted revision (writer attempt 2) + independent re-verification (verifier attempt 2).

Runs only when step 4 ended with a verifier pass but the critic flagged overclaims or
medium/high wording issues. Budgets stay those of Paper 05 (writer 2, verifier 2): if the
revised draft fails deterministic checks or the second verifier, the previous verified draft
is kept and the revision is recorded as a failed attempt.

Usage: python step4b_revise.py <slice_slug>
"""
from __future__ import annotations

import json
import sys

from af_common import load_json, open_store, rel_ref, slice_dir, utc_now, write_json
from macr_worker import build_task, dispatch, validate_output, worker_records
from prompts import REVISION_V, VERIFIER_V
from step4_workers import WRITER_MAX, VERIFIER_MAX, deterministic_checks


def main(slug: str) -> int:
    sdir = slice_dir(slug)
    reg, gr = load_json(sdir / "registration-receipt.json"), load_json(sdir / "grounding-receipt.json")
    wr = load_json(sdir / "workers-receipt.json")
    final = load_json(sdir / "worker_runs" / "final-draft-bundle.json")
    ov_packet = load_json(sdir / "packets" / "overview.json")
    bundle = load_json(sdir / gr["grounding_bundle_ref"].split("/", 1)[1])
    catalog, files = bundle["groundings"], set(bundle["files"])
    repo_id, rev_id, run_id, bundle_sha = reg["repository_id"], reg["revision_id"], gr["analysis_run_id"], gr["grounding_bundle_sha256"]
    critic = final.get("critic") or {}
    if final["writer_attempts"] >= WRITER_MAX or final["verifier_attempts"] >= VERIFIER_MAX:
        print(json.dumps({"status": "budget_exhausted", "writer_attempts": final["writer_attempts"], "verifier_attempts": final["verifier_attempts"]})); return 2
    fixes = [{"target": f["claim_id"], "action": "reword", "instruction": f"Critic overclaim flag: {f['reason']} Hedge or downgrade the wording; keep the same grounding refs."} for f in critic.get("overclaim_flags", [])]
    fixes += [{"target": i["section_id"], "action": "reword", "instruction": f"Critic ({i['severity']}): {i['issue']} Suggestion: {i['suggestion']} Do not add technical facts."}
              for i in critic.get("issues", []) if i.get("section_id") and i.get("severity") in ("medium", "high")]
    if not fixes:
        print(json.dumps({"status": "nothing_to_revise"})); return 0

    logf = open(sdir / "step4.log", "a", encoding="utf-8")
    def log(msg):
        line = f"[{utc_now()}] {msg}"; print(line, flush=True); logf.write(line + "\n"); logf.flush()
    store = open_store()
    sedb_records = []
    packet_text = json.dumps(ov_packet, ensure_ascii=False, indent=1)
    draft = final["draft"]

    def run_worker(role, contract_version, inputs, attempt, suffix, max_attempts):
        task_id = f"af-{slug}-{suffix}-{attempt:02d}"
        task = build_task(task_id, contract_version, inputs)
        log(f"{role} attempt {attempt}: dispatch {task_id} ({sum(len(i[1]) for i in inputs)} input chars)")
        rec = dispatch(task, sdir / "tasks", attempt=attempt, log=log)
        rec["budget_max_cost_usd"] = task["constraints"]["max_cost_usd"]; rec["role"], rec["contract_version"] = role, contract_version
        errs = validate_output(contract_version, rec["output"]) if rec.get("output") else ["no output"]
        rec["schema_errors"] = errs
        if rec.get("output") and errs:
            rec["status"] = "schema_invalid"
        out_path = sdir / "worker_runs" / f"{suffix}-{attempt:02d}.json"
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
                                           analysis_run_id=run_id, bundle_sha256=bundle_sha, asset_type="overview", max_attempts=max_attempts, structured_result=summary))
        wr["runs"].append({"role": role, "attempt": attempt, "task_id": task_id, "status": rec["status"], "failure": rec.get("failure"), "latency_seconds": rec.get("latency_seconds"),
                           "worker_run_entity_id": rec.get("worker_run_entity_id"), "cost": rec.get("cost"), "conservative_cost_ceiling_usd": rec.get("conservative_cost_ceiling_usd"),
                           "candidate": rec.get("macr_candidate_id"), "summary": summary, "output_ref": rec["output_ref"]})
        write_json(sdir / "workers-receipt.json", wr)
        return rec

    log(f"critic-driven targeted revision: {len(fixes)} fixes ({len(critic.get('overclaim_flags', []))} overclaim flags)")
    r = run_worker("writer", REVISION_V, [("grounding_packet", packet_text), ("previous_draft", json.dumps(draft, ensure_ascii=False, indent=1)),
                                                            ("required_fixes", json.dumps(fixes, ensure_ascii=False, indent=1))], final["writer_attempts"] + 1, "writer-revision", WRITER_MAX)
    outcome = {"status": "revision_rejected", "kept": "previous verified draft"}
    if r["status"] == "candidate_success":
        det = deterministic_checks(r["output"], ov_packet, catalog, files)
        write_json(sdir / "worker_runs" / "deterministic-check-02.json", det)
        wr["deterministic_check_2"] = {"ok": det["ok"], "failures": det["failures"][:20]}
        log(f"deterministic checks on revision: ok={det['ok']} failures={len(det['failures'])}")
        if det["ok"]:
            v = run_worker("verifier", VERIFIER_V, [("grounding_packet", packet_text), ("draft", json.dumps(r["output"], ensure_ascii=False, indent=1))], final["verifier_attempts"] + 1, "verifier", VERIFIER_MAX)
            if v["status"] == "candidate_success" and v["output"]["status"] == "pass":
                final.update({"draft": r["output"], "draft_run_ref": r["output_ref"], "verifier": v["output"], "deterministic": det, "hard_gate_pass": True,
                              "writer_attempts": final["writer_attempts"] + 1, "verifier_attempts": final["verifier_attempts"] + 1, "critic_revision_applied": fixes})
                wr["verifier_verdict"] = {"status": "pass", "summary": v["output"].get("summary"), "unsupported": v["output"].get("unsupported_claims"), "leakage": v["output"].get("knowledge_leakage"), "fixes": 0}
                outcome = {"status": "revision_accepted", "claims": len(r["output"]["claims"])}
            else:
                final.update({"writer_attempts": final["writer_attempts"] + 1, "verifier_attempts": final["verifier_attempts"] + 1, "critic_revision_rejected_by": "verifier"})
                outcome["reason"] = "second verifier did not pass the revision"
        else:
            final.update({"writer_attempts": final["writer_attempts"] + 1, "critic_revision_rejected_by": "deterministic_checks"})
            outcome["reason"] = det["failures"][:5]
    else:
        final.update({"writer_attempts": final["writer_attempts"] + 1, "critic_revision_rejected_by": r.get("failure") or r["status"]})
    final["worker_run_ids"] = [x["worker_run_entity_id"] for x in wr["runs"] if x.get("worker_run_entity_id")]
    write_json(sdir / "worker_runs" / "final-draft-bundle.json", final)
    wr["critic_revision"] = outcome; wr["outcome"] = "hard_gate_pass" if final["hard_gate_pass"] else "escalation_required"
    write_json(sdir / "workers-receipt.json", wr)
    res = store.write(sedb_records, source="worker")
    log(f"SEDB: {res}"); log(f"critic revision outcome: {outcome}")
    print(json.dumps(outcome, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
