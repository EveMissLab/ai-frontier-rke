"""Step 1 — register the repository in SEDB canonical memory.

GitHub API artifacts (already fetched, stored under artifacts/github) become:
repository identity, revision ledger entry, metadata snapshot, raw topics,
license record (GitHub detection + license file bytes at the revision +
RepoLumen detection, kept as separate observation sources), taxonomy v1 seed,
and the repository freshness state. Nothing here is model-generated.

Usage: python step1_register.py <slice_slug>
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request

from af_common import (
    LICENSE_POLICY, TAXONOMY_VERSION, load_json, open_store, repository_entity_id,
    revision_entity_id, slice_dir, utc_now, write_json,
)

sys.path.insert(0, str(slice_dir("pipeline")))
from cli import category_records  # noqa: E402  (SEDB project CLI, taxonomy seed)


def fetch_license_file(full_name: str, sha: str, path: str) -> tuple[str | None, int | None]:
    url = f"https://raw.githubusercontent.com/{full_name}/{sha}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "evemiss-ai-frontier-rke-slice"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        return hashlib.sha256(data).hexdigest(), len(data)
    except Exception:
        return None, None


def main(slug: str) -> int:
    sdir = slice_dir(slug)
    gh = sdir / "artifacts" / "github"
    repo = load_json(gh / "repo.json")
    head = load_json(gh / "head.json")
    languages = load_json(gh / "languages.json")
    license_api = load_json(gh / "license.json")
    releases = load_json(gh / "releases.json")
    observed_at = utc_now()

    repo_id = repository_entity_id("github", repo["id"])
    sha = head["sha"]
    rev_id = revision_entity_id(repo_id, sha)
    full_name = repo["full_name"]

    # License: three observation sources kept separate, then one state.
    lic_path = license_api.get("path") or "LICENSE"
    lic_file_sha256, lic_bytes = fetch_license_file(full_name, sha, lic_path)
    api_spdx = (repo.get("license") or {}).get("spdx_id")
    sources = ["github_api"] + (["repository_file"] if lic_file_sha256 else [])
    # RepoLumen's own detection is recorded when the analysis run is committed (step 3);
    # it must agree with api_spdx or the state becomes 'unresolved' there.
    if api_spdx and api_spdx not in ("NOASSERTION", "OTHER"):
        status = "open-source"
    elif api_spdx in ("NOASSERTION", "OTHER"):
        status = "custom"
    elif api_spdx is None and lic_file_sha256 is None:
        status = "none"
    else:
        status = "unknown"
    confidence = 1.0 if (lic_file_sha256 and api_spdx) else 0.8

    latest_release = releases[0] if isinstance(releases, list) and releases else {}
    snap_id = f"snap_{repo_id}_{observed_at.replace(':', '').replace('-', '')}"
    topics = repo.get("topics") or []

    records = [
        {"entity_id": repo_id, "kind": "af_repository", "label": full_name, "values": {
            "af_platform": "github",
            "af_platform_repository_id": str(repo["id"]),
            "af_platform_node_id": repo.get("node_id"),
            "af_owner": repo["owner"]["login"],
            "af_name": repo["name"],
            "af_full_name": full_name,
            "af_canonical_source_url": repo["html_url"],
            "af_default_branch": repo.get("default_branch"),
            "af_repository_status": "archived" if repo.get("archived") else "active",
            "af_source_description": repo.get("description"),
            "af_homepage_url": repo.get("homepage") or None,
            "af_is_fork": bool(repo.get("fork")),
            "af_source_created_at": repo.get("created_at"),
            "af_provenance_source": "github_api",
            "af_observed_at": observed_at,
        }},
        {"entity_id": rev_id, "kind": "af_repository_revision", "label": f"{full_name}@{sha[:12]}", "values": {
            "af_repository_id": repo_id,
            "af_branch": repo.get("default_branch"),
            "af_commit_sha": sha,
            "af_tag": None,
            "af_committed_at": head.get("commit", {}).get("committer", {}).get("date"),
            "af_observed_at": observed_at,
            "af_provenance_source": "github_api",
        }},
        {"entity_id": snap_id, "kind": "af_metadata_snapshot", "label": f"{full_name} metadata {observed_at}", "values": {
            "af_repository_id": repo_id,
            "af_observed_at": observed_at,
            "af_stars": repo.get("stargazers_count"),
            "af_forks": repo.get("forks_count"),
            "af_watchers": repo.get("subscribers_count"),
            "af_open_issues": repo.get("open_issues_count"),
            "af_primary_language": repo.get("language"),
            "af_language_bytes": languages,
            "af_size_kb": repo.get("size"),
            "af_archived": bool(repo.get("archived")),
            "af_pushed_at": repo.get("pushed_at"),
            "af_latest_release_tag": latest_release.get("tag_name"),
            "af_latest_release_at": latest_release.get("published_at"),
            "af_raw_topics": topics,
            "af_provenance_source": "github_api",
        }},
        {"entity_id": f"lic_{rev_id}", "kind": "af_license_record", "label": f"{full_name}@{sha[:12]} license", "values": {
            "af_repository_id": repo_id,
            "af_revision_id": rev_id,
            "af_detected_spdx": api_spdx,
            "af_license_status": status,
            "af_license_file_path": lic_path if lic_file_sha256 else None,
            "af_license_file_sha256": lic_file_sha256,
            "af_license_source": "+".join(sources),
            "af_license_policy": LICENSE_POLICY[status],
            "af_confidence": confidence,
            "af_observed_at": observed_at,
            "af_provenance_source": "github_api",
        }},
        {"entity_id": f"fresh_{repo_id}", "kind": "af_freshness_state", "label": f"{full_name} freshness", "values": {
            "af_entity_type": "repository",
            "af_entity_id": repo_id,
            "af_last_verified_revision_id": rev_id,
            "af_latest_observed_revision_id": rev_id,
            "af_freshness": "fresh",
            "af_reason": "first observation; analyzed revision equals latest observed revision",
            "af_updated_at": observed_at,
            "af_provenance_source": "system",
        }},
    ]
    for slug_t in topics:
        records.append({"entity_id": f"topic_{slug_t.replace('-', '_')}", "kind": "af_topic", "label": slug_t, "values": {
            "af_topic_slug": slug_t, "af_display_name": slug_t, "af_topic_source": "github", "af_provenance_source": "github_api",
        }})
        records.append({"entity_id": f"rt_{repo_id}_{slug_t.replace('-', '_')}", "kind": "af_repository_topic", "label": f"{full_name} · {slug_t}", "values": {
            "af_repository_id": repo_id, "af_topic_id": f"topic_{slug_t.replace('-', '_')}", "af_observed_at": observed_at, "af_provenance_source": "github_api",
        }})
    records.extend(category_records())

    store = open_store()
    result = store.write(records, source="github_api", confidence=None)

    # Rule-based first pass for the taxonomy classifier (Paper 06 §21): candidates only.
    desc = (repo.get("description") or "").lower()
    candidates = set()
    if any(t in topics for t in ("ai", "llm", "llms", "openai", "machine-learning", "deep-learning")):
        candidates.update(["artificial-intelligence", "llm-serving", "local-ai", "ai-infrastructure", "ai-agents"])
    if "command-line" in desc or "cli" in desc.split():
        candidates.update(["developer-tools", "cli-tools"])
    if not candidates:
        candidates.add("other")

    receipt = {
        "slice": slug,
        "repository_id": repo_id,
        "revision_id": rev_id,
        "commit_sha": sha,
        "snapshot_id": snap_id,
        "license_record_id": f"lic_{rev_id}",
        "license": {"spdx": api_spdx, "status": status, "sources": sources, "file_sha256": lic_file_sha256, "file_bytes": lic_bytes},
        "taxonomy_version": TAXONOMY_VERSION,
        "candidate_categories": sorted(candidates),
        "observed_at": observed_at,
        "sedb_write": result.__dict__,
    }
    write_json(sdir / "registration-receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
