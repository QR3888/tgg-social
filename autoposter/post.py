#!/usr/bin/env python3
"""TGG auto-poster: publishes due items from autoposter/queue.json to Instagram and the
Facebook Page via the Meta Graph API. Standard library only.

Env:
  META_PAGE_TOKEN  system-user token (GitHub secret, never printed)
  FB_PAGE_ID       Facebook Page id
  GRAPH_VERSION    e.g. v23.0
  DRY_RUN          "1" = log what would be posted, call nothing that publishes
  ONLY_ID          post just this queue id now, ignoring its time (manual test)
"""
import json, os, sys, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(ROOT, "queue.json")
POSTED = os.path.join(ROOT, "posted.json")
VER = os.environ.get("GRAPH_VERSION", "v23.0")
G = f"https://graph.facebook.com/{VER}"
TOKEN = os.environ.get("META_PAGE_TOKEN", "")
PAGE_ID = os.environ.get("FB_PAGE_ID", "105927898387533")
DRY = os.environ.get("DRY_RUN", "") == "1"
ONLY = os.environ.get("ONLY_ID", "").strip()
CHECK = os.environ.get("CHECK", "") == "1"   # verify token + media for upcoming items, publish nothing
STALE_AFTER = timedelta(hours=6)   # never post something more than 6 h late


def log(*a):
    print(*a, flush=True)


def call(method, url, params=None, headers=None, token=None):
    params = dict(params or {})
    if token:
        params["access_token"] = token
    data = None
    if method == "GET":
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"{method} {url.split('?')[0]} -> HTTP {e.code}: {body[:800]}")


_cache = {}


def page_token():
    if "pt" not in _cache:
        r = call("GET", f"{G}/{PAGE_ID}", {"fields": "access_token,instagram_business_account"}, token=TOKEN)
        _cache["pt"] = r.get("access_token") or TOKEN
        _cache["ig"] = (r.get("instagram_business_account") or {}).get("id")
        if not _cache["ig"]:
            raise RuntimeError("No Instagram business account linked to the Page (or missing permission).")
    return _cache["pt"]


def ig_id():
    page_token()
    return _cache["ig"]


def wait_container(cid, tok, label):
    for _ in range(60):  # up to ~10 minutes
        r = call("GET", f"{G}/{cid}", {"fields": "status_code,status"}, token=tok)
        sc = r.get("status_code")
        if sc == "FINISHED":
            return
        if sc in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"{label}: container {sc}: {r.get('status')}")
        time.sleep(10)
    raise RuntimeError(f"{label}: container not ready after 10 min")


def ig_image_container(tok, url, alt=None, carousel_item=False, caption=None):
    p = {"image_url": url}
    if carousel_item:
        p["is_carousel_item"] = "true"
    if caption:
        p["caption"] = caption
    if alt:
        p["alt_text"] = alt
    try:
        return call("POST", f"{G}/{ig_id()}/media", p, token=tok)["id"]
    except RuntimeError as e:
        if alt and "alt_text" in str(e):
            p.pop("alt_text")
            return call("POST", f"{G}/{ig_id()}/media", p, token=tok)["id"]
        raise


def post_instagram(item, tok):
    kind, cap = item["kind"], item["caption"]
    if kind == "carousel":
        alts = item.get("alts") or [None] * len(item["media"])
        kids = [ig_image_container(tok, u, a, carousel_item=True) for u, a in zip(item["media"], alts)]
        for k in kids:
            wait_container(k, tok, "carousel item")
        cid = call("POST", f"{G}/{ig_id()}/media",
                   {"media_type": "CAROUSEL", "children": ",".join(kids), "caption": cap}, token=tok)["id"]
    elif kind == "image":
        cid = ig_image_container(tok, item["media"][0], (item.get("alts") or [None])[0], caption=cap)
    elif kind == "reel":
        p = {"media_type": "REELS", "video_url": item["media"][0], "caption": cap, "share_to_feed": "true"}
        if item.get("cover"):
            p["cover_url"] = item["cover"]
        elif item.get("thumb_offset_ms"):
            p["thumb_offset"] = str(item["thumb_offset_ms"])
        cid = call("POST", f"{G}/{ig_id()}/media", p, token=tok)["id"]
    else:
        raise RuntimeError(f"unknown kind {kind}")
    wait_container(cid, tok, item["id"])
    r = call("POST", f"{G}/{ig_id()}/media_publish", {"creation_id": cid}, token=tok)
    return r.get("id")


def post_facebook(item, tok):
    kind, cap = item["kind"], item["caption"]
    if kind == "image":
        r = call("POST", f"{G}/{PAGE_ID}/photos", {"url": item["media"][0], "message": cap, "published": "true"}, token=tok)
        post_id = r.get("post_id") or r.get("id")
    elif kind == "reel":
        start = call("POST", f"{G}/{PAGE_ID}/video_reels", {"upload_phase": "start"}, token=tok)
        vid = start["video_id"]
        up = urllib.request.Request(start.get("upload_url") or f"https://rupload.facebook.com/video-upload/{VER}/{vid}",
                                    method="POST", data=b"",
                                    headers={"Authorization": f"OAuth {tok}", "file_url": item["media"][0]})
        with urllib.request.urlopen(up, timeout=300) as r:
            json.loads(r.read().decode() or "{}")
        for _ in range(30):  # wait for Facebook to fetch the file
            st = call("GET", f"{G}/{vid}", {"fields": "status"}, token=tok).get("status", {})
            if (st.get("uploading_phase") or {}).get("status") == "complete" or st.get("video_status") in ("upload_complete", "processing", "ready"):
                break
            time.sleep(10)
        call("POST", f"{G}/{PAGE_ID}/video_reels",
             {"upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED", "description": cap}, token=tok)
        post_id = vid
    else:
        raise RuntimeError(f"unknown kind {kind}")
    if item.get("first_comment") and post_id:
        time.sleep(5)
        call("POST", f"{G}/{post_id}/comments", {"message": item["first_comment"]}, token=tok)
    return post_id


def check(queue, posted):
    """Validate token, Page/IG link and every upcoming media URL without publishing."""
    tok = page_token()
    log(f"Token OK. Page {PAGE_ID}; Instagram account {ig_id()}")
    bad = 0
    for it in queue:
        if it["id"] in posted:
            continue
        try:
            if it["network"] == "instagram":
                if it["kind"] == "reel":
                    p = {"media_type": "REELS", "video_url": it["media"][0], "caption": "check"}
                    if it.get("cover"):
                        p["cover_url"] = it["cover"]
                    cid = call("POST", f"{G}/{ig_id()}/media", p, token=tok)["id"]
                    wait_container(cid, tok, it["id"])
                else:
                    for u in it["media"]:
                        cid = ig_image_container(tok, u, carousel_item=(it["kind"] == "carousel"))
                        wait_container(cid, tok, it["id"])
                log(f"  CHECK OK  {it['id']} (Instagram fetched all media; nothing published)")
            else:
                if it["kind"] == "image":
                    r = call("POST", f"{G}/{PAGE_ID}/photos", {"url": it["media"][0], "published": "false"}, token=tok)
                    call("DELETE", f"{G}/{r['id']}", token=tok)
                else:
                    req = urllib.request.Request(it["media"][0], method="HEAD")
                    with urllib.request.urlopen(req, timeout=60) as r:
                        assert r.status == 200
                log(f"  CHECK OK  {it['id']} (Facebook media reachable; nothing published)")
        except Exception as e:
            bad += 1
            log(f"  CHECK FAILED {it['id']}: {e}")
    return 1 if bad else 0


def main():
    queue = json.load(open(QUEUE))
    posted = json.load(open(POSTED)) if os.path.exists(POSTED) else {}
    if CHECK:
        if not TOKEN:
            log("META_PAGE_TOKEN secret is missing."); return 1
        return check(queue, posted)
    now = datetime.now(timezone.utc)
    due = []
    for it in queue:
        if it["id"] in posted:
            continue
        if ONLY:
            if it["id"] == ONLY:
                due.append(it)
            continue
        at = datetime.fromisoformat(it["publish_at"])
        if at <= now <= at + STALE_AFTER:
            due.append(it)
        elif now > at + STALE_AFTER:
            log(f"SKIP stale {it['id']} (was due {it['publish_at']})")
    if ONLY and not due:
        log(f"ONLY_ID {ONLY} not found or already posted"); return 1
    if not due:
        log("Nothing due."); return 0
    if not TOKEN and not DRY:
        log("META_PAGE_TOKEN secret is missing."); return 1
    failures = 0
    for it in due:
        log(f"{'DRY RUN ' if DRY else ''}POST {it['id']} -> {it['network']} {it['kind']} ({len(it['media'])} media)")
        if DRY:
            continue
        try:
            tok = page_token()
            rid = post_instagram(it, tok) if it["network"] == "instagram" else post_facebook(it, tok)
            posted[it["id"]] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "result_id": rid}
            log(f"  OK {it['id']} -> {rid}")
        except Exception as e:  # keep going with the other posts
            failures += 1
            log(f"  FAILED {it['id']}: {e}")
        json.dump(posted, open(POSTED, "w"), indent=1)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
