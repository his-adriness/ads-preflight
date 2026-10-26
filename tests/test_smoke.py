"""Smoke test: the package installs and imports."""

import mcpdoctor


def test_package_imports():
    """Import the package and read its version.

    What:     `import mcpdoctor` works and the package exposes a version string.
    Why:      pytest exits with an error when it collects no tests, and a broken
              install shows up here first.
    Expected: mcpdoctor.__version__ is a str.
    """
    assert isinstance(mcpdoctor.__version__, str)
