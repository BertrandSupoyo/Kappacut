"""End-to-end check against the RUNNING dev stack, through Caddy, as a real user.

Not collected by pytest/CI (no `test_` prefix): it needs the compose stack up, spends a
little Groq credit (two short analyses) and renders with ffmpeg. It creates throwaway
projects named pathcheck-* / and deletes them at the end, success or not.

Run it inside the web container, pointing it at any READY project whose video it may cut a
60-second test clip from (that project itself is only read, never changed):

    docker exec -i -e E2E_SOURCE_PROJECT=<project uuid> clipfinder-web-1 \
        python - < tests/e2e_live.py

Exit code 0 = every check passed.
"""
import asyncio
import base64
import json
import os
import subprocess
import sys
import time
import uuid

import httpx

BASE = os.environ.get("E2E_BASE", "http://caddy")
SRC_PID = uuid.UUID(os.environ["E2E_SOURCE_PROJECT"])
results: list[bool] = []


def ok(name, cond, detail=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail else ""), flush=True)


async def _setup():
    from app import storage
    from app.auth.backend import get_jwt_strategy
    from app.db import async_session_maker, engine
    from app.models.project import Project
    from app.models.user import User
    async with async_session_maker() as s:
        p = await s.get(Project, SRC_PID)
        u = await s.get(User, p.user_id)
    storage.client().download_file(storage._bucket(), storage.find_source(p.user_id, p.id), "/tmp/e2e_src.mp4")
    tok = await get_jwt_strategy().write_token(u)
    await engine.dispose()
    return tok

tok = asyncio.run(_setup())
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "40", "-t", "60", "-i", "/tmp/e2e_src.mp4",
                "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "/tmp/e2e.mp4"], check=True)
CSRF = "e2e-csrf"
c = httpx.Client(base_url=BASE, cookies={"cfa": tok, "csrftoken": CSRF}, headers={"X-CSRF-Token": CSRF}, timeout=30)


def sse(path, limit=900):
    msgs, t0 = [], time.time()
    with c.stream("GET", path, timeout=httpx.Timeout(limit, connect=10)) as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                msgs.append(json.loads(line[6:]))
            if time.time() - t0 > limit:
                break
    return msgs


def tus_upload(pid, path, name):
    data = open(path, "rb").read()
    meta = ",".join(f"{k} {base64.b64encode(v.encode()).decode()}" for k, v in
                    (("projectId", str(pid)), ("filename", name)))
    r = c.post("/files/", headers={"Tus-Resumable": "1.0.0", "Upload-Length": str(len(data)),
                                   "Upload-Metadata": meta})
    if r.status_code != 201:
        return r.status_code, r.text.strip()[:120]
    loc = r.headers["Location"]
    loc = loc if loc.startswith("/") else "/" + loc.split("/", 3)[-1]
    p = c.patch(loc, content=data, headers={"Tus-Resumable": "1.0.0", "Upload-Offset": "0",
                                            "Content-Type": "application/offset+octet-stream"}, timeout=120)
    return p.status_code, len(data)


created = []
try:
    # services + guards
    ok("health", c.get("/api/health").json().get("ok") is True)
    ok("session cookie auth", c.get("/api/users/me").status_code == 200)
    ok("CSRF rejects a POST without the header",
       httpx.post(BASE + "/api/projects", json={"filename": "x.mp4"}, cookies={"cfa": tok}).status_code == 403)

    # upload -> hook -> analyze -> auto render
    r = c.post("/api/projects", json={"filename": "pathcheck-auto.mp4", "taste": "3 clips.", "auto_render": True})
    pid = r.json()["id"]; created.append(pid)
    ok("create project (status uploading)", c.get(f"/api/projects/{pid}").json()["status"] == "uploading")
    code, n = tus_upload(pid, "/tmp/e2e.mp4", "pathcheck-auto.mp4")
    ok("tus upload via Caddy -> tusd", code == 204, f"{n} bytes")
    time.sleep(1.5)
    ok("post-finish hook queued analysis", c.get(f"/api/projects/{pid}").json()["status"] in ("queued", "analyzing"))
    msgs = sse(f"/api/projects/{pid}/analyze/events")
    ok("analysis streams progress and finishes", msgs and msgs[-1].get("status") == "done",
       " -> ".join(dict.fromkeys(m["msg"] for m in msgs if m.get("msg"))))
    pj = c.get(f"/api/projects/{pid}").json()
    clips = (pj.get("analysis") or {}).get("clips") or []
    ok("ready with clips, each with a 0-100 score", pj["status"] == "ready" and clips
       and all(isinstance(x.get("virality"), int) for x in clips), f"{len(clips)} clips")
    ok("clips are at least 15 s", all(x["duration"] >= 14.9 for x in clips), str([x["duration"] for x in clips]))
    time.sleep(1.0)
    rl = (sse(f"/api/projects/{pid}/render/events") or [{}])[-1]
    ok("auto-render finished", rl.get("status") == "done", f"{rl.get('done')}/{rl.get('total')}")

    # edits + manual render + one-job guard + upload refused while busy
    ok("first edit save", c.post(f"/api/projects/{pid}/edit", json={"state": {"v": 2, "activeIdx": 0, "edits": {}}}).status_code == 200)
    c0 = clips[0]
    rng = {"start": c0["start_seconds"], "end": c0["start_seconds"] + 8, "vertical": True, "label": "pathcheck",
           "crop": {"mode": "cropfill", "px": .5, "py": .5, "zoom": 1.2, "blur": 24},
           "captions": {"style": "bold", "position": "bottom", "size": 1, "anim": "pop", "karaoke": True},
           "color": {"preset": "punchy", "b": -0.2, "c": 1, "s": 1}, "sfx": [{"file": "whoosh.m4a", "at": 0, "gain": -3}]}
    ok("manual render accepted", c.post(f"/api/projects/{pid}/render", json={"ranges": [rng]}).status_code == 200)
    ok("second job refused while one runs (429)", c.post(f"/api/projects/{pid}/render", json={"ranges": [rng]}).status_code == 429)
    bid = c.post("/api/projects", json={"filename": "pathcheck-busy.mp4"}).json()["id"]; created.append(bid)
    code, _ = tus_upload(bid, "/tmp/e2e.mp4", "pathcheck-busy.mp4")
    ok("upload refused at pre-create while busy", code != 204, f"HTTP {code}")
    rl = (sse(f"/api/projects/{pid}/render/events") or [{}])[-1]
    ok("manual render finished", rl.get("status") == "done")
    outs = c.get(f"/api/projects/{pid}/outputs").json()["clips"]
    mine = [o for o in outs if "pathcheck" in o["file"]]
    m = c.get(mine[0]["url"], follow_redirects=False) if mine else None
    ok("/media clip -> 302 presigned URL", m is not None and m.status_code == 302 and "X-Amz-Signature" in m.headers.get("location", ""))

    # re-analyze a ready project: edits cleared, new clips stored
    r = c.post(f"/api/projects/{pid}/analyze")
    ok("re-analyze a ready project accepted", r.status_code == 200, r.text[:60])
    ok("a second re-analyze while queued is refused (409)", c.post(f"/api/projects/{pid}/analyze").status_code == 409)
    last = (sse(f"/api/projects/{pid}/analyze/events") or [{}])[-1]
    ok("re-analysis finished", last.get("status") == "done")
    ok("old edits cleared after re-analysis", c.get(f"/api/projects/{pid}/edit").json()["state"] is None)

    # request validation
    ok("bad render range rejected (400)",
       c.post(f"/api/projects/{pid}/render", json={"ranges": [{"start": 5, "end": 2}]}).status_code == 400)
    ok("missing project -> 404", c.get(f"/api/projects/{uuid.uuid4()}").status_code == 404)
finally:
    for p in created:
        print(f"cleanup delete {p}: {c.delete(f'/api/projects/{p}').status_code}", flush=True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
