"""Pure text helpers for the Code style (no Qt, so they are easy to test)."""
import math
import re
import zlib

PRONOUNS = {"i", "me", "my", "im", "ill", "ive", "id", "you", "we"}   # highlighted like keywords


def clean_word(w):
    w = re.sub(r"[^\w'’]+", "", w.lower())
    return w.replace("'", "").replace("’", "")


def code_tokens(words):
    """['Midnight,', 'Drive!'] or [(text, t0, t1), ...] -> ['midnight', 'drive']"""
    return [t for t in (clean_word(w[0] if isinstance(w, tuple) else w) for w in words) if t]


def code_kind(tokens):
    """How a finished line is written: 'dot' (a.b.c), 'comment' (// a b c) or 'call' (a(b, c);).
    Deterministic per line, so it never flickers."""
    if len(tokens) < 2:
        return "dot"
    r = zlib.crc32(" ".join(tokens).encode("utf-8")) % 10
    return "dot" if r < 6 else "comment" if r < 8 else "call"


def code_segments(words):
    """Finished line -> [(text, role)] with role in word|pron|dot|comment|name|arg|punct, plus its kind."""
    tokens = code_tokens(words)
    kind = code_kind(tokens)
    seg = []
    if kind == "comment":
        seg = [("// " + " ".join(tokens), "comment")]
    elif kind == "call":
        seg = [(tokens[0], "name"), ("(", "punct")]
        for n, t in enumerate(tokens[1:]):
            seg.append((t, "pron" if t in PRONOUNS else "arg"))
            if n < len(tokens) - 2:
                seg.append((", ", "punct"))
        seg.append((");", "punct"))
    else:
        for n, t in enumerate(tokens):
            seg.append((t, "pron" if t in PRONOUNS else "word"))
            if n < len(tokens) - 1:
                seg.append((".", "dot"))
    return seg, kind


def typed_text(words, now, speed=0.75):
    """Text typed so far for the current line: each word's letters appear over the first `speed`
    of that word's time, so the typing keeps pace with the singer."""
    out = []
    for w, t0, t1 in words:
        if now < t0:
            break
        frac = 1.0 if t1 <= t0 else min(1.0, (now - t0) / max((t1 - t0) * speed, 0.05))
        out.append(w[:max(1, math.ceil(len(w) * frac))])
    return " ".join(out)
