import requests
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class FeishuClient:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send_card(self, card: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send a card message to Feishu via Webhook.
        """
        if not self.webhook_url:
            logger.info("Feishu webhook URL not configured, skipping notification.")
            return {"status": "skipped", "message": "Webhook not configured"}

        headers = {"Content-Type": "application/json"}
        try:
            resp = requests.post(self.webhook_url, json=card, headers=headers, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to send Feishu card: {e}")
            raise
