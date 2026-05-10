import os

workers = 1  # Reduce to 1 worker to stay within connection limits
worker_class = 'sync'
timeout = 120

# Default access log format minus any header that could carry a Grok API key
# or Authorization Bearer token. Gunicorn does not log request bodies, so
# excluding those headers keeps the access log free of credentials. Redaction
# of any secret that still leaks into a log record (via app code, werkzeug,
# or a third-party traceback) is handled by SecretScrubFilter, which is
# attached to the root + WSGI loggers in app/__init__.py at startup.
accesslog = '-'
errorlog = '-'
access_log_format = (
    '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'
)


def post_fork(server, worker):
    # Each gunicorn worker initialises Python logging independently, so
    # importing the app here (which calls _install_log_scrubbing) guarantees
    # the redaction filter is attached to gunicorn's own loggers before the
    # worker starts handling requests.
    try:
        from app import _install_log_scrubbing
        _install_log_scrubbing()
    except Exception:
        # Worker startup must not fail because logging hardening tripped.
        pass
