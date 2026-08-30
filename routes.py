"""WSGI entrypoint: `gunicorn routes:app`.

Thin shim over webapp.create_app — the route logic lives in webapp.py, which
takes the radio as an injection and has no import-time side effects. Importing
this module IS the signal to boot the real radio (default yt_radio module +
background playlist work).
"""
from webapp import create_app

app = create_app()
