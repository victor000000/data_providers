"""Offline canonical documentation guard; no stores, providers or browser access."""
import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit


def prose(text):
    return re.sub(r'^\s*(`{3,}|~{3,}).*?^\s*\1\s*$', '', text, flags=re.MULTILINE | re.DOTALL)


def anchors(text):
    counts = Counter()
    result = set()
    for heading in re.findall(r'^#{1,6}\s+(.+?)\s*#*\s*$', prose(text), re.MULTILINE):
        heading = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', heading)
        slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
        suffix = f'-{counts[slug]}' if counts[slug] else ''
        result.add(slug + suffix)
        counts[slug] += 1
    return result


def canonical(root):
    paths = [root / 'README.md', *sorted(p for p in (root / 'docs').glob('*.md')
                                        if p.name not in {'DOC_REVIEW_20261021.md', 'DOC_REVIEW_20261031.md'})]
    private_index = root / 'docs/requirements/README.md'
    if private_index.exists():
        paths.append(private_index)
    return paths


def check_links(root, paths):
    errors = []
    for path in paths:
        for target in re.findall(r'(?<!!)\[[^\]]+\]\(([^\s)]+)\)', prose(path.read_text())):
            link = urlsplit(target)
            if link.scheme or target.startswith('/'):
                continue
            destination = (path.parent / unquote(link.path)).resolve() if link.path else path.resolve()
            if not destination.exists():
                errors.append(f'{path.relative_to(root)}: missing {target}')
            elif (link.fragment and destination.suffix == '.md'
                  and unquote(link.fragment) not in anchors(destination.read_text())):
                errors.append(f'{path.relative_to(root)}: missing anchor {target}')
    return errors


def catalog_rows(root):
    tree = ast.parse((root / 'data_providers/catalog.py').read_text())
    expression = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'CATALOG' for t in n.targets))
    rows = []
    for key, call in zip(expression.keys, expression.values):
        name = ast.literal_eval(key)
        args = [ast.literal_eval(value) for value in call.args]
        _, table, time, symbol = args[:4]
        private = args[4] if len(args) > 4 else False
        columns = [name, table, time, symbol]
        rows.append('| ' + ' | '.join(f'`{value}`' if value else '—' for value in columns)
                    + f' | {"yes" if private else "no"} |')
    return rows


def check(root, cli=False):
    paths = canonical(root)
    errors = check_links(root, paths)
    datasets = (root / 'docs/DATASETS.md').read_text()
    rows = catalog_rows(root)
    for row in rows:
        if datasets.count(row) != 1:
            errors.append(f'DATASETS.md requires exactly one catalog row: {row}')
    project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
    init = ast.parse((root / 'data_providers/__init__.py').read_text())
    version = next(ast.literal_eval(n.value) for n in init.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == '__version__' for t in n.targets))
    lock = tomllib.loads((root / 'uv.lock').read_text())
    locked = next(p['version'] for p in lock['package'] if p['name'] == project['name'])
    if len({project['version'], version, locked}) != 1:
        errors.append('Software versions disagree between package, metadata and lockfile')
    if cli:
        with tempfile.TemporaryDirectory(prefix='provider-doc-check-') as temporary:
            home = Path(temporary) / 'absent-runtime'
            env = dict(os.environ, DATA_PROVIDERS_HOME=str(home),
                       DATA_PROVIDERS_CONFIG=str(Path(temporary) / 'absent.toml'))
            for command in ['', 'init', 'datasets', 'schema', 'status', 'readiness', 'read',
                            'export', 'maintain', 'lake', 'serve']:
                args = [sys.executable, '-m', 'data_providers.cli']
                result = subprocess.run(args + ([command] if command else []) + ['--help'],
                                        cwd=root, env=env, capture_output=True, text=True, timeout=30, check=False)
                if result.returncode:
                    errors.append(f'CLI help failed: {command or "global"}')
            if home.exists():
                errors.append('CLI help created a runtime')
    return {'canonical_guides': len(paths), 'catalog_rows': len(rows),
            'cli_help_checked': cli, 'errors': errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', action='store_true', help='Also parse all CLI help in an absent temp runtime')
    args = parser.parse_args()
    result = check(Path(__file__).resolve().parents[1], cli=args.cli)
    print(json.dumps(result, indent=2))
    return bool(result['errors'])


if __name__ == '__main__':
    raise SystemExit(main())
