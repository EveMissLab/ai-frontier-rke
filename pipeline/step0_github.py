"""Step 0 — GitHub API artifacts for one repository, stored verbatim under <slice>/artifacts/github/.

repo.json, head.json (the default branch's head commit), languages.json, license.json, releases.json —
the shapes step 1 registers from. Unauthenticated REST by default (60 requests/hour per address, five per
repository); a GITHUB_TOKEN in the environment is used only if present and is never written anywhere.

Usage: python step0_github.py <owner/name> <slice_slug>
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

from af_common import slice_dir, utc_now, write_json

API = "https://api.github.com"


def get(path: str, allow_404: bool = False):
    req = urllib.request.Request(API + path, headers={"User-Agent": "evemiss-ai-frontier-rke", "Accept": "application/vnd.github+json",
                                                      **({"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"} if os.environ.get("GITHUB_TOKEN") else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            remaining = resp.headers.get("X-RateLimit-Remaining")
            return json.loads(resp.read().decode("utf-8")), remaining
    except urllib.error.HTTPError as e:
        if e.code == 404 and allow_404:
            return None, e.headers.get("X-RateLimit-Remaining")
        if e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0":
            reset = int(e.headers.get("X-RateLimit-Reset", "0"))
            raise SystemExit(f"GitHub rate limit exhausted; resets in {max(0, reset - int(time.time()))} s")
        raise


def main(full_name: str, slug: str) -> int:
    out = slice_dir(slug) / "artifacts" / "github"
    if (out / "head.json").exists():
        print(json.dumps({"slice": slug, "skipped": "artifacts already present"})); return 0
    repo, rem = get(f"/repos/{full_name}")
    head, _ = get(f"/repos/{full_name}/commits/{repo['default_branch']}")
    languages, _ = get(f"/repos/{full_name}/languages")
    license_api, _ = get(f"/repos/{full_name}/license", allow_404=True)
    releases, rem = get(f"/repos/{full_name}/releases?per_page=3")
    write_json(out / "repo.json", repo)
    write_json(out / "head.json", head)
    write_json(out / "languages.json", languages)
    write_json(out / "license.json", license_api or {"license": None, "path": None, "note": "GitHub license API returned 404 (no license detected)"})
    write_json(out / "releases.json", releases)
    write_json(out / "fetch-receipt.json", {"full_name": full_name, "fetched_at": utc_now(), "default_branch": repo["default_branch"], "head_sha": head["sha"],
                                            "license_api": (license_api or {}).get("license", {}).get("spdx_id") if license_api else None, "rate_limit_remaining": rem})
    print(json.dumps({"slice": slug, "repo": full_name, "head": head["sha"][:12], "language": repo.get("language"), "stars": repo.get("stargazers_count"),
                      "license": ((license_api or {}).get("license") or {}).get("spdx_id"), "rate_limit_remaining": rem}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
