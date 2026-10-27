"""Smoke test: the package installs and imports."""

import ads_preflight


def test_package_imports():
    """Import the package and read its version.

    What:     `import ads_preflight` works and the package exposes a version string.
    Why:      pytest exits with an error when it collects no tests, and a broken
              install shows up here first.
    Expected: ads_preflight.__version__ is a str.
    """
    assert isinstance(ads_preflight.__version__, str)
