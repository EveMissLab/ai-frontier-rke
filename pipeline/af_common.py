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
SEDB_ROOT = Path("D:/Ai/work together/SEDB")
SEDB_PROJECT = SEDB_ROOT / "projects" / "ai-frontier-repository-intelligence"
MACR_ROOT = Path("D:/Ai/work together/MACR")
MACR_STATE = Path("D:/AI_RESIDENCE/AI_Runtime/macr-state")

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
    "open_store", "slice_dir", "rel_ref", "repository_entity_id", "revision_entity_id", "analysis_run_id",
]
