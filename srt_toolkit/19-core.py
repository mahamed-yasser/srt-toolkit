"""Subtitle logic. Pure Python, standard library only."""
import re
import textwrap
from dataclasses import dataclass, replace
from typing import List

TS = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")
ARROW = re.compile(r"^\s*(\S+)\s*-->\s*(\S+)")
RLE = "\u202b"  # right-to-left embedding
PDF = "\u202c"
ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")


@dataclass(frozen=True)
class Cue:
    index: int
    start: int  # milliseconds
    end: int
    text: str


def ts_to_ms(ts: str) -> int:
    m = TS.fullmatch(ts.strip())
    if not m:
        raise ValueError(f"bad timestamp: {ts!r}")
    h, mi, s, frac = m.groups()
    if int(mi) > 59 or int(s) > 59:
        raise ValueError(f"bad timestamp (minutes and seconds must be 00-59): {ts!r}")
    return ((int(h) * 60 + int(mi)) * 60 + int(s)) * 1000 + int(frac.ljust(3, "0"))


def ms_to_ts(ms: int, sep: str = ",") -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, f = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{f:03d}"


def decode_bytes(data: bytes) -> str:
    """Decode subtitle bytes. Tries BOM/UTF-8, then UTF-16, then Windows-1256 (Arabic), then Latin-1."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1256")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def parse_srt(text: str) -> List[Cue]:
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        i = 0
        while i < len(lines) and not ARROW.match(lines[i]):
            i += 1
        if i >= len(lines):
            if block.strip():
                raise ValueError(f"block without a timing line: {block[:40]!r}")
            continue
        m = ARROW.match(lines[i])
        start, end = ts_to_ms(m.group(1)), ts_to_ms(m.group(2))
        idx = int(lines[0]) if i > 0 and lines[0].strip().isdigit() else len(cues) + 1
        cues.append(Cue(idx, start, end, "\n".join(lines[i + 1:]).strip("\n")))
    return cues


def format_srt(cues: List[Cue], rtl: bool = False) -> str:
    out = []
    for n, c in enumerate(cues, 1):
        body = c.text
        if rtl:
            body = "\n".join(RLE + ln + PDF if ARABIC.search(ln) and not ln.startswith(RLE) else ln for ln in body.split("\n"))
        out.append(f"{n}\n{ms_to_ts(c.start)} --> {ms_to_ts(c.end)}\n{body}\n")
    return "\n".join(out)


def to_vtt(cues: List[Cue]) -> str:
    out = ["WEBVTT\n"]
    for c in cues:
        out.append(f"{ms_to_ts(c.start, '.')} --> {ms_to_ts(c.end, '.')}\n{c.text}\n")
    return "\n".join(out)


def shift_report(cues: List[Cue], offset_ms: int):
    """Move every cue by offset_ms. Returns (cues, dropped, clamped).
    A cue that would end at or before 0 is dropped; one that would start before 0 is clamped to start at 0
    (so it keeps its end time and becomes shorter). Both counts are returned so callers can warn."""
    out, dropped, clamped = [], 0, 0
    for c in cues:
        s, e = c.start + offset_ms, c.end + offset_ms
        if e <= 0:
            dropped += 1
            continue
        if s < 0:
            clamped += 1
        out.append(replace(c, start=max(0, s), end=e))
    return out, dropped, clamped


def shift(cues: List[Cue], offset_ms: int) -> List[Cue]:
    return shift_report(cues, offset_ms)[0]


def rescale(cues: List[Cue], first_ms: int, last_ms: int) -> List[Cue]:
    """Linear re-sync: map the first cue start to first_ms and the last cue start to last_ms."""
    if first_ms < 0 or last_ms <= first_ms:
        raise ValueError("--first must be 0 or later and --last must be after --first")
    if len(cues) < 2:
        return shift(cues, first_ms - cues[0].start) if cues else []
    a, b = cues[0].start, cues[-1].start
    if a == b:
        raise ValueError("first and last cue start at the same time")
    k = (last_ms - first_ms) / (b - a)
    f = lambda t: int(round(first_ms + (t - a) * k))
    out = [replace(c, start=f(c.start), end=f(c.end)) for c in cues]
    if any(c.start < 0 or c.end < 0 for c in out):
        raise ValueError("this re-sync would move some cues before 0:00:00; pick different anchor times")
    return out


def merge(a: List[Cue], b: List[Cue], offset_b_ms: int = 0) -> List[Cue]:
    """Combine two tracks (for example two languages) ordered by start time."""
    allc = list(a) + shift(b, offset_b_ms)
    allc.sort(key=lambda c: (c.start, c.end))
    return renumber(allc)


def split_at(cues: List[Cue], at_ms: int):
    """Split into (before, after). A cue spanning the split goes to 'before'. 'after' is re-based to 0."""
    before = [c for c in cues if c.start < at_ms]
    after = [replace(c, start=c.start - at_ms, end=c.end - at_ms) for c in cues if c.start >= at_ms]
    return renumber(before), renumber(after)


def renumber(cues: List[Cue]) -> List[Cue]:
    return [replace(c, index=i) for i, c in enumerate(cues, 1)]


def fix_overlaps(cues: List[Cue], gap_ms: int = 1) -> List[Cue]:
    out = sorted(cues, key=lambda c: c.start)
    out = list(out)
    for i in range(len(out) - 1):
        if out[i].end > out[i + 1].start:
            out[i] = replace(out[i], end=max(out[i].start + 1, out[i + 1].start - gap_ms))
    return renumber(out)


def _plain(t: str) -> str:
    return t.replace("\n", "").replace(RLE, "").replace(PDF, "")


def validate(cues: List[Cue], max_cps: float = 21.0) -> List[str]:
    """Return human-readable problems. Empty list means clean."""
    problems = []
    for i, c in enumerate(cues):
        if c.end <= c.start:
            problems.append(f"cue {i+1}: end is not after start")
        if not c.text.strip():
            problems.append(f"cue {i+1}: empty text")
        dur = (c.end - c.start) / 1000
        if dur > 0 and len(_plain(c.text)) / dur > max_cps:
            problems.append(f"cue {i+1}: reads too fast ({len(_plain(c.text))/dur:.0f} chars/s)")
        if i + 1 < len(cues) and c.end > cues[i + 1].start:
            problems.append(f"cue {i+1}: overlaps cue {i+2}")
        if i + 1 < len(cues) and c.start > cues[i + 1].start:
            problems.append(f"cue {i+1}: out of order")
    return problems


TAG = re.compile(r"<[^>]{1,40}>|\{\\[^}]*\}")


def clean(cues: List[Cue]) -> List[Cue]:
    """Strip HTML/ASS tags, trim spaces, drop cues left empty, merge repeated identical neighbours."""
    out: List[Cue] = []
    for c in cues:
        lines = [re.sub(r"[ \t]+", " ", TAG.sub("", ln)).strip() for ln in c.text.split("\n")]
        text = "\n".join(ln for ln in lines if ln)
        if not text:
            continue
        if out and out[-1].text == text and c.start <= out[-1].end + 500:
            out[-1] = replace(out[-1], end=max(out[-1].end, c.end))
            continue
        out.append(replace(c, text=text))
    return renumber(out)


def wrap(cues: List[Cue], width: int = 42, max_lines: int = 2) -> List[Cue]:
    """Re-flow each cue to lines of at most `width` characters. Warns by raising if it needs more than max_lines."""
    if width < 10:
        raise ValueError("width must be at least 10")
    out = []
    for c in cues:
        flat = " ".join(c.text.split())
        lines = textwrap.wrap(flat, width=width, break_long_words=False, break_on_hyphens=False) or [""]
        if len(lines) > max_lines:
            lines = textwrap.wrap(flat, width=max(width, -(-len(flat) // max_lines) + 4),
                                  break_long_words=False, break_on_hyphens=False)
        out.append(replace(c, text="\n".join(lines)))
    return out


def ensure_min_duration(cues: List[Cue], min_ms: int = 1000, gap_ms: int = 1) -> List[Cue]:
    """Stretch very short cues, but never into the next cue."""
    out = list(cues)
    for i, c in enumerate(out):
        if c.end - c.start < min_ms:
            limit = out[i + 1].start - gap_ms if i + 1 < len(out) else c.start + min_ms
            out[i] = replace(c, end=max(c.end, min(c.start + min_ms, limit)))
    return out
