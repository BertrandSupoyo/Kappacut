import logging

logger = logging.getLogger(__name__)


def _safe_disk_usage() -> float:
    import shutil
    import tempfile

    try:
        du = shutil.disk_usage(tempfile.gettempdir())
        return round(du.free / du.total * 100, 1)
    except OSError as exc:
        logger.warning("disk usage check failed: %s", exc)
        return 100.0


try:
    __import__("redis").exceptions  # noqa: F401
except Exception:  # pragma: no cover
    pass
