"""Invenio Testrig Dashboard."""

from importlib.metadata import PackageNotFoundError, version

try:
    # The one source of the version is pyproject.toml; the image tag and the chart follow it.
    __version__ = version("invenio-testrig-dashboard")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"
