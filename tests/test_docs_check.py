"""Regressions for offline documentation validation."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('provider_docs_check', Path(__file__).parents[1] / 'scripts/check_docs.py')
docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docs)


def test_links_validate_targets_and_duplicate_heading_anchors(tmp_path):
    page = tmp_path / 'README.md'
    target = tmp_path / 'guide.md'
    target.write_text('# Setup\n\n## Setup\n\n## API / CLI\n')
    page.write_text('[second](guide.md#setup-1) [api](guide.md#api--cli)')
    assert docs.check_links(tmp_path, [page]) == []
    page.write_text('[bad](guide.md#missing) [absent](absent.md)')
    errors = docs.check_links(tmp_path, [page])
    assert len(errors) == 2
    assert 'missing anchor' in errors[0] and 'missing absent.md' in errors[1]


def test_fenced_examples_are_not_links_or_headings(tmp_path):
    page = tmp_path / 'README.md'
    page.write_text('# Real\n\n```markdown\n# Fake\n[x](absent.md)\n```\n\n[real](#real)\n')
    assert docs.anchors(page.read_text()) == {'real'}
    assert docs.check_links(tmp_path, [page]) == []


def test_all_current_catalog_contracts_and_versions_match_documentation():
    root = Path(__file__).resolve().parents[1]
    result = docs.check(root)
    assert result['errors'] == []
    assert result['catalog_rows'] > 0


def test_stale_catalog_rows_are_rejected_even_when_all_real_rows_exist():
    row = '| `us_daily` | `underlying_daily` | `day` | `symbol` | no |'
    ghost = '| `obsolete_dataset` | `old_table` | `day` | `symbol` | no |'
    reference = '## Catalog mapping\n' + row + '\n## Choosing a dataset\n'
    assert docs.catalog_errors(reference, [row]) == []
    errors = docs.catalog_errors(reference.replace(row, row + '\n' + ghost), [row])
    assert len(errors) == 1 and 'stale or mismatched' in errors[0]
    assert docs.catalog_errors(reference.replace(row, row + '\n' + row), [row])
