"""Canary batch orchestrator: several repositories through the whole line, in phases.

Phase A (sequential; RepoLumen and the 15k-row SEDB writes should not overlap): step 0 GitHub artifacts →
RepoLumen analysis in its own venv → step 1 register → step 3 ground + packets.
Phase B (parallel, `--workers`, default 3): step 4 real workers through MACR — the slow, paid part.
Phase C (sequential): step 5 validate → step 6 render → report → step 7 view model.

Nothing here publishes. A repository that fails a phase is recorded and skipped in later phases; the
batch continues. Re-running skips finished phases (each step is idempotent on its own receipts).

Usage: python canary.py <batch.json>     # {"batch": "canary-001", "repos": ["owner/name", ...], "first_index": 2}
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from af_common import LAB, load_json, slice_dir, utc_now, write_json

PIPE = LAB / "pipeline"
_local = json.loads((PIPE / "local_paths.json").read_text(encoding="utf-8")) if (PIPE / "local_paths.json").exists() else {}
REPOLUMEN = Path(os.environ.get("AF_REPOLUMEN_ROOT") or _local.get("AF_REPOLUMEN_ROOT") or (LAB.parent / "RepoLumen"))
RL_PY = REPOLUMEN / ".venv" / "Scripts" / "python.exe"


def free_ram_gb() -> float:
    """Free physical memory (Windows). The batch waits below RAM_FLOOR_GB before every heavy step."""
    import ctypes
    class _MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    ms = _MS(); ms.dwLength = ctypes.sizeof(_MS); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
    return ms.ullAvailPhys / 2**30


RAM_FLOOR_GB = 8.0


def run(cmd: list[str], log: Path, cwd: Path = LAB) -> tuple[int, float]:
    # 2026-09-14: a fanned-out batch exhausted the machine's 32 GB. Heavy steps wait for headroom.
    waited = 0
    while free_ram_gb() < RAM_FLOOR_GB and waited < 1800:
        time.sleep(15); waited += 15
    if waited:
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n[ram-guard] waited {waited} s for free RAM >= {RAM_FLOOR_GB} GB (now {free_ram_gb():.1f} GB)\n")
    t0 = time.perf_counter()
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"\n===== {utc_now()} $ {' '.join(cmd)}\n"); f.flush()
        rc = subprocess.call(cmd, cwd=str(cwd), stdout=f, stderr=subprocess.STDOUT)
    return rc, round(time.perf_counter() - t0, 1)


def slug_for(full_name: str, index: int) -> str:
    owner, name = full_name.split("/")
    return f"slice-{index:03d}-{owner}-{name}".lower().replace("_", "-").replace(".", "-")


def main(batch_file: str) -> int:
    spec = load_json(batch_file)
    batch = spec["batch"]; repos = spec["repos"]; first = spec.get("first_index", 2); workers = int(spec.get("workers", 1))  # one at a time on this machine unless Neo asks otherwise
    bdir = LAB / "canary" / batch; bdir.mkdir(parents=True, exist_ok=True)
    status_path = bdir / "status.json"
    status = load_json(status_path) if status_path.exists() else {"batch": batch, "started_at": utc_now(), "repos": {}}
    py = sys.executable

    def st(full_name):
        return status["repos"].setdefault(full_name, {"slug": slug_for(full_name, first + repos.index(full_name)), "phases": {}, "timings": {}})

    lock = threading.Lock()

    def save():
        with lock:
            status["updated_at"] = utc_now(); write_json(status_path, status)

    def phase(full_name, name, cmd, cwd=LAB):
        s = st(full_name); slug = s["slug"]
        if s["phases"].get(name) == "ok":
            return True
        rc, secs = run(cmd, bdir / f"{slug}.log", cwd)
        s["phases"][name] = "ok" if rc == 0 else f"failed rc={rc}"; s["timings"][name] = secs; save()
        return rc == 0

    # ---- Phase B runner (parallel); a repository's step 4 starts as soon as its phase A is done, so one
    # slow analysis (pydantic-ai: 22 min in RepoLumen) never holds the paid phase for everyone else.
    def step4(full_name):
        ok = phase(full_name, "step4", [py, str(PIPE / "step4_workers.py"), st(full_name)["slug"]])
        print(f"[B] {full_name} -> step4 {st(full_name)['phases'].get('step4')} ({st(full_name)['timings'].get('step4')} s)", flush=True)
        return ok
    ex = ThreadPoolExecutor(max_workers=workers)
    futures = []

    # ---- Phase A (sequential)
    for full_name in repos:
        s = st(full_name); slug = s["slug"]; sdir = slice_dir(slug)
        ok = phase(full_name, "step0", [py, str(PIPE / "step0_github.py"), full_name, slug])
        if ok:
            head = load_json(sdir / "artifacts" / "github" / "head.json")["sha"]
            durable_exists = any((sdir / "artifacts" / "repolumen").glob("*/*/*/semantic-manifest.json")) if (sdir / "artifacts" / "repolumen").exists() else False
            if durable_exists:
                s["phases"]["repolumen"] = "ok"
            else:
                ok = phase(full_name, "repolumen", [str(RL_PY), str(PIPE / "rl_analyze.py"), f"https://github.com/{full_name}", str(sdir / "artifacts" / "repolumen" / "pending"), head])
        ok = ok and phase(full_name, "step1", [py, str(PIPE / "step1_register.py"), slug])
        ok = ok and phase(full_name, "step3", [py, str(PIPE / "step3_ground.py"), slug])
        print(f"[A] {full_name} -> {s['phases']}", flush=True)
        if ok and s["phases"].get("step4") != "ok":
            futures.append(ex.submit(step4, full_name))

    for f in futures:
        f.result()
    ex.shutdown(wait=True)

    # ---- Phase C
    for full_name in repos:
        s = st(full_name); slug = s["slug"]
        if s["phases"].get("step4") != "ok":
            continue
        ok = phase(full_name, "step5", [py, str(PIPE / "step5_validate.py"), slug])
        ok = ok and phase(full_name, "step6", [py, str(PIPE / "step6_render.py"), slug])
        ok = ok and phase(full_name, "report", [py, str(PIPE / "report.py"), slug])
        ok = ok and phase(full_name, "step7", [py, str(PIPE / "step7_viewmodel.py"), slug])
        try:
            wr = load_json(slice_dir(slug) / "workers-receipt.json")
            s["cost_usd"] = round(sum((x.get("cost") or {}).get("currency_cost_usd") or 0 for x in wr["runs"]), 4)
            s["dispatches"] = len(wr["runs"]); s["outcome"] = wr.get("outcome"); s["taxonomy"] = wr.get("taxonomy")
        except Exception:
            pass
        try:
            val = load_json(slice_dir(slug) / "validation-receipt.json")
            s["validated"] = val["hard_gate_pass"]; s["asset_revision_id"] = val["asset_revision_id"]; s["claims"] = f"{val['supported_claims']}/{val['claims']}"
        except Exception:
            s["validated"] = False
        save()
        print(f"[C] {full_name} -> validated={s.get('validated')} claims={s.get('claims')} cost={s.get('cost_usd')}", flush=True)

    status["completed_at"] = utc_now(); save()
    print(json.dumps({r: {k: v for k, v in st(r).items() if k in ("slug", "validated", "claims", "cost_usd", "dispatches", "outcome")} for r in repos}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
