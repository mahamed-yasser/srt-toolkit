# SRT Toolkit

**Made by Mahamed Yasser**

Small command-line tool and Python library for subtitle files. Standard library only, Python 3.8+.

It handles the annoying jobs: wrong timing, two languages in one file, old Arabic files saved in Windows-1256, overlapping cues, and WebVTT for the web.

## Install (optional)

```
pip install .        # gives you a `srt-toolkit` command; or just use `python -m srt_toolkit`
```

## Commands

```
srt-toolkit shift  in.srt --by=+2.5s -o out.srt        # also --by=-800ms or --by=00:00:01,500 (use = so a minus sign works)
srt-toolkit sync   in.srt --first 2s --last 1:10:00,000 -o out.srt   # fix drift (linear)
srt-toolkit merge  ar.srt en.srt -o both.srt           # two tracks in one file
srt-toolkit split  in.srt 00:30:00,000 --out-before a.srt --out-after b.srt
srt-toolkit vtt    in.srt -o out.vtt
srt-toolkit fix    in.srt -o out.srt --rtl --min-duration 1s   # encoding, order, numbering, overlaps, short cues
srt-toolkit clean  in.srt -o out.srt                   # strip <i> and {\an8} tags, extra spaces, empty and repeated cues
srt-toolkit wrap   in.srt -o out.srt --width 42        # re-flow long lines to at most 2 lines
srt-toolkit check  in.srt [more.srt ...] [--max-cps 21] [--max-line 42]   # exit 1 if problems
```

`shift`, `sync`, `vtt`, `fix`, `clean` and `wrap` take several files with `--out-dir DIR` instead of `-o`:
`srt-toolkit fix season1/*.srt --out-dir fixed/`

It never overwrites a file (including the input) unless you add `--force`.

Input encoding is detected (UTF-8, UTF-8 with BOM, UTF-16, Windows-1256, then Latin-1). Output is always UTF-8.
`--rtl` wraps lines that contain Arabic letters in right-to-left marks so players that mix directions show them correctly.

## Example

Run on `examples/messy-ar.srt` (Arabic, with tags, an overlap, a duplicate cue and an empty cue). To test the encoding repair too, save a Windows-1256 copy first:

```
python -c "import pathlib; pathlib.Path('messy.srt').write_bytes(pathlib.Path('examples/messy-ar.srt').read_text(encoding='utf-8').encode('cp1256'))"
```

Then:

```
$ srt-toolkit check messy.srt --max-line 42
8 problem(s)
$ srt-toolkit clean messy.srt -o a.srt && srt-toolkit wrap a.srt --width 32 -o b.srt \
    && srt-toolkit fix b.srt -o final.srt --rtl --min-duration 1s
$ srt-toolkit check final.srt
cue 3: reads too fast (27 chars/s)
1 problem(s)
```

Overlaps, tags, the duplicate and the empty cue are gone. The reading-speed warning stays on purpose, since only a human can shorten the text.

## Tests

```
python -m unittest discover -s tests
```

## Things to know

- **Negative shift.** `shift --by=-4s` drops any cue that would end at or before 0:00:00 and moves a cue that would start before 0 to start at 0 (it keeps its end, so it gets shorter). The command prints a warning with both counts. Shifting back later does not restore the original, so keep the source file.
- **Positive shift** is exact and reversible.
- **sync** needs `--last` after `--first` and refuses a stretch that would push any cue before 0:00:00. It maps the start of the first cue and the start of the last cue, so wrong anchors give wrong timing everywhere.
- **fix** trims the earlier cue when cues overlap. Two cues with the same start time leave the first one only 1 ms long (`check` then flags it). Fix those by hand.
- **split** keeps a cue that spans the cut point in the first file, running past the cut. The second file starts at 0:00:00.
- **Timestamps** must look like `HH:MM:SS,mmm` (a `.` instead of `,` is fine). Minutes and seconds above 59 are rejected. Hours can be over 99.
- **Safe output.** Nothing is overwritten (not even the input) without `--force`. For several files, every output is planned and every input is read and converted before anything is written, so a bad file or a name clash stops the run with nothing written. Files are written through a temporary name and then moved into place.

## Limits

- SRT and WebVTT only. No styling tags kept, no positions, no ASS/SSA.
- `fix` repairs overlaps by trimming the earlier cue. It does not shorten text or re-time reading speed.
- `wrap` keeps cues to two lines, so it may make lines longer than `--width` when the text needs it. `check --max-line` will still flag those.
- An encoding guess can be wrong for rare legacy code pages. Check the output once.
- `--rtl` only adds direction marks. It does not reshape Arabic letters, which players do themselves.
- A cue that spans a `split` point stays in the first part.
- `shift` changed in 0.2.0: the offset is now `--by=...` (it was a positional argument in 0.1.0).

## Changelog

- 0.2.0: negative-shift warnings, stricter `sync` and timestamp checks, all-or-nothing batch writes, CI workflow, `clean`, `wrap`, `fix --min-duration`, several files at once with `--out-dir`, `check --max-line`, overwrite protection, `pip install` support.
- 0.1.0: first version.

---
Made by Mahamed Yasser
