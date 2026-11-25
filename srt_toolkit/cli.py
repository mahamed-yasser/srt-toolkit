import argparse, os, sys
from . import core, __version__


def _read(path):
    with open(path, "rb") as f:
        return core.parse_srt(core.decode_bytes(f.read()))


def _check_free(path, force):
    if os.path.exists(path) and not force:
        raise ValueError(f"{path} already exists (use --force to overwrite)")
    d = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(d):
        raise ValueError(f"output folder does not exist: {d}")


def _write(path, text, force=False):
    """Write through a temporary file so a failure never leaves a half-written subtitle file."""
    _check_free(path, force)
    tmp = os.path.join(os.path.dirname(os.path.abspath(path)), "." + os.path.basename(path) + ".partial")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _ms(v):
    """Accept 1500, -2.5s, 1500ms or HH:MM:SS,mmm."""
    v = v.strip()
    try:
        if ":" in v:
            return core.ts_to_ms(v)
        if v.endswith("ms"):
            return int(float(v[:-2]))
        if v.endswith("s"):
            return int(round(float(v[:-1]) * 1000))
        return int(v)
    except ValueError:
        raise ValueError(f"bad time: {v!r}")


def _targets(a, ext):
    """Pair each input with an output path: -o for one file, --out-dir for many."""
    if len(a.inputs) == 1 and a.output:
        return [(a.inputs[0], a.output)]
    if a.output:
        raise ValueError("-o works with one input file; use --out-dir for several")
    if not a.out_dir:
        raise ValueError("give -o OUTPUT (one file) or --out-dir DIR (one or more files)")
    if not os.path.isdir(a.out_dir):
        raise ValueError(f"output folder does not exist: {a.out_dir}")
    pairs = []
    for src in a.inputs:
        stem = os.path.splitext(os.path.basename(src))[0]
        pairs.append((src, os.path.join(a.out_dir, stem + ext)))
    return pairs


def _batch(a, ext, transform, fmt=core.format_srt):
    """Plan every output first, refuse on any clash, then convert. Nothing is written if the plan is bad."""
    pairs = _targets(a, ext)
    dsts = [os.path.abspath(d) for _, d in pairs]
    if len(set(dsts)) != len(dsts):
        raise ValueError("two inputs would write the same output file (same name in different folders?)")
    for src, dst in pairs:
        if os.path.abspath(src) == os.path.abspath(dst):
            if not a.force:
                raise ValueError(f"{dst} is the input file (use --force to overwrite it)")
        else:
            _check_free(dst, a.force)
    rendered = []  # convert everything before writing anything, so a bad input stops the whole run cleanly
    for src, dst in pairs:
        try:
            rendered.append((dst, fmt(transform(_read(src)))))
        except ValueError as e:
            raise ValueError(f"{src}: {e}")
    for dst, text in rendered:
        _write(dst, text, True)
    if len(rendered) > 1:
        print(f"{len(rendered)} files written to {a.out_dir}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="srt-toolkit", description="Fix, shift, merge, split and convert SRT subtitles.",
                                epilog="Made by Mahamed Yasser")
    p.add_argument("--version", action="version", version=f"srt-toolkit {__version__} - Made by Mahamed Yasser")
    sub = p.add_subparsers(dest="cmd", required=True)

    def multi(name, help):
        s = sub.add_parser(name, help=help)
        s.add_argument("inputs", nargs="+", metavar="input")
        s.add_argument("-o", "--output"); s.add_argument("--out-dir")
        s.add_argument("--force", action="store_true", help="overwrite existing output")
        return s

    s = multi("shift", "move all cues by an offset (e.g. +2.5s, -800ms)")
    s.add_argument("--by", required=True, help="offset such as +2.5s, -800ms, 1500")

    s = multi("sync", "linear re-sync from two anchor times")
    s.add_argument("--first", required=True, help="new start of first cue")
    s.add_argument("--last", required=True, help="new start of last cue")

    s = multi("vtt", "convert to WebVTT")

    s = multi("fix", "repair encoding, order, numbering and overlaps; optional RTL marks for Arabic")
    s.add_argument("--rtl", action="store_true", help="wrap lines with Arabic letters in right-to-left marks")
    s.add_argument("--min-duration", default=None, help="stretch cues shorter than this (e.g. 1s) without touching the next cue")

    s = multi("clean", "strip <i>/{\\an8} tags, extra spaces, empty and repeated cues")

    s = multi("wrap", "re-flow long lines")
    s.add_argument("--width", type=int, default=42); s.add_argument("--max-lines", type=int, default=2)

    s = sub.add_parser("merge", help="merge two tracks, e.g. Arabic + English")
    s.add_argument("a"); s.add_argument("b"); s.add_argument("--offset-b", default="0")
    s.add_argument("-o", "--output", required=True); s.add_argument("--force", action="store_true")

    s = sub.add_parser("split", help="split at a time into two files")
    s.add_argument("input"); s.add_argument("at"); s.add_argument("--out-before", required=True)
    s.add_argument("--out-after", required=True); s.add_argument("--force", action="store_true")

    s = sub.add_parser("check", help="report problems; exit code 1 if any")
    s.add_argument("inputs", nargs="+", metavar="input"); s.add_argument("--max-cps", type=float, default=21.0)
    s.add_argument("--max-line", type=int, default=0, help="also flag lines longer than this (0 = off)")

    a = p.parse_args(argv)
    try:
        if a.cmd == "shift":
            off = _ms(a.by)
            def sh(c):
                out, dropped, clamped = core.shift_report(c, off)
                if dropped or clamped:
                    print(f"warning: {dropped} cue(s) dropped (would end before 0:00:00), "
                          f"{clamped} cue(s) shortened to start at 0:00:00", file=sys.stderr)
                return out
            _batch(a, ".srt", sh)
        elif a.cmd == "sync":
            f, l = _ms(a.first), _ms(a.last)
            _batch(a, ".srt", lambda c: core.rescale(c, f, l))
        elif a.cmd == "vtt":
            _batch(a, ".vtt", lambda c: c, core.to_vtt)
        elif a.cmd == "fix":
            mn = _ms(a.min_duration) if a.min_duration else None
            def fx(c):
                c = core.fix_overlaps(c)
                return core.ensure_min_duration(c, mn) if mn else c
            _batch(a, ".srt", fx, lambda c: core.format_srt(c, rtl=a.rtl))
        elif a.cmd == "clean":
            _batch(a, ".srt", core.clean)
        elif a.cmd == "wrap":
            _batch(a, ".srt", lambda c: core.wrap(c, a.width, a.max_lines))
        elif a.cmd == "merge":
            if os.path.abspath(a.output) in (os.path.abspath(a.a), os.path.abspath(a.b)) and not a.force:
                raise ValueError("output is one of the input files (use --force to overwrite it)")
            _write(a.output, core.format_srt(core.merge(_read(a.a), _read(a.b), _ms(a.offset_b))),
                   a.force or os.path.abspath(a.output) in (os.path.abspath(a.a), os.path.abspath(a.b)))
        elif a.cmd == "split":
            if os.path.abspath(a.out_before) == os.path.abspath(a.out_after):
                raise ValueError("--out-before and --out-after are the same file")
            _check_free(a.out_before, a.force); _check_free(a.out_after, a.force)
            before, after = core.split_at(_read(a.input), _ms(a.at))
            _write(a.out_before, core.format_srt(before), a.force); _write(a.out_after, core.format_srt(after), a.force)
        elif a.cmd == "check":
            total = 0
            for path in a.inputs:
                cues = _read(path)
                probs = core.validate(cues, a.max_cps)
                if a.max_line:
                    for i, c in enumerate(cues, 1):
                        if any(len(core._plain(ln)) > a.max_line for ln in c.text.split("\n")):
                            probs.append(f"cue {i}: line longer than {a.max_line} characters")
                prefix = f"{path}: " if len(a.inputs) > 1 else ""
                for x in probs:
                    print(prefix + x)
                total += len(probs)
            print(f"{total} problem(s)")
            return 1 if total else 0
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
