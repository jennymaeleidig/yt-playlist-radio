"""Root test configuration.

IMPORTANT: this runs before any test module is imported. The environment
variables set here are read into module-level constants when yt_radio is
first imported (load_dotenv does not override already-set variables), so
they pin the app's config deterministically for every test run:

- PLAYLIST_URL points at the local sample fixture, so the app's bootstrap
  playlist load never does a network playlist listing. Metadata and media
  fetches (integration only) still hit real YouTube.
- CACHE_FILE is redirected out of the repo so tests never touch cache.json.

Unit tests never import routes.py, so no background work (threads, network)
is started in the default run.
"""
import os
import tempfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent

os.environ["PLAYLIST_URL"] = str(TESTS_DIR / "fixtures" / "sample.radio")
os.environ["CACHE_FILE"] = os.path.join(tempfile.gettempdir(), "yt-radio-test-cache.json")
