"""Shared helpers for the AI Frontier RKE vertical-slice pipeline.

Paths, canonical JSON, hashing, deterministic IDs and the SEDB store handle.
Nothing here talks to a model or to the network.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
# Component locations, in order: environment variable → pipeline/local_paths.json (git-ignored, the
# development machine's layout) → sibling directory of the lab. Nothing here is a credential.
_LOCAL_PATHS = LAB / "pipeline" / "local_paths.json"
_local = json.loads(_LOCAL_PATHS.read_text(encoding="utf-8")) if _LOCAL_PATHS.exists() else {}


def _loc(key: str, default: Path) -> Path:
    return Path(os.environ.get(key) or _local.get(key) or default)


SEDB_ROOT = _loc("AF_SEDB_ROOT", LAB.parent / "SEDB")
SEDB_PROJECT = SEDB_ROOT / "projects" / "ai-frontier-repository-intelligence"
MACR_ROOT = _loc("AF_MACR_ROOT", LAB.parent / "MACR")
MACR_STATE = _loc("AF_MACR_STATE", MACR_ROOT / "state")

for p in (SEDB_ROOT / "current" / "src", SEDB_PROJECT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from config import LICENSE_POLICY, TAXONOMY_VERSION, ProjectConfig  # noqa: E402
from store import RepoIntelStore  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_bytes(obj) -> bytes:
    """UTF-8, sorted keys, normalized newline; stable for hashing (Paper 04 §67)."""
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def short(digest: str, n: int = 12) -> str:
    return digest[:n]


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, obj) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_bytes(obj)
    path.write_bytes(data)
    return sha256_bytes(data)


def open_store() -> RepoIntelStore:
    """Open the canonical catalog, or the database named by AF_SEDB_DB (mock / test runs only)."""
    override = os.environ.get("AF_SEDB_DB")
    config = ProjectConfig(database_path=Path(override)) if override else ProjectConfig()
    store = RepoIntelStore.open(config)
    store.ensure_schema()
    return store


def slice_dir(slug: str) -> Path:
    return LAB / slug


def asset_paths(sdir: Path, asset_type: str = "overview") -> dict:
    """Per-asset file layout. `overview` keeps the original flat layout; other assets get suffixed/nested names."""
    if asset_type == "overview":
        return {"packet": sdir / "packets" / "overview.json", "runs_dir": sdir / "worker_runs", "workers_receipt": sdir / "workers-receipt.json",
                "log": sdir / "step4.log", "canonical_md": sdir / "canonical" / "overview.md", "validation_receipt": sdir / "validation-receipt.json",
                "preview": sdir / "canonical" / "preview.html", "task_prefix": "", "path_suffix": ""}
    return {"packet": sdir / "packets" / f"{asset_type}.json", "runs_dir": sdir / "worker_runs" / asset_type, "workers_receipt": sdir / f"workers-receipt.{asset_type}.json",
            "log": sdir / f"step4.{asset_type}.log", "canonical_md": sdir / "canonical" / f"{asset_type}.md", "validation_receipt": sdir / f"validation-receipt.{asset_type}.json",
            "preview": sdir / "canonical" / f"{asset_type}-preview.html", "task_prefix": f"{asset_type[:4]}-", "path_suffix": f"{asset_type}/"}


def rel_ref(path: Path) -> str:
    """Artifact reference relative to the lab root (never an absolute local path)."""
    return Path(path).resolve().relative_to(LAB.resolve()).as_posix()


def repository_entity_id(platform: str, native_id) -> str:
    return f"repo_{platform}_{native_id}"


def revision_entity_id(repo_id: str, sha: str) -> str:
    return f"rev_{repo_id}_{short(sha)}"


def analysis_run_id(repo_id: str, sha: str, engine_version: str, config_hash: str) -> str:
    return "analysis_" + short(sha256_bytes(f"{repo_id}|{sha}|{engine_version}|{config_hash}".encode()), 16)


__all__ = [
    "LAB", "SEDB_ROOT", "SEDB_PROJECT", "MACR_ROOT", "MACR_STATE", "LICENSE_POLICY", "TAXONOMY_VERSION",
    "utc_now", "canonical_bytes", "sha256_bytes", "sha256_file", "short", "load_json", "write_json",
    "open_store", "slice_dir", "rel_ref", "repository_entity_id", "revision_entity_id", "analysis_run_id", "asset_paths",
]
