"""Web-service adapters for the pinned musicdl 2.14.1 runtime."""
from __future__ import annotations

import importlib
import io
import os
import sys
from functools import wraps

from rich.console import Console
from rich.progress import Progress


class WebProgress(Progress):
    """Keep upstream task bookkeeping without touching the shared terminal console."""

    def __init__(self, *args, **kwargs):
        kwargs.update(disable=True, auto_refresh=False,
                      console=Console(file=io.StringIO(), color_system=None))
        super().__init__(*args, **kwargs)


def configure_stdio():
    # Spawned platform processes must inherit the same encoding on Windows pipes.
    os.environ['PYTHONIOENCODING'] = 'utf-8:backslashreplace'
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')


def _qq_parser(parser, client):
    @wraps(parser)
    def compatible(search_result, request_overrides=None):
        overrides = dict(request_overrides or {})
        # These endpoints already supply timeout=10/20 before **request_overrides.
        # mikus uses AudioLinkTester instead, so retain its caller-supplied timeout.
        if parser.__name__ != '_parsewithmikusapi':
            overrides.pop('timeout', None)
        try:
            return parser(search_result=search_result, request_overrides=overrides)
        except Exception as exc:
            # The upstream dispatcher suppresses exceptions. Preserve diagnostics
            # without logging request URLs, credentials or response contents.
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            client.logger_handle.warning(
                f'{client.source}.{parser.__name__}: {type(exc).__name__}, HTTP status={status}',
                disable_print=True)
            raise
    return compatible


def adapt_client(client):
    from musicdl.modules.sources import base

    # Upstream ignores disable_print for Progress. Replace only its module bindings;
    # neither vendor files nor Rich's global console are modified.
    base.Progress = WebProgress
    module = importlib.import_module(type(client).__module__)
    if hasattr(module, 'Progress'):
        module.Progress = WebProgress
    if client.source == 'QQMusicClient' and not getattr(client, '_web_adapted', False):
        for name in dir(client):
            if name.startswith('_parsewith') and name.endswith('api'):
                setattr(client, name, _qq_parser(getattr(client, name), client))
        client._web_adapted = True
    return client
