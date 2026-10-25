import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / '.agents/skills/chow-comedy-director'
spec = importlib.util.spec_from_file_location('preprocess', SKILL / 'scripts/preprocess.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def ffmpeg(*args):
    p.run(['ffmpeg', '-nostdin', '-v', 'error', *args])


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / 'two colors with audio.mov'
        ffmpeg('-f', 'lavfi', '-i', 'color=red:s=960x720:r=25:d=1',
               '-f', 'lavfi', '-i', 'color=blue:s=960x720:r=25:d=1',
               '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
               '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]',
               '-map', '[v]', '-map', '2:a', '-c:v', 'libx264', '-threads', '2',
               '-c:a', 'aac', '-timecode', '01:00:00:00', '-output_ts_offset', '5',
               cls.source)
        cls.output = cls.root / 'review'
        cls.result = p.process(cls.source, cls.output)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_cut_and_keyframes(self):
        shots = self.result['shots']
        self.assertEqual(len(shots), 2)
        self.assertAlmostEqual(shots[1]['clip_start_seconds'], 1, places=2)
        self.assertEqual(shots[0]['source_end_frame_exclusive'], 25)
        self.assertEqual(shots[1]['source_start_frame'], 25)
        for shot, channel in zip(shots, [0, 2]):
            path = self.output / shot['keyframe']
            self.assertTrue(path.is_file())
            pixels = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path),
                                     '-vf', 'scale=1:1', '-pix_fmt', 'rgb24',
                                     '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
            self.assertGreater(pixels[channel], 150)
            self.assertEqual(max(pixels), pixels[channel])
            self.assertGreaterEqual(shot['keyframe_source_frame'], shot['source_start_frame'])
            self.assertLess(shot['keyframe_source_frame'], shot['source_end_frame_exclusive'])

    def test_480p_pts_timecode_and_audio(self):
        proxy = self.output / 'proxy.mkv'
        info = p.probe(proxy)
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        self.assertEqual((video['width'], video['height']), (640, 480))
        self.assertGreater(self.result['source_first_video_pts_seconds'], 4)
        self.assertEqual(len(p.frames(proxy)[0]), 50)
        self.assertLessEqual(self.result['max_timestamp_error_seconds'], .002)
        self.assertTrue(any('01:00:00:00' in str(t) for t in self.result['timecode_metadata']))
        def audio_hash(path):
            return p.run(['ffmpeg', '-v', 'error', '-i', path, '-map', '0:a:0',
                          '-c:a', 'copy', '-f', 'hash', '-'])[0]
        self.assertEqual(audio_hash(self.source), audio_hash(proxy))
        def audio_pts(path):
            data = json.loads(p.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                                     '-show_packets', '-show_entries', 'packet=pts_time',
                                     '-of', 'json', path])[0])
            return [float(x['pts_time']) for x in data['packets']]
        before, after = audio_pts(self.source), audio_pts(proxy)
        self.assertEqual(len(before), len(after))
        self.assertLess(max(abs(a-b) for a,b in zip(before,after)), .002)
        with (self.output / 'frame-map.csv').open() as f:
            self.assertEqual(len(list(csv.DictReader(f))), 50)

    def test_templates_and_no_overwrite(self):
        with (self.output / 'shots.csv').open(encoding='utf-8-sig') as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 2)
        self.assertIn('sound', rows[0])
        self.assertTrue((self.output / 'comedy-beats.csv').is_file())
        with self.assertRaises(FileExistsError):
            p.process(self.source, self.output)

    def test_no_audio_no_upscale_single_shot(self):
        source = self.root / 'silent.mkv'
        ffmpeg('-f', 'lavfi', '-i', 'color=green:s=320x240:r=25:d=0.4',
               '-c:v', 'libx264', '-threads', '2', source)
        result = p.process(source, self.root / 'silent-review')
        self.assertEqual(result['audio_stream_count'], 0)
        self.assertEqual(len(result['shots']), 1)
        video = p.probe(self.root / 'silent-review/proxy.mkv')['streams'][0]
        self.assertEqual((video['width'], video['height']), (320, 240))

    def test_vfr_mapping(self):
        source = self.root / 'vfr.mkv'
        ffmpeg('-f', 'lavfi', '-i', 'testsrc2=s=160x120:r=25:d=1',
               '-vf', "select='not(mod(n,3))+eq(n,1)'", '-fps_mode', 'vfr',
               '-c:v', 'libx264', '-threads', '2', source)
        result = p.process(source, self.root / 'vfr-review')
        original, _ = p.frames(source)
        proxy, _ = p.frames(self.root / 'vfr-review/proxy.mkv')
        self.assertGreater(len({round(b-a, 3) for a,b in zip(original,original[1:])}), 1)
        self.assertEqual(len(original), len(proxy))
        self.assertLessEqual(result['max_timestamp_error_seconds'], .002)

    def test_multiple_audio_tracks(self):
        source = self.root / 'multi.mkv'
        ffmpeg('-i', self.source, '-map', '0:v:0', '-map', '0:a:0',
               '-map', '0:a:0', '-c', 'copy', source)
        result = p.process(source, self.root / 'multi-review')
        self.assertEqual(result['audio_stream_count'], 2)
        self.assertEqual(len([s for s in p.probe(self.root / 'multi-review/proxy.mkv')['streams']
                              if s['codec_type'] == 'audio']), 2)

    def test_minimum_shot_filter(self):
        result = p.process(self.source, self.root / 'filtered-review', min_shot=1.1)
        self.assertEqual(len(result['shots']), 1)
        self.assertTrue(result['cut_candidates'])
        self.assertTrue(all(not c['accepted'] for c in result['cut_candidates']))

    def test_cli_success(self):
        output = self.root / 'cli-review'
        result = subprocess.run([sys.executable, str(SKILL / 'scripts/preprocess.py'),
                                 str(self.source), '--output', str(output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads((output / 'manifest.json').read_text())['shots']), 2)

    def test_invalid_inputs(self):
        for threshold in [-1, 0, 1, float('nan')]:
            with self.assertRaises(ValueError):
                p.process(self.source, self.root / 'invalid', threshold)
        with self.assertRaises(ValueError):
            p.process(self.root / 'missing.mp4', self.root / 'invalid')
        audio = self.root / 'audio.wav'
        ffmpeg('-f', 'lavfi', '-i', 'sine=duration=0.2', audio)
        with self.assertRaisesRegex(ValueError, 'no video'):
            p.process(audio, self.root / 'audio-review')
        broken = self.root / 'broken.mp4'
        broken.write_text('not video')
        result = subprocess.run([sys.executable, str(SKILL / 'scripts/preprocess.py'),
                                 str(broken), '--output', str(self.root / 'invalid')],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ERROR', result.stderr)


class MethodTest(unittest.TestCase):
    def test_schema_and_template(self):
        from jsonschema import Draft202012Validator
        schema = json.loads((SKILL / 'references/method-library.schema.json').read_text())
        Draft202012Validator.check_schema(schema)
        card = json.loads((SKILL / 'assets/method-card.json').read_text())
        validator = Draft202012Validator(schema)
        validator.validate(card)
        card['status'] = 'confirmed'
        self.assertFalse(validator.is_valid(card))
        card['evidence'][0]['review_status'] = 'reviewed'
        card['validation']['results'] = ['独立原创实验结果占位，仅用于结构测试']
        validator.validate(card)
        card['dimensions'] = ['unknown']
        self.assertFalse(validator.is_valid(card))

    def test_skill_metadata(self):
        import yaml
        content = (SKILL / 'SKILL.md').read_text()
        metadata = yaml.safe_load(content.split('---', 2)[1])
        self.assertEqual(set(metadata), {'name', 'description'})
        self.assertEqual(metadata['name'], SKILL.name)
        self.assertTrue(metadata['description'])
        interface = yaml.safe_load((SKILL / 'agents/openai.yaml').read_text())['interface']
        self.assertIn('$chow-comedy-director', interface['default_prompt'])
        self.assertTrue(25 <= len(interface['short_description']) <= 64)
        self.assertNotIn('TODO', content)

    def test_validator_cli(self):
        result = subprocess.run([sys.executable, str(SKILL / 'scripts/validate_method.py'),
                                 str(SKILL / 'assets/method-card.json')], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        card = json.loads((SKILL / 'assets/method-card.json').read_text())
        card['evidence'][0]['source_end_pts_seconds'] = -1
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'bad.json'
            path.write_text(json.dumps(card))
            result = subprocess.run([sys.executable, str(SKILL / 'scripts/validate_method.py'),
                                     str(path)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            path.write_text('[]')
            result = subprocess.run([sys.executable, str(SKILL / 'scripts/validate_method.py'),
                                     str(path)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(b'Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
