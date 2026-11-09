# TGG auto-poster

Posts The Grey Gym's approved content to **Instagram** and the **Facebook Page** with the Meta Graph API.
LinkedIn is still posted by hand.

- `queue.json`: approved posts with their publish time (Doha time, `+03:00`). Claude adds each week's batch after Angelo approves it.
- `posted.json`: log of what's been published (written by the workflow, so nothing posts twice).
- `post.py`: the poster (Python standard library only).
- `.github/workflows/autopost.yml`: runs every 10 minutes. Anything due is published within about 10–20 minutes of its time. Anything more than 6 hours late is skipped, never posted late.

Secret needed: `META_PAGE_TOKEN` (system-user token for the TGG Auto Poster app). Never paste it anywhere else.

Manual run (Actions → TGG auto-poster → Run workflow):
- **check**: verifies the token and that Meta can fetch every upcoming image/video. Publishes nothing.
- **only_id**: publishes one queue item now (for testing).
- **dry_run**: logs what would post.
