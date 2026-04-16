import asyncio
import email
import html
import imaplib
import re
from dataclasses import dataclass
from email.header import decode_header
from typing import Optional

from loguru import logger

from app.dependencies import config


FIRSTMAIL_IMAP_HOST = config.get("FIRSTMAIL_IMAP_HOST", "imap.firstmail.ltd")
FIRSTMAIL_IMAP_PORT = int(config.get("FIRSTMAIL_IMAP_PORT", 993))


@dataclass
class FirstMailMessage:
    """
    Письмо, полученное из IMAP FirstMail.
    """
    uid: str
    from_header: str
    subject: str
    content: str


class FirstMailImapClient:
    """
    Синхронный IMAP-клиент для FirstMail.

    Используется через asyncio.to_thread(...), чтобы не блокировать event loop.
    """

    def __init__(
        self,
        email_addr: str,
        password: str,
        imap_host: str = FIRSTMAIL_IMAP_HOST,
        imap_port: int = FIRSTMAIL_IMAP_PORT,
    ):
        self.email_addr = email_addr
        self.password = password
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.imap: Optional[imaplib.IMAP4_SSL] = None

    @staticmethod
    def _decode_str(value: str) -> str:
        """
        Декодирует заголовок письма (From / Subject) в unicode.
        """
        if not value:
            return ""

        parts = decode_header(value)
        decoded_parts = []

        for chunk, charset in parts:
            if isinstance(chunk, bytes):
                charset = charset or "utf-8"
                try:
                    decoded_parts.append(chunk.decode(charset, errors="replace"))
                except LookupError:
                    decoded_parts.append(chunk.decode("utf-8", errors="replace"))
            else:
                decoded_parts.append(chunk)

        return "".join(decoded_parts)

    @staticmethod
    def _clean_html(raw_html: str) -> str:
        """
        Упрощённо чистит HTML до текста.
        """
        if not raw_html:
            return ""

        text = re.sub(r"<style[^>]*>.*?</style>", "", raw_html, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        text = text.replace("\xa0", " ")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s+\n", "\n\n", text)
        return text.strip()

    def _get_body_from_msg(self, msg) -> str:
        """
        Возвращает текст письма.

        Приоритет:
        1) text/plain
        2) text/html -> чистим в текст
        """
        text_plain = ""
        html_body = ""

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = str(part.get("Content-Disposition") or "").lower()

                if "attachment" in disposition:
                    continue

                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"

                try:
                    text = payload.decode(charset, errors="replace")
                except LookupError:
                    text = payload.decode("utf-8", errors="replace")

                if content_type == "text/plain" and not text_plain:
                    text_plain = text
                elif content_type == "text/html" and not html_body:
                    html_body = text
        else:
            content_type = msg.get_content_type()
            payload = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"

            try:
                text = payload.decode(charset, errors="replace")
            except LookupError:
                text = payload.decode("utf-8", errors="replace")

            if content_type == "text/plain":
                text_plain = text
            elif content_type == "text/html":
                html_body = text

        if text_plain.strip():
            return text_plain.strip()

        if html_body.strip():
            return self._clean_html(html_body)

        return ""

    def _connect(self) -> None:
        """
        Подключается к IMAP и открывает INBOX.
        """
        # logger.info(
        #     "IMAP FirstMail connect | host=%s port=%s email=%s",
        #     self.imap_host,
        #     self.imap_port,
        #     self.email_addr,
        # )

        self.imap = imaplib.IMAP4_SSL(self.imap_host, self.imap_port, timeout=30)
        self.imap.login(self.email_addr, self.password)
        self.imap.select("INBOX")

    def _get_all_uids(self) -> list[str]:
        """
        Возвращает список UID всех писем в INBOX.
        """
        rv, data = self.imap.uid("search", None, "ALL")
        if rv != "OK":
            logger.warning("IMAP uid search вернул %s для %s", rv, self.email_addr)
            return []

        raw_uids = data[0].split() if data and data[0] else []
        return [uid.decode("utf-8", errors="replace") for uid in raw_uids]

    def _fetch_message_by_uid(self, uid: str) -> Optional[FirstMailMessage]:
        """
        Загружает письмо по UID.
        """
        rv, msg_data = self.imap.uid("fetch", uid, "(RFC822)")
        if rv != "OK" or not msg_data:
            logger.warning("IMAP uid fetch не удался | email=%s uid=%s rv=%s", self.email_addr, uid, rv)
            return None

        raw_message = None
        for item in msg_data:
            if isinstance(item, tuple) and len(item) > 1:
                raw_message = item[1]
                break

        if not raw_message:
            return None

        msg = email.message_from_bytes(raw_message)

        from_header = self._decode_str(msg.get("From", ""))
        subject = self._decode_str(msg.get("Subject", ""))
        content = self._get_body_from_msg(msg)

        return FirstMailMessage(
            uid=uid,
            from_header=from_header,
            subject=subject,
            content=content,
        )

    def get_new_messages(
            self,
            known_uids: Optional[list] = None,
            limit: int = 5,
            is_initialized: bool = False,
    ) -> dict:
        """
        Возвращает новые письма относительно known_uids.

        Логика:
        - если is_initialized=False, это стартовая инициализация ящика:
          текущие письма считаем "старыми" и не отдаём пользователю;
        - если is_initialized=True, все письма, которых нет в known_uids,
          считаются новыми;
        - это решает проблему, когда inbox был пуст на момент инициализации:
          old_messages_id остаётся пустым, но ящик уже считается подготовленным.
        """
        known_uids = [str(uid) for uid in (known_uids or [])]

        try:
            self._connect()

            all_uids = self._get_all_uids()
            if not all_uids:
                return {
                    "initialized": is_initialized,
                    "messages": [],
                    "updated_old_uids": known_uids,
                }

            # Стартовая инициализация при аренде:
            # все текущие письма считаем уже существовавшими.
            if not is_initialized:
                logger.info(
                    "Инициализация old_messages_id для %s | писем=%s",
                    self.email_addr,
                    len(all_uids),
                )
                return {
                    "initialized": True,
                    "messages": [],
                    "updated_old_uids": all_uids,
                }

            known_set = set(known_uids)
            new_uids = [uid for uid in all_uids if uid not in known_set]

            if not new_uids:
                return {
                    "initialized": True,
                    "messages": [],
                    "updated_old_uids": known_uids,
                }

            selected_uids = new_uids[-limit:]
            messages: list[FirstMailMessage] = []

            for uid in selected_uids:
                message_obj = self._fetch_message_by_uid(uid)
                if message_obj:
                    messages.append(message_obj)

            updated_old_uids = known_uids + [uid for uid in selected_uids if uid not in known_set]

            return {
                "initialized": True,
                "messages": messages,
                "updated_old_uids": updated_old_uids,
            }

        except Exception:
            logger.exception("Ошибка при чтении FirstMail через IMAP для %s", self.email_addr)
            raise

        finally:
            try:
                if self.imap:
                    self.imap.logout()
            except Exception:
                pass

async def fetch_firstmail_messages_async(
    email_addr: str,
    password: str,
    known_uids: Optional[list] = None,
    limit: int = 5,
    is_initialized: bool = False,
) -> dict:
    """
    Асинхронная обёртка над FirstMailImapClient.get_new_messages().
    """

    def _worker() -> dict:
        client = FirstMailImapClient(
            email_addr=email_addr,
            password=password,
        )
        return client.get_new_messages(
            known_uids=known_uids,
            limit=limit,
            is_initialized=is_initialized,
        )

    return await asyncio.to_thread(_worker)