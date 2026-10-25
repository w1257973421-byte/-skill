#!/usr/bin/env python3
"""Create review proxies and evidence indexes; Python stdlib + FFmpeg only."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys


def run(args):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'{args[0]} failed ({result.returncode}):\n{result.stderr[-6000:]}')
    return result.stdout, result.stderr


def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                           '-of', 'json', path])[0])


def frames(path):
    data = json.loads(run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                           '-show_frames', '-show_entries',
                           'frame=best_effort_timestamp_time,duration_time,pkt_duration_time',
                           '-of', 'json', path])[0])['frames']
    if not data or any('best_effort_timestamp_time' not in f for f in data):
        raise ValueError('Video requires decoded frames with usable timestamps')
    pts = [float(f['best_effort_timestamp_time']) for f in data]
    if any(b <= a for a, b in zip(pts, pts[1:])):
        raise ValueError('Non-increasing video timestamps are unsupported')
    last_duration = float(data[-1].get('duration_time',
                          data[-1].get('pkt_duration_time', 0)))
    if last_duration <= 0:
        last_duration = pts[-1] - pts[-2] if len(pts) > 1 else 0.04
    return pts, pts[-1] + last_duration


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def process(source, output, threshold=0.3, min_shot=0.15):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_file():
        raise ValueError(f'Input is not a file: {source}')
    if not math.isfinite(threshold) or not 0 < threshold < 1:
        raise ValueError('threshold must be between 0 and 1 (exclusive)')
    if not math.isfinite(min_shot) or min_shot < 0:
        raise ValueError('min-shot must be finite and nonnegative')
    for tool in ('ffmpeg', 'ffprobe'):
        if not shutil.which(tool):
            raise ValueError(f'Missing dependency: {tool}')
    info = probe(source)
    videos = [s for s in info['streams'] if s['codec_type'] == 'video'
              and not s.get('disposition', {}).get('attached_pic')]
    if not videos:
        raise ValueError('Input has no video stream')
    if videos[0]['index'] != next(s['index'] for s in info['streams'] if s['codec_type'] == 'video'):
        raise ValueError('Attached cover art before video is unsupported; remux input first')
    original, original_end = frames(source)
    # Refuse reuse, even of an empty directory, so no user outputs can be overwritten.
    output.mkdir(parents=True, exist_ok=False)
    (output / 'keyframes').mkdir()
    write_json(output / 'source.ffprobe.json', info)
    proxy = output / 'proxy.mkv'
    run(['ffmpeg', '-nostdin', '-v', 'error', '-n', '-copyts', '-noautorotate',
         '-i', source, '-map', '0:v:0', '-map', '0:a?', '-map_metadata', '0',
         '-vf', "scale=w='trunc(iw*min(1,480/ih)/2)*2':h='trunc(ih*min(1,480/ih)/2)*2'",
         '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-threads', '2',
         '-enc_time_base:v', videos[0]['time_base'],
         '-fps_mode', 'passthrough', '-c:a', 'copy', '-avoid_negative_ts', 'disabled', proxy])
    proxy_info = probe(proxy)
    pts, proxy_end = frames(proxy)
    if len(pts) != len(original):
        raise RuntimeError('Proxy changed frame count; cannot establish exact frame mapping')
    max_error = max(abs(a - b) for a, b in zip(pts, original))
    if max_error > 0.002:
        raise RuntimeError(f'Proxy timestamps shifted by {max_error}s; mapping validation failed')
    audio_before = [s['codec_name'] for s in info['streams'] if s['codec_type'] == 'audio']
    audio_after = [s['codec_name'] for s in proxy_info['streams'] if s['codec_type'] == 'audio']
    if audio_before != audio_after:
        raise RuntimeError('Audio stream copy validation failed')
    _, log = run(['ffmpeg', '-nostdin', '-v', 'info', '-copyts', '-i', proxy,
                  '-map', '0:v:0', '-an', '-vf',
                  f"select='gt(scene,{threshold})',showinfo", '-fps_mode', 'passthrough',
                  '-f', 'null', '-'])
    candidates = [float(t) for t in re.findall(r'pts_time:([-+\d.eE]+)', log)]
    boundaries = [0]
    cuts = []
    for timestamp in candidates:
        index = min(range(len(pts)), key=lambda i: abs(pts[i] - timestamp))
        accepted = (index > boundaries[-1]
                    and pts[index] - pts[boundaries[-1]] >= min_shot
                    and proxy_end - pts[index] >= min_shot)
        cuts.append({'proxy_pts_seconds': pts[index], 'source_pts_seconds': original[index],
                     'source_frame_index': index, 'accepted': accepted})
        if accepted:
            boundaries.append(index)
    shots = []
    for number, (start, end) in enumerate(zip(boundaries, boundaries[1:] + [len(pts)]), 1):
        end_pts = original[end] if end < len(original) else original_end
        midpoint = (original[start] + end_pts) / 2
        key = min(range(start, end), key=lambda i: abs(original[i] - midpoint))
        filename = f'keyframes/shot-{number:04d}.jpg'
        run(['ffmpeg', '-nostdin', '-v', 'error', '-n', '-noautorotate', '-i', proxy,
             '-map', '0:v:0', '-vf', f"select='eq(n,{key})'", '-frames:v', '1',
             '-fps_mode', 'vfr', output / filename])
        shots.append({'shot_id': f'S{number:04d}', 'boundary_status': 'candidate_unreviewed',
                      'source_start_pts_seconds': original[start],
                      'source_end_pts_seconds_exclusive': end_pts,
                      'clip_start_seconds': original[start] - original[0],
                      'clip_end_seconds_exclusive': end_pts - original[0],
                      'source_start_frame': start, 'source_end_frame_exclusive': end,
                      'keyframe': filename, 'keyframe_source_frame': key,
                      'keyframe_source_pts_seconds': original[key]})
    write_json(output / 'proxy.ffprobe.json', proxy_info)
    with (output / 'frame-map.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['source_frame_index', 'source_pts_seconds', 'proxy_pts_seconds'])
        writer.writerows((i, a, b) for i, (a, b) in enumerate(zip(original, pts)))
    manifest = {'schema_version': '1.0', 'source_name': source.name,
                'source_sha256': sha256(source), 'proxy': 'proxy.mkv',
                'source_first_video_pts_seconds': original[0],
                'timestamp_policy': 'copyts; VFR passthrough; Matroska millisecond precision; frame-map.csv is authoritative',
                'max_timestamp_error_seconds': max_error,
                'timecode_metadata': [{'stream_index': s.get('index'), 'tags': s.get('tags', {})}
                                      for s in [info['format'], *info['streams']]
                                     if any(k.lower() == 'timecode' for k in s.get('tags', {}))],
                'audio_policy': 'all audio streams copied without re-encoding',
                'audio_stream_count': len(audio_after), 'threshold': threshold,
                'min_shot_seconds': min_shot, 'cut_candidates': cuts, 'shots': shots,
                'tools': {t: run([t, '-version'])[0].splitlines()[0]
                          for t in ('ffmpeg', 'ffprobe')}}
    write_json(output / 'manifest.json', manifest)
    fields = ['shot_id', 'source_start_pts_seconds', 'source_end_pts_seconds_exclusive',
              'keyframe', 'boundary_status', 'framing_camera', 'performance', 'blocking',
              'gag_mechanism', 'sound', 'editing_rhythm', 'observation', 'hypothesis',
              'confidence', 'counterexample', 'method_ids']
    with (output / 'shots.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(shots)
    templates = Path(__file__).resolve().parents[1] / 'assets'
    shutil.copyfile(templates / 'comedy-beats.csv', output / 'comedy-beats.csv')
    shutil.copyfile(templates / 'analysis-report.md', output / 'analysis-report.md')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', required=True, type=Path, help='new output directory')
    parser.add_argument('--threshold', type=float, default=0.3)
    parser.add_argument('--min-shot', type=float, default=0.15)
    args = parser.parse_args()
    try:
        result = process(args.source, args.output, args.threshold, args.min_shot)
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    print(f"Prepared {len(result['shots'])} candidate shots in {args.output}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
