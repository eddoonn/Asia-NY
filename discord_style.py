"""One house style for every Discord message this stack sends.

The messages are built in five scripts - `notify.py` (armed, entry, result,
closures), `weekly_digest.py`, `place_orders.py`, `reclaim_monitor.py` and
`end_session.py` - and they land in one channel, so they have to read like one
voice.  The rules:

* the title is `Asia Grab · <subject> · <STATE>`, the state in caps, so the
  phone notification on its own carries the headline (which side, which
  outcome) instead of just the instrument name;
* the description says what happened, in words, in at most three short lines;
* at most five fields: three inline columns for the numbers a trader reads
  first, and one full-width line for the rule or deadline that applies;
* the footer is provenance only - session, clock, source - and repeats nothing
  from above, so the ATR, `TP` and the flatten each appear exactly once, which
  is what `STAMPS` enforces (the *fact* may appear once, even where the same
  number legitimately appears twice - a single-trade session's total is that
  trade's P&L, and the last price can sit exactly on a level);
* no em dashes, and no number repeated inside one line.

`problems()` checks an embed against every rule and `render()` prints it the way
the channel shows it; `test_message_style.py` runs each message the stack can
send through both.
"""
from __future__ import annotations

import re

GREEN, RED, BLUE, GRAY = 0x2ECC71, 0xE74C3C, 0x3498DB, 0x95A5A6
BRAND = "Asia Grab"

MAX_TITLE = 72
MAX_LINE = 110          # description and field-value lines
MAX_FOOTER = 95
MAX_DESCRIPTION_LINES = 3
MAX_FIELDS = 5
MAX_FIELD_LINES = 3

# `time` is the strategy's flatten, `eod` a position that never resolved.
REASONS = {"tp": "target hit", "sl": "stop hit", "time": "time exit",
           "eod": "still on"}

# A price: `4374.00`, not the R multiple `0.75R` that may legitimately repeat.
DECIMAL = re.compile(r"\d+\.\d{2,}(?!R)")

# The facts that may be stated once and only once.  Counting numbers would be
# too blunt - a one-trade session's total *is* that trade's P&L, two legs share
# their stop distance, and the last price can sit exactly on a level - so the
# rule is per fact: the buffer, the target rule and the flatten each get one
# place to live, and the second mention is the redundancy this style removed.
STAMPS = {
    "the ATR/buffer line": re.compile(r"xATR\d+"),
    "the target rule": re.compile(r"\bTP\b"),
    "the flatten deadline": re.compile(r"\b[Ff]lat\b"),
    "the one-trade rule": re.compile(r"1 trade/session"),
}


def title(subject=None, state=None):
    """`Asia Grab · <subject> · <STATE>` - the whole headline in the title."""
    parts = [BRAND]
    if subject:
        parts.append(subject)
    if state:
        parts.append(state)
    return " · ".join(parts)


def stamp(ts):
    """A UTC instant to the minute: `2026-09-11 06:00 UTC`."""
    return f"{ts:%Y-%m-%d %H:%M} UTC"


def rule(*parts):
    """The one full-width line: the rule or deadline that applies here."""
    return " · ".join(str(p) for p in parts if p)


def reason_words(reason):
    """`tp` -> `target hit`, so a result line reads in words, not in code."""
    return REASONS.get(reason, reason)


def trade_level(trade):
    """The NY-late level a trade swept, read back off the trade itself.

    The entry is `level -/+ 1xATR10` and the stop sits one more ATR out, so
    `|entry - stop|` is the ATR10 the order was built from.  Derived rather
    than looked up, so a message can never quote a level the trade didn't use.
    """
    atr = abs(trade["entry"] - trade["sl"])
    if trade["side"] == "long":
        return trade["entry"] + atr, atr
    return trade["entry"] - atr, atr


def swept_line(trade, level):
    """`NY high swept · 4374.00 + 1xATR10 (18.81)` - where the entry came from.

    The sign is the side's: a SHORT fills *above* the level it swept, a LONG
    below it.  The ATR is the one the entry was priced from, so `level ± ATR`
    lands on the entry exactly.
    """
    swept = "NY high swept" if trade["side"] == "short" else "NY low swept"
    atr = abs(trade["entry"] - trade["sl"])
    sign = "+" if trade["side"] == "short" else "-"
    return f"{swept} · {level:.2f} {sign} 1xATR10 ({atr:.2f})"


def render(embed):
    """The message the way the channel shows it, for previews and checks."""
    lines = [embed.get("title", "")]
    if embed.get("description"):
        lines += embed["description"].splitlines()
    for field in embed.get("fields", []):
        for i, line in enumerate(field["value"].splitlines()):
            prefix = f"{field['name']}: " if i == 0 else "  "
            lines.append(prefix + line)
    footer = (embed.get("footer") or {}).get("text")
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def blocks(embed):
    """The message split into the pieces a repetition check works on.

    One block per title / description line / field-value line / footer, so a
    number repeated *inside* one line is a warning while the same level quoted
    by two different sessions in a digest list is not.
    """
    out = [embed.get("title", "")]
    if embed.get("description"):
        out += embed["description"].splitlines()
    for field in embed.get("fields", []):
        out += field["value"].splitlines()
    footer = (embed.get("footer") or {}).get("text")
    if footer:
        out.append(footer)
    return out


def problems(embed, max_lines=MAX_DESCRIPTION_LINES):
    """Every way `embed` breaks the house style, as a list of strings.

    `max_lines` is raised for the weekly digest, whose description is a list of
    sessions and so grows with the data rather than with the copy.
    """
    bad = []
    text = render(embed)

    if "\u2014" in text:
        bad.append("em dash in the copy")

    head = embed.get("title", "")
    if not head.startswith(BRAND + " · "):
        bad.append(f"title does not start with '{BRAND} · ': {head!r}")
    if len(head) > MAX_TITLE:
        bad.append(f"title is {len(head)} chars (max {MAX_TITLE})")
    if head.count(" · ") > 2:
        bad.append(f"title has more than three parts: {head!r}")
    if head.count(" · ") == 2 and head.rsplit(" · ", 1)[1].islower():
        bad.append(f"the state word is not in caps: {head!r}")

    description = embed.get("description") or ""
    lines = description.splitlines()
    if len(lines) > max_lines:
        bad.append(f"description is {len(lines)} lines (max {max_lines})")
    for line in lines:
        if len(line) > MAX_LINE:
            bad.append(f"description line is {len(line)} chars: {line!r}")

    fields = embed.get("fields", [])
    if len(fields) > MAX_FIELDS:
        bad.append(f"{len(fields)} fields (max {MAX_FIELDS})")
    for field in fields:
        value_lines = field["value"].splitlines()
        if len(value_lines) > MAX_FIELD_LINES:
            bad.append(f"field {field['name']!r} is {len(value_lines)} lines "
                       f"(max {MAX_FIELD_LINES})")
        for line in value_lines:
            if len(line) > MAX_LINE:
                bad.append(f"field {field['name']!r} line is {len(line)} chars")

    foot = (embed.get("footer") or {}).get("text")
    if foot is not None:
        if "\n" in foot:
            bad.append("footer is more than one line")
        if len(foot) > MAX_FOOTER:
            bad.append(f"footer is {len(foot)} chars (max {MAX_FOOTER})")

    # A price repeated inside one line is always a mistake; across lines it can
    # be legitimate, so what is checked across the whole message is the *fact*.
    for block in blocks(embed):
        for number in set(DECIMAL.findall(block)):
            if block.count(number) > 1:
                bad.append(f"{number} appears twice in {block!r}")

    # A list of legs may state a stop distance per row, so the check is that the
    # footer does not restate what the body says, and the description does not
    # restate a field - the two redundancies this style removed.
    body = []
    if embed.get("description"):
        body += embed["description"].splitlines()
    fields_text = [f["value"] for f in fields]
    if foot is not None:
        for label, pattern in STAMPS.items():
            if pattern.search(foot) and any(pattern.search(p) for p in body + fields_text):
                bad.append(f"{label} is stated in the footer and above it")
        if embed.get("description"):
            for label, pattern in STAMPS.items():
                if (pattern.search(embed["description"])
                        and any(pattern.search(v) for v in fields_text)):
                    bad.append(f"{label} is stated in the description and a field")
    return bad
