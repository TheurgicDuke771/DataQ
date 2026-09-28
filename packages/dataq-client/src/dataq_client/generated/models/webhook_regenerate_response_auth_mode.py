from enum import StrEnum


class WebhookRegenerateResponseAuthMode(StrEnum):
    HMAC = "hmac"
    URL_TOKEN = "url_token"

    def __str__(self) -> str:
        return str(self.value)
