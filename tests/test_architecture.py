import ast
from pathlib import Path


def test_shared_package_never_imports_a_consumer():
    root = Path(__file__).resolve().parents[1] / "data_providers"
    violations = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level:
                continue
            names = [node.module or ""] if isinstance(node, ast.ImportFrom) else (
                [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            for name in names:
                if name.split(".")[0] in {"apps", "data", "datasources", "shared", "compute", "bootstrap"}:
                    violations.append(f"{path}:{node.lineno}: {name}")
    assert not violations, "\n".join(violations)
