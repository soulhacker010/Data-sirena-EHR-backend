"""
Production health-check command.

Verifies the moving parts that aren't visible from the request log:

    - Redis broker connectivity      (CELERY_BROKER_URL ping)
    - Redis cache connectivity       (Django cache.set/get round trip)
    - Celery worker liveness         (sends a debug task, waits for ack)
    - Database connectivity          (SELECT 1)

Run from a Render shell or anywhere with the production env vars set:

    python manage.py healthcheck

Each step prints "OK" or a short failure reason. Exit code is 0 only when
every check passes; non-zero on any failure so this can be wired into a
monitoring probe later if desired.
"""
from __future__ import annotations

import sys
import time
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Verify Redis, Celery, and database connectivity.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--celery-timeout', type=int, default=10,
            help='Seconds to wait for the Celery debug task. Default 10.',
        )

    def handle(self, *args, celery_timeout: int = 10, **kwargs):
        results = []

        results.append(self._check_database())
        results.append(self._check_redis_broker())
        results.append(self._check_django_cache())
        results.append(self._check_celery_worker(timeout=celery_timeout))

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('--- Summary ---'))
        for name, ok, detail in results:
            status = self.style.SUCCESS('OK') if ok else self.style.ERROR('FAIL')
            self.stdout.write(f'  {status:<25}  {name:<20}  {detail}')
        self.stdout.write('')

        if not all(ok for _, ok, _ in results):
            sys.exit(1)

    # ─── individual checks ──────────────────────────────────────────────────

    def _check_database(self):
        name = 'Database'
        try:
            from django.db import connection
            with connection.cursor() as cur:
                cur.execute('SELECT 1')
                row = cur.fetchone()
            ok = row == (1,)
            return name, ok, 'SELECT 1 -> 1' if ok else f'unexpected: {row}'
        except Exception as e:
            return name, False, f'{type(e).__name__}: {e}'

    def _check_redis_broker(self):
        name = 'Redis (broker)'
        url = getattr(settings, 'CELERY_BROKER_URL', '')
        if not url:
            return name, False, 'CELERY_BROKER_URL not set'
        try:
            import redis
            client = redis.from_url(url, socket_connect_timeout=5)
            ok = bool(client.ping())
            return name, ok, f'PING -> PONG ({_redact_url(url)})'
        except Exception as e:
            return name, False, f'{type(e).__name__}: {e}'

    def _check_django_cache(self):
        name = 'Django cache'
        try:
            from django.core.cache import cache
            key = f'healthcheck:{uuid.uuid4().hex}'
            value = 'ok'
            cache.set(key, value, timeout=30)
            got = cache.get(key)
            cache.delete(key)
            ok = got == value
            backend = settings.CACHES.get('default', {}).get('BACKEND', '<unset>') \
                if hasattr(settings, 'CACHES') else '<unset>'
            return name, ok, (
                f'set/get round-trip via {backend.rsplit(".", 1)[-1]}'
                if ok else f'value mismatch: wrote {value!r}, read {got!r}'
            )
        except Exception as e:
            return name, False, f'{type(e).__name__}: {e}'

    def _check_celery_worker(self, timeout: int):
        name = 'Celery worker'
        try:
            from config.celery import app as celery_app
            t0 = time.monotonic()
            result = celery_app.send_task('config.celery.debug_task')
            try:
                result.get(timeout=timeout, propagate=True)
            except Exception as e:
                return name, False, (
                    f'task queued but no worker responded in {timeout}s '
                    f'({type(e).__name__}). Worker may be down.'
                )
            elapsed = time.monotonic() - t0
            return name, True, f'debug_task ack in {elapsed:.2f}s'
        except Exception as e:
            return name, False, f'{type(e).__name__}: {e}'


def _redact_url(url: str) -> str:
    """Strip password from a redis:// URL for safe logging."""
    if '@' not in url:
        return url
    scheme_creds, _, host = url.partition('@')
    scheme, _, _ = scheme_creds.rpartition(':')
    return f'{scheme}:***@{host}'
