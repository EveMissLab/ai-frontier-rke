"""MACR-backed cheap-model worker dispatch (GLM-5.3-Flash through `glm_flash_worker`).

One worker attempt = one MACR task file = one preflight/approve/preflight/invoke
sequence = at most one provider call. MACR performs no automatic retry; the
orchestrator decides explicitly and records every attempt (Paper 05 §29-30, §64).

Never touches the credential: MACR reads its own key file inside its own bounded
loader. Never puts local paths or secrets into a packet: inputs are scrubbed
against MACR's own path-marker rules before the task is written.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from af_common import MACR_ROOT, MACR_STATE, canonical_bytes, sha256_bytes, utc_now, write_json
from prompts import CONTRACTS, SCHEMAS, contract_hash

PROVIDER_ID = "glm_flash_worker"
MODEL_PROVIDER, MODEL_NAME = "zhipu", "glm-5.3-flash"
PROJECT_ID = "ai-frontier-rke"
HARD_TASK_COST_CAP_USD = 0.50

_DRIVE = re.compile(r"(?i)(?<![a-z0-9])[a-z]:[\\/]")
_UNC = re.compile(r"\\{2,}[^\s\\/:*?\"<>|{}\[\]]+\\+[^\s\\/:*?\"<>|{}\[\]]+")
# URI form only: a draft that says "by file:\n- src/..." (JSON-escaped newline) is prose, not a local path
# (2026-09-14, pallets/click: the verifier input was refused on exactly that).
_FILE_URI = re.compile(r"(?i)(?<![a-z0-9+.-])file://[^\s]+")
_SECRET = re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}")


class PacketPolicyError(ValueError):
    pass


_JSON_ESCAPES = re.compile(r'\\[ntr"]')


def _string_leaves(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _string_leaves(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _string_leaves(v)


def scrub_check(text: str, label: str) -> None:
    """Refuse an input that carries a local path, UNC path, file URI or credential marker.

    Inputs are JSON text; the check runs on the *decoded* strings (2026-09-14: JSON escaping turned
    `class E:\n` into a drive path and a source regex `^\\d+\\.\\d+$` into a UNC path). Non-JSON text is
    checked with JSON escape sequences neutralised."""
    try:
        pieces = list(_string_leaves(json.loads(text)))
    except Exception:
        pieces = [_JSON_ESCAPES.sub(" ", text)]
    for piece in pieces:
        for name, rx in (("windows_drive_path", _DRIVE), ("unc_path", _UNC), ("file_uri", _FILE_URI), ("credential_marker", _SECRET)):
            m = rx.search(piece)
            if m:
                raise PacketPolicyError(f"input '{label}' contains a {name} marker near: {piece[max(0, m.start()-40):m.end()+40]!r}")


_JSON_ESCAPE = re.compile(r'\\(u[0-9a-fA-F]{4}|.)', re.S)
_JSON_SIMPLE = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def readable_text(text: str) -> str:
    """Model-facing form of a JSON input: the same structure, with string escapes resolved so a code excerpt
    reads as code (`class E:` + newline, `^\\d+\\.\\d+$`) instead of as `E:\\n` and `\\\\d+\\\\.` — which MACR's own
    path-marker policy (rightly) refuses in raw JSON (2026-09-14: rich and mitmproxy writer tasks failed
    `approval_invalid` on exactly that). Non-JSON text is returned unchanged. Real paths stay visible, so
    both this module's scrub and MACR's policy still catch them."""
    try:
        obj = json.loads(text)
    except Exception:
        return text
    dumped = json.dumps(obj, ensure_ascii=False, indent=1)

    def unescape(m):
        code = m.group(1)
        if code.startswith("u"):
            return chr(int(code[1:], 16))
        return _JSON_SIMPLE.get(code, m.group(0))
    return _JSON_ESCAPE.sub(unescape, dumped)


def build_task(task_id: str, contract_version: str, inputs: list[tuple[str, str]], *, max_cost_usd: float = 0.15,
               max_latency_s: int = 900, verification_methods: list[str] | None = None) -> dict:
    c = CONTRACTS[contract_version]
    task_inputs = [{"type": "text", "name": "contract", "content": c["contract"]}]
    for name, text in inputs:
        text = readable_text(text)  # packet text v2 (2026-09-14): JSON structure, escapes resolved
        scrub_check(text, name)
        task_inputs.append({"type": "text", "name": name, "content": text})
    return {
        "task_id": task_id,
        "goal": c["goal"],
        "task_type": "delegated_routine",
        "delegable": True,
        "delegation_class": "non_sensitive_routine",
        "delegation_approval_sha256": "0" * 64,
        "workspace": {"repo": "ai-frontier-rke-slice", "write_scope": []},
        "inputs": task_inputs,
        "constraints": {"max_cost_usd": max_cost_usd, "max_latency_s": max_latency_s, "max_output_tokens": 65536,
                        "max_context_tokens": 512000, "internet": True, "privacy": "public"},
        "required_capabilities": ["text_generation"],
        "verification": {"required": True, "methods": verification_methods or ["deterministic_schema_validation", "grounding_id_existence", "independent_verifier_worker"]},
        "return_contract": {"summary": False, "patch": False, "evidence": False, "format": "json_object", "exact_text": None, "language": None},
    }


def _ps(script_args: list[str], timeout: int = 1200, named: dict | None = None) -> tuple[int, str, str]:
    """Run a PowerShell script. Positional args are single-quoted; `named` are passed as -Name 'value'."""
    quoted = " ".join("'" + a.replace("'", "''") + "'" for a in script_args)
    if named:
        quoted += " " + " ".join(f"-{k} '" + str(v).replace("'", "''") + "'" for k, v in named.items())
    cmd = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $env:PYTHONIOENCODING='utf-8'; "
           f"& {quoted}; exit $LASTEXITCODE")
    proc = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                          cwd=str(MACR_ROOT), capture_output=True, timeout=timeout)
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def _json_from(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def macr(*args: str, timeout: int = 1200) -> tuple[int, dict | None, str]:
    code, out, err = _ps([str(MACR_ROOT / "scripts" / "macr.ps1"), *args], timeout=timeout)
    try:
        return code, _json_from(out), err
    except Exception:
        return code, None, out + "\n" + err


def parse_answer(text: str) -> tuple[dict | None, bool, str | None]:
    """Return (object, repaired, error). Repair is envelope-only: fences and surrounding prose."""
    raw = text.strip()
    try:
        return json.loads(raw), False, None
    except json.JSONDecodeError:
        pass
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.S).strip()
    for candidate in (fenced, raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw and "}" in raw else ""):
        if not candidate:
            continue
        try:
            return json.loads(candidate), True, None
        except json.JSONDecodeError as exc:
            last = str(exc)
    return None, True, last if "last" in dir() else "no JSON object found"


def newest_candidate_dir(after_ts: float) -> Path | None:
    root = MACR_STATE / "candidates" / PROVIDER_ID
    dirs = [d for d in root.iterdir() if d.is_dir() and d.stat().st_mtime >= after_ts - 2]
    return max(dirs, key=lambda d: d.stat().st_mtime) if dirs else None


def dispatch(task: dict, task_dir: Path, *, attempt: int, log) -> dict:
    """Run one bounded worker attempt. Returns a worker-run record (never raises on provider failure)."""
    task_dir.mkdir(parents=True, exist_ok=True)
    task_path = task_dir / f"{task['task_id']}.json"
    record = {"task_id": task["task_id"], "attempt": attempt, "provider_id": PROVIDER_ID, "model_provider": MODEL_PROVIDER, "model_name": MODEL_NAME,
              "started_at": utc_now(), "stages": [], "status": "failed", "answer_sha256": None, "answer_repaired": False, "output": None, "cost": {}, "provider_meta": {}}
    input_bytes = canonical_bytes([i for i in task["inputs"]])
    record["input_sha256"] = sha256_bytes(input_bytes)

    # 1) digest
    write_json(task_path, task)
    code, pre, err = macr("glm-preflight", str(task_path), "--show-required-digest")
    record["stages"].append({"stage": "preflight_digest", "exit": code, "result": pre or err[-1500:]})
    if code != 0 or not pre or "required_approval_sha256" not in pre:
        record["failure"] = "preflight_digest_failed"; return record
    ceiling = float(pre.get("conservative_cost_ceiling_usd") or 0)
    if ceiling > task["constraints"]["max_cost_usd"]:
        bumped = round(min(HARD_TASK_COST_CAP_USD, ceiling * 1.25 + 0.01), 4)
        log(f"  budget: conservative ceiling {ceiling} > {task['constraints']['max_cost_usd']}; raising max_cost_usd to {bumped}")
        if bumped < ceiling:
            record["failure"] = "cost_ceiling_above_hard_cap"; return record
        task["constraints"]["max_cost_usd"] = bumped
        write_json(task_path, task)
        code, pre, err = macr("glm-preflight", str(task_path), "--show-required-digest")
        record["stages"].append({"stage": "preflight_digest_rebudget", "exit": code, "result": pre or err[-1500:]})
        if code != 0 or not pre:
            record["failure"] = "preflight_digest_failed"; return record
    task["delegation_approval_sha256"] = pre["required_approval_sha256"]
    write_json(task_path, task)
    record["conservative_cost_ceiling_usd"] = float(pre.get("conservative_cost_ceiling_usd") or 0)
    record["request_bytes"] = pre.get("request_bytes")

    # 2) approve (host-signed, local) unless an exact unexpired approval already exists, then 3) verify
    code, chk, err = macr("glm-preflight", str(task_path))
    if code != 0:
        code, appr, err = macr("glm-approve", str(task_path), "--expires-in-days", "2")
        record["stages"].append({"stage": "approve", "exit": code, "result": (appr or err[-1500:])})
        if code != 0:
            record["failure"] = "approve_failed"; return record
        code, chk, err = macr("glm-preflight", str(task_path))
    else:
        record["stages"].append({"stage": "approve", "exit": 0, "result": "existing exact approval reused"})
    record["stages"].append({"stage": "preflight_verify", "exit": code, "result": chk or err[-1500:]})
    if code != 0:
        record["failure"] = "preflight_verify_failed"; return record

    # 4) the only paid step (named parameters: -TaskPath, -ProjectId)
    t0 = time.time()
    code, out, err = _ps([str(MACR_ROOT / "scripts" / "invoke-glm.ps1")], named={"TaskPath": str(task_path), "ProjectId": PROJECT_ID},
                         timeout=task["constraints"]["max_latency_s"] + 300)
    latency = round(time.time() - t0, 2)
    record["latency_seconds"] = latency
    try:
        res = _json_from(out)
    except Exception:
        res = None
    record["stages"].append({"stage": "invoke", "exit": code, "result": ({k: v for k, v in res.items() if k != "answer"} if res else (out[-2000:] + err[-1500:]))})
    record["completed_at"] = utc_now()
    if not res:
        record["failure"] = "invoke_no_result"; return record
    record["macr_status"] = res.get("status")
    record["cost"] = res.get("cost") or {}
    record["provider_meta"] = res.get("provider_meta") or {}
    record["warnings"] = res.get("warnings") or []
    if res.get("failure_code"):
        record["failure"] = f"{res.get('failure_stage')}:{res.get('failure_code')}"
    cand = newest_candidate_dir(t0)
    answer_text = res.get("answer") or ""
    if cand and (cand / "answer.bin").exists():
        raw = (cand / "answer.bin").read_bytes()
        record["macr_candidate_id"] = cand.name
        record["answer_sha256"] = sha256_bytes(raw)
        answer_text = raw.decode("utf-8", "replace") or answer_text
    else:
        record["answer_sha256"] = sha256_bytes(answer_text.encode("utf-8")) if answer_text else None
    (task_dir / f"{task['task_id']}.answer.txt").write_text(answer_text, encoding="utf-8")
    if res.get("status") != "candidate_success" or not answer_text.strip():
        record["failure"] = record.get("failure") or "candidate_failure"; return record
    obj, repaired, perr = parse_answer(answer_text)
    record["answer_repaired"] = repaired
    if obj is None:
        record["failure"] = f"answer_not_json:{perr}"; record["status"] = "schema_invalid"; return record
    record["output"] = obj
    record["status"] = "candidate_success"
    return record


def validate_output(contract_version: str, obj: dict) -> list[str]:
    import jsonschema
    v = jsonschema.Draft202012Validator(SCHEMAS[contract_version])
    return [f"{'/'.join(str(p) for p in e.path)}: {e.message[:160]}" for e in sorted(v.iter_errors(obj), key=lambda e: list(e.path))]


def usage_from(record: dict) -> dict:
    """Best-effort extraction of tokens/cost from MACR's cost + provider_meta blocks."""
    flat = {}
    for src in (record.get("cost") or {}, record.get("provider_meta") or {}):
        for k, v in src.items():
            if isinstance(v, dict):
                for k2, v2 in v.items():
                    flat[f"{k}.{k2}"] = v2
            else:
                flat[k] = v
    def pick(*names):
        for n in names:
            for k, v in flat.items():
                if k.endswith(n) and isinstance(v, (int, float)):
                    return v
        return None
    return {"input_tokens": pick("prompt_tokens", "input_tokens"), "output_tokens": pick("completion_tokens", "output_tokens"),
            "reasoning_tokens": pick("reasoning_tokens"), "cost_usd": pick("known_cost_usd", "cost_usd", "usd", "estimated_usd"),
            "raw": flat}


def worker_records(store_kind_prefix: str, task_entity_id: str, record: dict, *, role: str, contract_version: str,
                   repository_id: str, revision_id: str, analysis_run_id: str, bundle_sha256: str, asset_type: str | None,
                   max_attempts: int, structured_result: dict | None) -> list[dict]:
    usage = usage_from(record)
    run_id = f"worker_{record.get('macr_candidate_id') or sha256_bytes((task_entity_id + str(record['attempt']) + record['started_at']).encode())[:16]}"
    status = {"candidate_success": "passed"}.get(record["status"], record["status"])
    if record.get("failure") and status == "passed":
        status = "failed"
    model_provider, model_name = record.get("model_provider", MODEL_PROVIDER), record.get("model_name", MODEL_NAME)
    provider_id = record.get("provider_id", PROVIDER_ID)
    record["worker_run_entity_id"] = run_id
    return [
        {"entity_id": task_entity_id, "kind": "af_worker_task", "label": f"{role} · {asset_type or 'n/a'} · {task_entity_id[-12:]}", "values": {
            "af_repository_id": repository_id, "af_revision_id": revision_id, "af_analysis_run_id": analysis_run_id,
            "af_worker_role": role, "af_asset_type": asset_type, "af_task_status": "completed" if status == "passed" else "failed",
            "af_max_attempts": max_attempts, "af_attempt_count": record["attempt"], "af_prompt_contract_version": contract_version,
            "af_prompt_contract_sha256": contract_hash(contract_version), "af_input_sha256": record.get("input_sha256"),
            "af_grounding_bundle_sha256": bundle_sha256, "af_budget": {"max_cost_usd": record.get("budget_max_cost_usd"), "max_latency_s": 900, "max_output_tokens": 65536, "max_attempts": max_attempts},
            "af_macr_task_id": record["task_id"], "af_created_at": record["started_at"], "af_provenance_source": "system"}},
        {"entity_id": run_id, "kind": "af_worker_run", "label": f"{role} attempt {record['attempt']} ({status})", "values": {
            "af_task_id": task_entity_id, "af_worker_role": role, "af_attempt": record["attempt"], "af_model_provider": model_provider, "af_model_name": model_name,
            "af_macr_provider_id": provider_id, "af_macr_candidate_id": record.get("macr_candidate_id"), "af_input_sha256": record.get("input_sha256"),
            "af_output_ref": record.get("output_ref"), "af_output_sha256": record.get("answer_sha256"), "af_run_status": status,
            "af_input_tokens": usage["input_tokens"], "af_output_tokens": usage["output_tokens"], "af_reasoning_tokens": usage["reasoning_tokens"],
            "af_cost_usd": usage["cost_usd"], "af_cost_status": "known" if usage["cost_usd"] is not None else "unknown_after_dispatch",
            "af_latency_seconds": record.get("latency_seconds"), "af_started_at": record["started_at"], "af_completed_at": record.get("completed_at"),
            "af_prompt_contract_version": contract_version, "af_generation_config": {"reasoning_effort": "max", "max_output_tokens": 65536, "temperature": "provider_default"},
            "af_structured_result": structured_result, "af_provenance_source": "worker"}},
    ]
