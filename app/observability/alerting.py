import logging

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


def send_alert(settings: Settings, *, kind: str, status: str, error: str | None) -> None:
    if not settings.n8n_alert_webhook_url:
        return
    try:
        httpx.post(
            settings.n8n_alert_webhook_url,
            json={"kind": kind, "status": status, "error": error},
            timeout=5.0,
        )
    except Exception:
        logger.warning("send_alert: failed to reach n8n webhook", exc_info=True)
