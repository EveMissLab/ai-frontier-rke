"""Preview the validated-but-unpublished pages of a canary batch for human review.

Temporarily drops each validated view model into the site's data directory marked PREVIEW (never a
publication event), builds the site once, serves `dist/` locally, captures one desktop screenshot per page
with headless Edge into `canary/<batch>/previews/`, then removes the temporary files. The portal's real
data directory is left exactly as it was; nothing is committed.

Usage: python preview_batch.py <batch>
"""
from __future__ import annotations

import http.server
import json
import shutil
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

from af_common import LAB, load_json, slice_dir, write_json
from step8_publish import SITE_DATA, SITE_ROOT

EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
PORT = 5199


def main(batch: str) -> int:
    status = load_json(LAB / "canary" / batch / "status.json")
    out_dir = LAB / "canary" / batch / "previews"; out_dir.mkdir(parents=True, exist_ok=True)
    temp, pages = [], []
    for full_name, s in status["repos"].items():
        if not s.get("validated"):
            continue
        view_path = slice_dir(s["slug"]) / "canonical" / "repository-view.json"
        if not view_path.exists():
            continue
        view = load_json(view_path)
        if view["publication"]["status"] == "published":
            continue  # already live; nothing to preview
        view["publication"] = {"status": "published", "approved_by": "PREVIEW ONLY - not approved", "via": "preview_batch.py", "published_at": None, "event_id": "preview", "url": None}
        target = SITE_DATA / f"{view['repository']['owner']}--{view['repository']['name']}.json"
        if target.exists():
            continue
        write_json(target, view); temp.append(target)
        pages.append((s["slug"], f"/ai-frontier/repository/{view['repository']['owner']}/{view['repository']['name']}/"))
    if not pages:
        print(json.dumps({"batch": batch, "previews": 0, "note": "nothing validated and unpublished"})); return 0
    try:
        rc = subprocess.call(["npm.cmd", "run", "build"], cwd=str(SITE_ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert rc == 0, "site build failed"
        dist = SITE_ROOT / "dist"
        handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(dist), **k)  # noqa: E731
        socketserver.TCPServer.allow_reuse_address = True
        httpd = socketserver.TCPServer(("127.0.0.1", PORT), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        shots = []
        for slug, path in pages:
            png = out_dir / f"{slug}.png"
            subprocess.call([str(EDGE), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=1280,2400", f"--screenshot={png}", f"http://127.0.0.1:{PORT}{path}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
            shots.append({"slug": slug, "path": path, "png": str(png), "bytes": png.stat().st_size if png.exists() else 0})
        httpd.shutdown()
    finally:
        for t in temp:
            t.unlink(missing_ok=True)
    print(json.dumps({"batch": batch, "previews": len(shots), "shots": shots, "note": "temporary preview files removed; portal data directory unchanged"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
