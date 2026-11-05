import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""files.py — safe file operations for user-provided paths.

Centralizes path validation so every downloader doesn't reimplement it.
"""

ROOT = bootstrap.ROOT


def safe_read_ticker_file(path_str, restrict_to_project=False):
    """Read a ticker file with validation.

    Args:
        path_str: Path to the ticker file (can be relative or absolute)
        restrict_to_project: If True, reject paths outside the project directory

    Returns:
        List of ticker symbols (one per line, comments and blank lines stripped)

    Raises:
        FileNotFoundError: If the file doesn't exist
        ValueError: If path validation fails
        OSError: If the file can't be read

    Example:
        tickers = safe_read_ticker_file("plans/screener_universe.txt")
    """
    path = pathlib.Path(path_str).resolve()

    # Validation checks
    if not path.exists():
        raise FileNotFoundError(f"Ticker file not found: {path_str}")

    if not path.is_file():
        raise ValueError(f"Not a file: {path_str}")

    # Optional: restrict to project directory (defense in depth)
    if restrict_to_project and not path.is_relative_to(ROOT):
        raise ValueError(f"Path outside project directory: {path_str}")

    # Read and parse.
    #
    # utf-8-sig, not utf-8: `str.strip()` does NOT remove a UTF-8 BOM, so a ticker file
    # saved from Excel/Notepad on Windows yielded '﻿AAPL' for the first line. That
    # then fails symbol lookup and looks exactly like an absence of data -- the failure
    # this repo's conventions say must never be mistaken for a real one.
    #
    # Inline comments are stripped too: the line filter below only dropped lines
    # STARTING with '#', so "AAPL   # core" was returned whole as a ticker.
    try:
        out = []
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            body = line.split("#", 1)[0].strip()
            if body:
                out.append(body)
        return out
    except OSError as e:
        raise OSError(f"Error reading {path_str}: {e}") from e


def validate_output_path(path_str, must_exist=False, create_parents=False):
    """Validate an output file path.

    Args:
        path_str: Path to validate
        must_exist: If True, the file must already exist
        create_parents: If True, create parent directories if they don't exist

    Returns:
        Resolved pathlib.Path object

    Raises:
        ValueError: If validation fails
    """
    path = pathlib.Path(path_str).resolve()

    if must_exist and not path.exists():
        raise ValueError(f"Output file does not exist: {path_str}")

    if create_parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.parent.exists():
        raise ValueError(f"Parent directory does not exist: {path.parent}")

    return path
