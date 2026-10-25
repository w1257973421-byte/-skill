#!/usr/bin/env python3
"""Validate a method card using the bundled JSON Schema (requires jsonschema)."""
import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('card', type=Path)
    args = parser.parse_args()
    try:
        from jsonschema import Draft202012Validator
        schema = json.loads((Path(__file__).resolve().parents[1] /
                             'references/method-library.schema.json').read_text())
        data = json.loads(args.card.read_text(encoding='utf-8'))
        errors = list(Draft202012Validator(schema).iter_errors(data))
        for error in errors:
            print(f'{list(error.path)}: {error.message}', file=sys.stderr)
        if errors:
            return 1
        for item in data.get('evidence', []):
            if item['source_end_pts_seconds'] <= item['source_start_pts_seconds']:
                errors.append('Evidence interval must have positive duration')
                print(errors[-1], file=sys.stderr)
        return 1 if errors else 0
    except (OSError, ValueError, ImportError, KeyError, TypeError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
