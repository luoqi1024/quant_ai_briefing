"""WeCom Markdown notification client."""

from __future__ import annotations

import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import Settings, load_settings


logger = logging.getLogger(__name__)

TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
SEND_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
MARKDOWN_CONTENT_LIMIT_BYTES = 1800


class WeComNotifier:
    """Send Markdown messages through a WeCom self-built app."""

    def __init__(
        self,
        settings: Settings | None = None,
        timeout: float = 10.0,
        retries: int = 2,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.timeout = timeout
        self.session = session or _build_retry_session(retries)

    def send_markdown(self, content: str) -> bool:
        """Send Markdown content. Returns False on config or API failure."""

        if not self._configured():
            logger.warning("WeCom settings are incomplete; message was not sent")
            return False

        token = self._get_access_token()
        if token is None:
            return False

        chunks = _split_markdown_by_bytes(content, MARKDOWN_CONTENT_LIMIT_BYTES)
        sent_all = True
        for index, chunk in enumerate(chunks, start=1):
            message = _format_chunk(chunk, index=index, total=len(chunks))
            payload = {
                "touser": "@all",
                "msgtype": "markdown",
                "agentid": int(self.settings.wecom_agentid),
                "markdown": {"content": message},
                "safe": 0,
            }
            if not self._post_message(token, payload):
                sent_all = False
        return sent_all

    def _post_message(self, token: str, payload: dict) -> bool:
        try:
            response = self.session.post(
                SEND_URL,
                params={"access_token": token},
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            ok = data.get("errcode") == 0
            if not ok:
                logger.warning("WeCom send failed: %s", data)
            return ok
        except Exception as exc:  # noqa: BLE001 - remote APIs can fail many ways.
            logger.warning("WeCom send request failed: %s", exc)
            return False

    def _get_access_token(self) -> str | None:
        try:
            response = self.session.get(
                TOKEN_URL,
                params={
                    "corpid": self.settings.wecom_corpid,
                    "corpsecret": self.settings.wecom_secret,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            token = data.get("access_token")
            if not token:
                logger.warning("WeCom token response missing access_token: %s", data)
                return None
            return str(token)
        except Exception as exc:  # noqa: BLE001 - remote APIs can fail many ways.
            logger.warning("WeCom token request failed: %s", exc)
            return None

    def _configured(self) -> bool:
        return all(
            [
                self.settings.wecom_corpid,
                self.settings.wecom_agentid,
                self.settings.wecom_secret,
            ]
        )


def send_markdown(content: str) -> bool:
    """Module-level convenience wrapper for the default notifier."""

    return WeComNotifier().send_markdown(content)


def _format_chunk(chunk: str, *, index: int, total: int) -> str:
    if total <= 1:
        return chunk
    return f"### 投资日报（第 {index}/{total} 部分）\n\n{chunk}"


def _split_markdown_by_bytes(
    content: str,
    max_bytes: int = MARKDOWN_CONTENT_LIMIT_BYTES,
) -> list[str]:
    """Split Markdown into UTF-8 safe chunks below the byte limit."""

    if len(content.encode("utf-8")) <= max_bytes:
        return [content]

    chunks: list[str] = []
    current = ""
    blocks = content.splitlines(keepends=True)
    for block in blocks:
        if len(block.encode("utf-8")) > max_bytes:
            if current:
                chunks.append(current.rstrip())
                current = ""
            chunks.extend(_split_long_text_by_bytes(block, max_bytes))
            continue

        candidate = current + block
        if len(candidate.encode("utf-8")) > max_bytes:
            if current:
                chunks.append(current.rstrip())
            current = block
        else:
            current = candidate

    if current:
        chunks.append(current.rstrip())
    return [chunk for chunk in chunks if chunk]


def _split_long_text_by_bytes(text: str, max_bytes: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if len(candidate.encode("utf-8")) > max_bytes:
            if current:
                chunks.append(current.rstrip())
            current = char
        else:
            current = candidate
    if current:
        chunks.append(current.rstrip())
    return chunks


def _build_retry_session(retries: int) -> requests.Session:
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=0.3,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
