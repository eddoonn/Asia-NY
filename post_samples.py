"""Post one sample of every message the stack can send, labelled as a sample.

The preview is built from `test_message_style.every_message()`, which is the
same list the style test validates - so what lands in the channel is the real
render of the real copy, not a mock-up.  Each post carries a `content` line
naming it, so a sample can never be mistaken for a live signal, and the embed
itself is untouched (a marker inside the embed would only prove what the marker
path renders).

    python post_samples.py                  # post them all, paced
    python post_samples.py --dry-run        # render locally, send nothing
    python post_samples.py --only entry     # just the shapes matching a filter
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.error

from notify import load_webhook, send
from discord_style import render
from test_message_style import every_message

# A webhook token allows 5 executions per 2s and 30 per minute; 1.2s keeps the
# whole run inside both windows without a retry path.
PACE_SECONDS = 1.2


def sample_label(count, name):
    """`SAMPLE 3/18 · notify: entry alert`, the line above each embed."""
    return f"SAMPLE {count} · {name} — sample, not a live signal"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true",
                   help="render locally and send nothing")
    p.add_argument("--only", default=None,
                   help="substring filter on the shape name")
    p.add_argument("--pace", type=float, default=PACE_SECONDS,
                   help="seconds between posts (webhook rate limit)")
    args = p.parse_args()

    shapes = every_message()
    if args.only:
        shapes = [s for s in shapes if args.only.lower() in s[0].lower()]
    if not shapes:
        raise SystemExit(f"No message shape matches {args.only!r}")

    webhook = load_webhook()
    if not webhook and not args.dry_run:
        raise SystemExit("No webhook found. Put DISCORD_WEBHOOK=... in .env")

    total = len(shapes)
    for i, (name, embed, _lines) in enumerate(shapes, start=1):
        label = sample_label(f"{i}/{total}", name)
        print(f"[{i}/{total}] {name}")
        if args.dry_run:
            print(label)
            print(render(embed))
            print()
            continue
        try:
            status = send(webhook, embed, content=label)
        except urllib.error.HTTPError as exc:
            # 429 is the one worth naming: it means the pace is too fast, and
            # silently skipping would leave a shape unshown.
            body = exc.read().decode("utf-8", "replace")[:200]
            raise SystemExit(f"HTTP {exc.code} posting {name!r}: {body}")
        print(f"        HTTP {status}")
        if i < total:
            time.sleep(args.pace)

    verb = "rendered" if args.dry_run else "posted"
    print(f"\n{total} message shape(s) {verb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
