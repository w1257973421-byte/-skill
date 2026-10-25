"""Synthetic fixtures validate tooling only; no film analysis assertions."""
import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / '.agents/skills/chow-comedy-director/scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('prepare_review', SCRIPTS / 'prepare_review.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class ReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / 'synthetic-30-seconds.mov'
        r.run(['ffmpeg', '-nostdin', '-v', 'error',
               '-f', 'lavfi', '-i', 'color=red:s=320x240:r=10:d=14.9',
               '-f', 'lavfi', '-i', 'color=lime:s=320x240:r=10:d=0.1',
               '-f', 'lavfi', '-i', 'color=blue:s=320x240:r=10:d=15',
               '-f', 'lavfi', '-i', 'sine=frequency=440:duration=30',
               '-filter_complex', '[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]',
               '-map', '[v]', '-map', '3:a', '-c:v', 'libx264', '-threads', '2',
               '-c:a', 'aac', '-timecode', '01:00:00:00', '-output_ts_offset', '5',
               cls.source])
        cls.output = cls.root / 'review'
        cls.review = r.prepare(cls.source, cls.output, 'SYNTHETIC-TEST-ONLY', 'synthetic_test')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def table(self, filename):
        with (self.output / filename).open(encoding='utf-8-sig', newline='') as handle:
            return list(csv.DictReader(handle))

    def test_duration_provenance_and_unverified_status(self):
        self.assertAlmostEqual(self.review['duration_seconds'], 30, places=2)
        self.assertEqual(self.review['source_kind_assertion'], 'synthetic_test')
        self.assertFalse(self.review['source_content_verified'])
        self.assertFalse(self.review['analysis_complete'])
        self.assertTrue(self.review['preparation_complete'])
        self.assertFalse(self.review['audio_reviewed'])
        self.assertFalse(self.review['continuous_video_reviewed'])

    def test_all_frames_near_including_rejected_cut(self):
        manifest = json.loads((self.output / 'manifest.json').read_text())
        cuts = manifest['cut_candidates']
        self.assertEqual(len(cuts), 2)
        self.assertTrue(any(not c['accepted'] for c in cuts))
        pts, _ = r.frames(self.source)
        selected = self.table('boundary-frames.csv')
        indices = {int(row['source_frame_index']) for row in selected}
        for cut in cuts:
            expected = {i for i,t in enumerate(pts) if abs(t-cut['source_pts_seconds']) <= .50000001}
            self.assertTrue(expected.issubset(indices))
        self.assertIn(149, indices)  # One-frame green flash must survive dense extraction.
        green = next(row for row in selected if int(row['source_frame_index']) == 149)
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(self.output / green['file']),
                              '-vf', 'scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
                             check=True, capture_output=True).stdout
        self.assertGreater(raw[1], raw[0])
        self.assertGreater(raw[1], raw[2])
        self.assertEqual(len(selected), len(list((self.output / 'boundary-frames').glob('*.jpg'))))
        self.assertEqual(len(indices), len(selected))
        self.assertTrue(all(row['review_status'] == 'unreviewed' for row in selected))
        self.assertTrue(all(float(row['source_pts_seconds']) >= 5 for row in selected))

    def test_empty_interpretations_and_template_only(self):
        cuts = self.table('cut-candidates.csv')
        shots = self.table('director-analysis.csv')
        self.assertTrue(cuts)
        self.assertTrue(shots)
        for row in cuts + shots:
            for field in ('human_observation', 'ai_interpretation', 'unverified_inference'):
                self.assertEqual(row[field], '')
        self.assertTrue(all(s['audio_reviewed'] == 'false' for s in shots))
        self.assertEqual(self.table('comedy-beats-review.csv'), [])
        method = json.loads((self.output / 'method-cards/method-card.template.json').read_text())
        self.assertEqual(method['evidence'][0]['source_id'], 'TEMPLATE-NOT-REAL-EVIDENCE')
        self.assertEqual(list((self.output / 'method-cards').glob('M*.json')), [])

    def test_reject_existing_output(self):
        with self.assertRaises(FileExistsError):
            r.prepare(self.source, self.output, 'test')

    def test_bad_parameters_before_output(self):
        for params in ({'window': 0}, {'window': float('nan')}, {'max_dense_frames': 0},
                       {'source_kind': 'made_up'}, {'allow_short_test': True}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                r.prepare(self.source, self.root / 'invalid', 'test', **params)
        with self.assertRaises(ValueError):
            r.prepare(self.source, self.root / 'invalid', ' ')
        self.assertFalse((self.root / 'invalid').exists())

    def test_duration_gate_and_synthetic_cli(self):
        short = self.root / 'short.mkv'
        r.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
               'color=blue:s=160x120:r=10:d=1', '-c:v', 'libx264', '-threads', '2', short])
        with self.assertRaisesRegex(ValueError, '30–90'):
            r.prepare(short, self.root / 'short-rejected', 'test')
        self.assertFalse((self.root / 'short-rejected').exists())
        result = subprocess.run([sys.executable, str(SCRIPTS / 'prepare_review.py'), str(short),
                                 '--output', str(self.root / 'short-test-review'), '--source-id', 'test',
                                 '--source-kind', 'synthetic_test', '--allow-short-test'],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        prepared = json.loads((self.root / 'short-test-review/review-preparation.json').read_text())
        self.assertFalse(prepared['analysis_complete'])
        with self.assertRaisesRegex(ValueError, 'exceed limit'):
            r.prepare(short, self.root / 'limited', 'test', 'synthetic_test',
                      max_dense_frames=1, allow_short_test=True)
        self.assertFalse((self.root / 'limited/review-preparation.json').exists())

    def test_long_duration_rejected(self):
        long_clip = self.root / 'long.mkv'
        r.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
               'color=blue:s=32x32:r=1:d=91', '-c:v', 'libx264', '-threads', '2', long_clip])
        with self.assertRaisesRegex(ValueError, '30–90'):
            r.prepare(long_clip, self.root / 'long-rejected', 'test')
        self.assertFalse((self.root / 'long-rejected').exists())

    def test_frame_filter_ranges_and_vfr_selection(self):
        self.assertEqual(r.frame_filter([0,1,2,4]), "select='between(n,0,2)+between(n,4,4)'")
        self.assertEqual(r.dense_selection([5,5.03,5.2,5.6], [5.2], .2), [0,1,2])


if __name__ == '__main__':
    unittest.main()
