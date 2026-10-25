#!/usr/bin/env python3
"""Prepare a 30–90 second review with every frame near candidate cuts.

Adds files to a NEW output via the phase-one pipeline; never edits phase-one code.
No semantic film analysis is generated. Real-movie origin is a user assertion.
"""
import argparse
import csv
import json
import math
from pathlib import Path
import shutil
import sys

from preprocess import frames, process, run, write_json


ASSETS = Path(__file__).resolve().parents[1] / 'assets'


def write_table(path, template, rows):
    with template.open(encoding='utf-8') as handle:
        fields = next(csv.reader(handle))
    with path.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def dense_selection(pts, centers, window):
    """Use original PTS and retain every decoded frame in every closed window."""
    return [i for i, timestamp in enumerate(pts)
            if any(abs(timestamp - center) <= window + 1e-8 for center in centers)]


def frame_filter(indices):
    """Collapse runs to keep FFmpeg arguments small, even for many frames."""
    groups = []
    for index in indices:
        if groups and index == groups[-1][1] + 1:
            groups[-1][1] = index
        else:
            groups.append([index, index])
    return "select='" + '+'.join(f'between(n,{a},{b})' for a, b in groups) + "'"


def prepare(source, output, source_id, source_kind='unknown', window=0.5,
            threshold=0.3, min_shot=0.15, max_dense_frames=5000,
            allow_short_test=False):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(f'Output already exists: {output}')
    if not source_id.strip():
        raise ValueError('source-id must be nonempty')
    if source_kind not in {'unknown', 'real_movie', 'synthetic_test'}:
        raise ValueError('Unsupported source kind')
    if not math.isfinite(window) or window <= 0:
        raise ValueError('window must be finite and positive')
    if not isinstance(max_dense_frames, int) or max_dense_frames < 1:
        raise ValueError('max-dense-frames must be a positive integer')
    if allow_short_test and source_kind != 'synthetic_test':
        raise ValueError('allow-short-test is restricted to synthetic_test')
    original, end = frames(source)
    duration = end - original[0]
    if duration > 90.002 or (duration < 29.998 and not allow_short_test):
        raise ValueError(f'Expected a 30–90 second clip, got {duration:.3f}s; '
                         'provide a clip or identify an interval for cloud-side extraction')
    manifest = process(source, output, threshold, min_shot)
    centers = [c['source_pts_seconds'] for c in manifest['cut_candidates']]
    # Also inspect the first/last moments, including clips with no detected cuts.
    centers.extend([original[0], original[-1]])
    selected = dense_selection(original, centers, window)
    if len(selected) > max_dense_frames:
        raise ValueError(f'{len(selected)} dense frames exceed limit {max_dense_frames}; '
                         'partial phase-one outputs retained, no sampling silently dropped')
    dense_dir = output / 'boundary-frames'
    dense_dir.mkdir()
    run(['ffmpeg', '-nostdin', '-v', 'error', '-n', '-noautorotate',
         '-i', output / 'proxy.mkv', '-map', '0:v:0', '-an',
         '-vf', frame_filter(selected), '-fps_mode', 'vfr', '-q:v', '2',
         dense_dir / 'frame-%06d.jpg'])
    images = sorted(dense_dir.glob('frame-*.jpg'))
    if len(images) != len(selected):
        raise RuntimeError('Dense extraction count mismatch; review package incomplete')
    proxy_pts, _ = frames(output / 'proxy.mkv')
    with (output / 'boundary-frames.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.writer(handle)
        writer.writerow(['evidence_id', 'file', 'source_frame_index', 'source_pts_seconds',
                         'proxy_pts_seconds', 'clip_seconds', 'near_candidate_ids', 'review_status'])
        for number, (path, index) in enumerate(zip(images, selected), 1):
            candidates = [f'C{i:04d}' for i, c in enumerate(manifest['cut_candidates'], 1)
                          if abs(original[index] - c['source_pts_seconds']) <= window + 1e-8]
            writer.writerow([f'E{number:05d}', path.relative_to(output).as_posix(), index,
                             original[index], proxy_pts[index], original[index] - original[0],
                             ';'.join(candidates), 'unreviewed'])
    cut_rows = []
    for i, cut in enumerate(manifest['cut_candidates'], 1):
        cut_rows.append({'candidate_id': f'C{i:04d}', **cut,
                         'clip_seconds': cut['source_pts_seconds'] - original[0],
                         'algorithm': 'ffmpeg_select_scene_gt_threshold', 'threshold': threshold,
                         'accepted_by_spacing_filter': str(cut['accepted']).lower(),
                         'review_status': 'algorithm_only_unreviewed'})
    templates = ASSETS / 'first-review'
    write_table(output / 'cut-candidates.csv', templates / 'cut-candidates.csv', cut_rows)
    shot_rows = [{**s, 'algorithm_boundary_status': s['boundary_status'],
                  'boundary_revision': 0, 'continuous_video_reviewed': 'false',
                  'audio_reviewed': 'false', 'review_status': 'unreviewed'}
                 for s in manifest['shots']]
    write_table(output / 'director-analysis.csv', templates / 'director-analysis.csv', shot_rows)
    write_table(output / 'comedy-beats-review.csv', templates / 'comedy-beats.csv', [])
    # Templates are deliberately named templates; they are NOT completed method cards.
    (output / 'method-cards').mkdir()
    shutil.copyfile(ASSETS / 'method-card.json', output / 'method-cards/method-card.template.json')
    review = {'schema_version': '1.0', 'preparation_complete': True,
              'analysis_complete': False, 'source_id': source_id,
              'source_kind_assertion': source_kind, 'source_content_verified': False,
              'source_sha256': manifest['source_sha256'], 'duration_seconds': duration,
              'window_seconds_each_side': window, 'dense_frame_count': len(images),
              'dense_policy': 'every original frame near ALL candidates, including spacing-rejected cuts; plus clip edges',
              'boundary_revision': 0, 'boundary_review_status': 'unreviewed',
              'continuous_video_reviewed': False, 'audio_reviewed': False,
              'limitations': ['scene detection can miss cuts; no claim of exhaustive coverage',
                              'frames alone do not establish timing, acting, or sound conclusions',
                              'AI interpretation and unverified inference fields are intentionally empty',
                              'method card is a template, not film evidence']}
    write_json(output / 'review-preparation.json', review)
    return review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source-id', required=True)
    parser.add_argument('--source-kind', choices=['unknown', 'real_movie', 'synthetic_test'], default='unknown')
    parser.add_argument('--window', type=float, default=0.5, help='seconds each side; every frame, no stride')
    parser.add_argument('--threshold', type=float, default=0.3)
    parser.add_argument('--min-shot', type=float, default=0.15)
    parser.add_argument('--max-dense-frames', type=int, default=5000)
    parser.add_argument('--allow-short-test', action='store_true', help='synthetic_test ONLY')
    args = parser.parse_args()
    try:
        review = prepare(args.source, args.output, args.source_id, args.source_kind,
                         args.window, args.threshold, args.min_shot, args.max_dense_frames,
                         args.allow_short_test)
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    print(f"Prepared review, not analysis: {review['dense_frame_count']} dense frames; {args.output}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
