import os, subprocess, sys, tempfile, unittest
from srt_toolkit import *

SAMPLE = "1\n00:00:01,000 --> 00:00:02,500\nمرحبا بالعالم\n\n2\n00:00:03,000 --> 00:00:05,000\nSecond line\nwith two rows\n"


class Core(unittest.TestCase):
    def test_roundtrip(self):
        cues = parse_srt(SAMPLE)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[1].text, "Second line\nwith two rows")
        self.assertEqual(parse_srt(format_srt(cues)), renumber(cues))

    def test_timestamps(self):
        self.assertEqual(ts_to_ms("01:02:03,456"), 3723456)
        self.assertEqual(ts_to_ms("00:00:01.5"), 1500)
        self.assertEqual(ms_to_ts(3723456), "01:02:03,456")
        with self.assertRaises(ValueError):
            ts_to_ms("nonsense")

    def test_crlf_and_bom(self):
        cues = parse_srt("\ufeff" + SAMPLE.replace("\n", "\r\n"))
        self.assertEqual(len(cues), 2)

    def test_shift_clamps_and_drops(self):
        cues = shift(parse_srt(SAMPLE), -2000)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].start, 0)
        self.assertEqual(cues[0].end, 500)
        self.assertEqual(len(shift(parse_srt(SAMPLE), -2600)), 1)

    def test_rescale(self):
        cues = rescale(parse_srt(SAMPLE), 2000, 6000)
        self.assertEqual(cues[0].start, 2000)
        self.assertEqual(cues[1].start, 6000)
        self.assertEqual(cues[0].end, 2000 + 3000)  # 1.5s * 2

    def test_merge_orders_and_renumbers(self):
        a = parse_srt(SAMPLE)
        b = parse_srt("1\n00:00:00,500 --> 00:00:00,900\nEarly\n")
        m = merge(a, b)
        self.assertEqual([c.text for c in m][0], "Early")
        self.assertEqual([c.index for c in m], [1, 2, 3])

    def test_split(self):
        before, after = split_at(parse_srt(SAMPLE), 3000)
        self.assertEqual(len(before), 1)
        self.assertEqual(after[0].start, 0)
        self.assertEqual(after[0].index, 1)

    def test_vtt(self):
        v = to_vtt(parse_srt(SAMPLE))
        self.assertTrue(v.startswith("WEBVTT"))
        self.assertIn("00:00:01.000 --> 00:00:02.500", v)

    def test_validate_and_fix_overlap(self):
        bad = parse_srt("1\n00:00:01,000 --> 00:00:04,000\nA\n\n2\n00:00:03,000 --> 00:00:05,000\nB\n")
        self.assertTrue(any("overlaps" in p for p in validate(bad)))
        self.assertEqual(validate(fix_overlaps(bad)), [])

    def test_validate_fast_and_empty(self):
        cues = parse_srt("1\n00:00:01,000 --> 00:00:01,500\n" + "x" * 40 + "\n")
        self.assertTrue(any("too fast" in p for p in validate(cues)))

    def test_missing_timing_line(self):
        with self.assertRaises(ValueError):
            parse_srt("1\njust text\n")

    def test_decode_cp1256_arabic(self):
        raw = "مرحبا".encode("cp1256")
        self.assertEqual(decode_bytes(raw), "مرحبا")
        self.assertEqual(decode_bytes("مرحبا".encode("utf-8")), "مرحبا")
        self.assertEqual(decode_bytes("مرحبا".encode("utf-16")), "مرحبا")

    def test_rtl_marks(self):
        out = format_srt(parse_srt(SAMPLE), rtl=True)
        self.assertIn("\u202b", out)
        self.assertNotIn("\u202bSecond", out)  # Latin lines are left alone


class Semantics(unittest.TestCase):
    """The behaviours people trip over. These tests document them on purpose."""

    def test_negative_shift_drops_and_clamps(self):
        cues = parse_srt("1\n00:00:01,000 --> 00:00:02,000\nA\n\n2\n00:00:03,000 --> 00:00:06,000\nB\n\n3\n00:00:08,000 --> 00:00:09,000\nC\n")
        out, dropped, clamped = shift_report(cues, -4000)
        self.assertEqual((dropped, clamped), (1 + 0, 1))      # A would end before 0; B starts before 0
        self.assertEqual([c.text for c in out], ["B", "C"])
        self.assertEqual((out[0].start, out[0].end), (0, 2000))  # keeps its end, so it gets shorter
        self.assertEqual(out[1].start, 4000)
        # a cue ending exactly at 0 is dropped too
        self.assertEqual(shift_report(cues[:1], -2000)[1], 1)

    def test_shift_then_back_is_not_lossless_when_clamped(self):
        cues = parse_srt("1\n00:00:01,000 --> 00:00:03,000\nA\n")
        back = shift(shift(cues, -2000), 2000)
        self.assertNotEqual(back[0].start, cues[0].start)  # clamping lost the original start

    def test_positive_shift_is_exact(self):
        cues = parse_srt(SAMPLE)
        self.assertEqual(shift(shift(cues, 12345), -12345), cues)

    def test_rescale_rejects_bad_anchors(self):
        cues = parse_srt(SAMPLE)
        for first, last in ((5000, 4000), (5000, 5000), (-1, 4000)):
            with self.assertRaises(ValueError):
                rescale(cues, first, last)
        # a stretch that would push the first cue below zero is refused, not silently clamped
        three = parse_srt("1\n00:00:10,000 --> 00:00:11,000\nA\n\n2\n00:00:20,000 --> 00:00:21,000\nB\n\n3\n00:00:30,000 --> 00:00:31,000\nC\n")
        self.assertEqual(rescale(three, 0, 40000)[-1].start, 40000)

    def test_fix_overlaps_squeezes_cues_with_same_start(self):
        cues = parse_srt("1\n00:00:01,000 --> 00:00:03,000\nA\n\n2\n00:00:01,000 --> 00:00:04,000\nB\n")
        out = fix_overlaps(cues)
        self.assertEqual(out[0].end - out[0].start, 1)  # known limit: identical starts leave a 1 ms cue
        self.assertTrue(any("too fast" in p for p in validate(out)))

    def test_split_cue_spanning_the_cut_stays_before(self):
        cues = parse_srt("1\n00:00:01,000 --> 00:00:05,000\nA\n\n2\n00:00:06,000 --> 00:00:07,000\nB\n")
        before, after = split_at(cues, 3000)
        self.assertEqual(before[0].end, 5000)  # not cut, so it runs past the split point
        self.assertEqual(after[0].start, 3000)

    def test_timestamp_forms(self):
        self.assertEqual(ts_to_ms("00:00:01,5"), 1500)
        self.assertEqual(ts_to_ms("100:00:00,000"), 360000000)
        for bad in ("00:00:01,5000", "00:61:00,000", "00:00:61,000", "00:00:01:000", "0:0:1,5", ""):
            with self.assertRaises(ValueError, msg=bad):
                ts_to_ms(bad)


class Cleanup(unittest.TestCase):
    def test_clean_tags_dupes_empty(self):
        cues = parse_srt("1\n00:00:01,000 --> 00:00:02,000\n<i>Hello</i>  {\\an8}there\n\n"
                         "2\n00:00:02,200 --> 00:00:03,000\nHello there\n\n"
                         "3\n00:00:04,000 --> 00:00:05,000\n<b></b>\n\n"
                         "4\n00:00:06,000 --> 00:00:07,000\nNext\n")
        out = clean(cues)
        self.assertEqual([c.text for c in out], ["Hello there", "Next"])
        self.assertEqual(out[0].end, 3000)  # repeat merged into one cue
        self.assertEqual([c.index for c in out], [1, 2])

    def test_clean_keeps_far_apart_repeats(self):
        cues = parse_srt("1\n00:00:01,000 --> 00:00:02,000\nYes\n\n2\n00:00:09,000 --> 00:00:10,000\nYes\n")
        self.assertEqual(len(clean(cues)), 2)

    def test_wrap(self):
        long = "هذا سطر عربي طويل جدا يحتاج إلى إعادة توزيع على سطرين لكي يقرأ بسهولة"
        cue = parse_srt(f"1\n00:00:01,000 --> 00:00:05,000\n{long}\n")
        out = wrap(cue, width=30)
        lines = out[0].text.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(len(x) <= 40 for x in lines))
        self.assertEqual(" ".join(lines), long)
        with self.assertRaises(ValueError):
            wrap(cue, width=3)

    def test_wrap_caps_at_two_lines_when_possible(self):
        text = " ".join(["word"] * 30)
        out = wrap(parse_srt(f"1\n00:00:01,000 --> 00:00:05,000\n{text}\n"), width=20)
        self.assertEqual(len(out[0].text.split("\n")), 2)

    def test_min_duration_does_not_hit_next_cue(self):
        cues = parse_srt("1\n00:00:01,000 --> 00:00:01,200\nA\n\n2\n00:00:01,600 --> 00:00:03,000\nB\n\n3\n00:00:05,000 --> 00:00:05,100\nC\n")
        out = ensure_min_duration(cues, 1000)
        self.assertEqual(out[0].end, 1599)
        self.assertEqual(out[2].end, 6000)
        self.assertEqual(validate(out, max_cps=100), [])


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "srt_toolkit", *args], capture_output=True, text=True,
                              cwd=os.path.dirname(os.path.dirname(__file__)))

    def test_shift_cli_with_cp1256_input(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "a.srt"), os.path.join(d, "b.srt")
            with open(src, "wb") as f:
                f.write(SAMPLE.encode("cp1256", errors="ignore"))
            r = self.run_cli("shift", src, "--by=+1.5s", "-o", dst)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(dst, encoding="utf-8") as f:
                self.assertIn("00:00:02,500", f.read())

    def test_batch_out_dir_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            files = []
            for n in ("a", "b"):
                f = os.path.join(d, n + ".srt")
                with open(f, "w", encoding="utf-8") as fh:
                    fh.write(SAMPLE)
                files.append(f)
            out = os.path.join(d, "out"); os.mkdir(out)
            r = self.run_cli("shift", *files, "--by=1s", "--out-dir", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(sorted(os.listdir(out)), ["a.srt", "b.srt"])
            r = self.run_cli("shift", *files, "--by=1s", "--out-dir", out)
            self.assertEqual(r.returncode, 2)
            self.assertIn("--force", r.stderr)
            r = self.run_cli("shift", *files, "--by=1s", "--out-dir", out, "--force")
            self.assertEqual(r.returncode, 0)
            r = self.run_cli("shift", *files, "--by=1s", "-o", os.path.join(d, "x.srt"))
            self.assertEqual(r.returncode, 2)

    def test_never_overwrites_input_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "a.srt")
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(SAMPLE)
            r = self.run_cli("fix", f, "-o", f)
            self.assertEqual(r.returncode, 2)
            with open(f, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), SAMPLE)

    def test_vtt_out_dir_extension(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "movie.srt")
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(SAMPLE)
            self.assertEqual(self.run_cli("vtt", f, "--out-dir", d).returncode, 0)
            self.assertTrue(os.path.exists(os.path.join(d, "movie.vtt")))

    def test_check_max_line_and_multi(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "a.srt")
            with open(f, "w", encoding="utf-8") as fh:
                fh.write("1\n00:00:01,000 --> 00:00:09,000\n" + "x" * 60 + "\n")
            r = self.run_cli("check", f, f, "--max-line", "42")
            self.assertEqual(r.returncode, 1)
            self.assertIn("longer than 42", r.stdout)
            self.assertIn(f + ": cue 1", r.stdout)

    def test_fix_min_duration_cli(self):
        with tempfile.TemporaryDirectory() as d:
            f, o = os.path.join(d, "a.srt"), os.path.join(d, "o.srt")
            with open(f, "w", encoding="utf-8") as fh:
                fh.write("1\n00:00:01,000 --> 00:00:01,200\nHi\n")
            self.assertEqual(self.run_cli("fix", f, "-o", o, "--min-duration", "1s").returncode, 0)
            with open(o, encoding="utf-8") as fh:
                self.assertIn("00:00:02,000", fh.read())

    def test_shift_cli_warns_about_dropped_cues(self):
        with tempfile.TemporaryDirectory() as d:
            f, o = os.path.join(d, "a.srt"), os.path.join(d, "o.srt")
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(SAMPLE)
            r = self.run_cli("shift", f, "--by=-2.6s", "-o", o)
            self.assertEqual(r.returncode, 0)
            self.assertIn("1 cue(s) dropped", r.stderr)

    def test_batch_writes_nothing_if_one_input_is_bad(self):
        with tempfile.TemporaryDirectory() as d:
            good, bad = os.path.join(d, "good.srt"), os.path.join(d, "bad.srt")
            with open(good, "w", encoding="utf-8") as fh:
                fh.write(SAMPLE)
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("1\nthis has no timing line\n")
            out = os.path.join(d, "out"); os.mkdir(out)
            r = self.run_cli("fix", good, bad, "--out-dir", out)
            self.assertEqual(r.returncode, 2)
            self.assertIn("bad.srt", r.stderr)
            self.assertEqual(os.listdir(out), [])

    def test_batch_detects_output_name_clash(self):
        with tempfile.TemporaryDirectory() as d:
            for sub in ("x", "y"):
                os.mkdir(os.path.join(d, sub))
                with open(os.path.join(d, sub, "ep.srt"), "w", encoding="utf-8") as fh:
                    fh.write(SAMPLE)
            out = os.path.join(d, "out"); os.mkdir(out)
            r = self.run_cli("fix", os.path.join(d, "x", "ep.srt"), os.path.join(d, "y", "ep.srt"), "--out-dir", out)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(os.listdir(out), [])

    def test_split_checks_both_outputs_first(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "a.srt")
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(SAMPLE)
            existing = os.path.join(d, "after.srt")
            with open(existing, "w") as fh:
                fh.write("keep me")
            r = self.run_cli("split", f, "2s", "--out-before", os.path.join(d, "before.srt"), "--out-after", existing)
            self.assertEqual(r.returncode, 2)
            self.assertFalse(os.path.exists(os.path.join(d, "before.srt")))  # no half-finished split
            with open(existing) as fh:
                self.assertEqual(fh.read(), "keep me")

    def test_no_partial_files_left_behind(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "a.srt")
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(SAMPLE)
            self.assertEqual(self.run_cli("vtt", f, "-o", os.path.join(d, "a.vtt")).returncode, 0)
            self.assertEqual(sorted(os.listdir(d)), ["a.srt", "a.vtt"])

    def test_bad_offset(self):
        r = self.run_cli("shift", "x.srt", "--by=abc", "-o", "/tmp/y.srt")
        self.assertEqual(r.returncode, 2)

    def test_check_exit_code(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "a.srt")
            with open(src, "w") as f:
                f.write("1\n00:00:02,000 --> 00:00:01,000\nbad\n")
            r = self.run_cli("check", src)
            self.assertEqual(r.returncode, 1)
            self.assertIn("end is not after start", r.stdout)

    def test_bad_file_exit_code(self):
        r = self.run_cli("vtt", "/nonexistent.srt", "-o", "/tmp/x.vtt")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
