"""Security tests — ensure no secrets are leaked in code."""

from __future__ import annotations

import ast
import pathlib


def test_no_hardcoded_secrets_in_lib() -> None:
    """Ensure no hardcoded secrets exist in library code."""
    lib_path = pathlib.Path("lib/tuya_cloudless")
    secret_patterns = ["password", "api_key", "secret", "token"]

    for py_file in lib_path.rglob("*.py"):
        source = py_file.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name_lower = target.id.lower()
                        for pattern in secret_patterns:
                            if (
                                pattern in name_lower
                                and isinstance(node.value, ast.Constant)
                                and isinstance(node.value.value, str)
                                and len(node.value.value) > 3
                            ):
                                msg = f"Possible hardcoded secret: {target.id} in {py_file}"
                                raise AssertionError(msg)
