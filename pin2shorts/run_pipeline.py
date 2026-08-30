#!/usr/bin/env python3
"""
run_pipeline.py — one command for the whole AI-Bhagwan pipeline:

  discover (Pinterest search by keyword) -> once (download + render) -> publish (YouTube)

This is what cron / Task Scheduler / a GitHub Actions workflow should call. Each stage
is independently idempotent (manifest.csv / uploads.csv dedupe), so re-running this
after a failure is always safe.
"""
from __future__ import annotations

import sys

import automate
import publish as publish_mod


def main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="discover -> render -> publish, in one run")
    p.add_argument("--upload-limit", type=int, default=1,
                    help="max videos to publish to YouTube this run (default 1 -- "
                         "3 scheduled runs/day = 3 videos/day)")
    p.add_argument("--render-limit", type=int, default=2,
                    help="max queue items to download+render this run (default 2 -- a "
                         "little above upload-limit as headroom for image-only/failed pins)")
    p.add_argument("--skip-discover", action="store_true", help="only render+publish what's already queued")
    p.add_argument("--skip-publish", action="store_true", help="render only, don't touch YouTube")
    p.add_argument("--dry-run-publish", action="store_true", help="publish stage: print, don't upload")
    a = p.parse_args(argv)

    cfg = automate.load_config()

    if not a.skip_discover:
        automate.discover(cfg)

    automate.run_once(cfg, render_limit=a.render_limit)

    if not a.skip_publish:
        publish_mod.publish_pending(limit=a.upload_limit, dry_run=a.dry_run_publish)

    return 0


if __name__ == "__main__":
    sys.exit(main())
