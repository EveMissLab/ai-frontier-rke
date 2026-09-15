"""Step 9 — freshness: does the source repository still match the revision a published guide describes?

Daily, for every published repository page: read GitHub's default-branch head and repository metadata
(two unauthenticated requests per repository), then

1. append an immutable metadata snapshot (stars, forks, watchers, open issues, pushed_at) — the trend
   continuity Weekly Frontier needs;
2. if the head moved, register the newly observed revision (immutable) and move the repository's
   freshness state to `changed_unassessed` (the analysis is not stale until a semantic diff says which
   pages it affects; the page says so instead of pretending);
3. write the result into the portal's view model (`freshness` block) so the page shows a notice.

No model, no publication. Usage: python step9_freshness.py [owner/name ...]   (default: every published page)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from af_common import load_json, open_store, revision_entity_id, utc_now, write_json
from step0_github import get
from step8_publish import SITE_DATA


def main(only: list[str]) -> int:
    store = open_store()
    now = utc_now()
    out = []
    for view_path in sorted(SITE_DATA.glob("*--*.json")):
        view = load_json(view_path)
        if view.get("publication", {}).get("status") != "published":
            continue
        full = view["repository"]["full_name"]
        if only and full not in only:
            continue
        repo_id = view["repository_id"]
        analyzed = view["analysis"]["analyzed_revision"]
        repo, _ = get(f"/repos/{full}")
        head, remaining = get(f"/repos/{full}/commits/{repo['default_branch']}")
        head_sha = head["sha"]
        snap_id = f"snap_{repo_id}_{now.replace(':', '').replace('-', '')}"
        records = [{"entity_id": snap_id, "kind": "af_metadata_snapshot", "label": f"{full} metadata {now}", "values": {
            "af_repository_id": repo_id, "af_observed_at": now, "af_stars": repo.get("stargazers_count"), "af_forks": repo.get("forks_count"),
            "af_watchers": repo.get("subscribers_count"), "af_open_issues": repo.get("open_issues_count"), "af_pushed_at": repo.get("pushed_at"),
            "af_default_branch": repo.get("default_branch"), "af_provenance_source": "github_api"}}]
        moved = head_sha != analyzed
        state = "changed_unassessed" if moved else "fresh"
        latest_rev = revision_entity_id(repo_id, head_sha)
        if moved and store.get(latest_rev) is None:
            records.append({"entity_id": latest_rev, "kind": "af_repository_revision", "label": f"{full}@{head_sha[:12]}", "values": {
                "af_repository_id": repo_id, "af_branch": repo.get("default_branch"), "af_commit_sha": head_sha, "af_tag": None,
                "af_committed_at": head.get("commit", {}).get("committer", {}).get("date"), "af_observed_at": now, "af_provenance_source": "github_api"}})
        records.append({"entity_id": f"fresh_{repo_id}", "kind": "af_freshness_state", "label": f"{full} freshness", "values": {
            "af_entity_type": "repository", "af_entity_id": repo_id, "af_last_verified_revision_id": revision_entity_id(repo_id, analyzed),
            "af_latest_observed_revision_id": latest_rev, "af_freshness": state,
            "af_reason": ("default-branch head moved after the analyzed revision; pages not yet reassessed" if moved else "default-branch head equals the analyzed revision"),
            "af_updated_at": now, "af_provenance_source": "system"}})
        res = store.write(records, source="freshness")
        view["freshness"] = {"state": state, "checked_at": now, "latest_observed_revision": head_sha, "latest_observed_url": f"{view['repository']['github_url']}/tree/{head_sha}",
                             "latest_committed_at": head.get("commit", {}).get("committer", {}).get("date"), "stars_now": repo.get("stargazers_count"),
                             "note": None if not moved else "The default branch has moved past the analyzed revision. This guide still describes the analyzed revision; a semantic diff decides which pages need revalidation, and this notice stays until then."}
        write_json(view_path, view)
        out.append({"repository": full, "state": state, "analyzed": analyzed[:12], "head": head_sha[:12], "stars": repo.get("stargazers_count"), "sedb": res.__dict__, "rate_limit_remaining": remaining})
        print(json.dumps(out[-1], ensure_ascii=False), flush=True)
    print(json.dumps({"checked": len(out), "changed": sum(1 for o in out if o["state"] != "fresh"), "at": now}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
