import asyncio
import email
import html
import imaplib
import mimetypes
import re
from dataclasses import dataclass, field
from email.header import decode_header
from typing import Optional

from loguru import logger

from app.dependencies import config


FIRSTMAIL_IMAP_HOST = config.get("FIRSTMAIL_IMAP_HOST", "imap.firstmail.ltd")
FIRSTMAIL_IMAP_PORT = int(config.get("FIRSTMAIL_IMAP_PORT", 993))

# Потолок Telegram Bot API на sendDocument — 50 МБ, берём с запасом,
# чтобы не раздувать RSS процесса при пачке писем.
FIRSTMAIL_MAX_ATTACHMENT_MB = float(config.get("FIRSTMAIL_MAX_ATTACHMENT_MB", 20))
FIRSTMAIL_MAX_ATTACHMENT_BYTES = int(FIRSTMAIL_MAX_ATTACHMENT_MB * 1024 * 1024)

# Мелкие inline-картинки — это логотипы из подписей, а не вложения.
INLINE_IMAGE_SKIP_BYTES = 50 * 1024


@dataclass
class FirstMailAttachment:
    """
    Вложение письма.

    content = None означает, что файл слишком большой и байты
    намеренно не загружались в память; причина лежит в skip_reason.
    """
    filename: str
    content_type: str
    size: int
    content: Optional[bytes] = None
    skip_reason: Optional[str] = None
    charset: Optional[str] = None


@dataclass
class FirstMailMessage:
    """
    Письмо, полученное из IMAP FirstMail.
    """
    uid: str
    from_header: str
    subject: str
    content: str
    attachments: list = field(default_factory=list)


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
        # Условные комментарии Word/Outlook (<!--[if gte mso 9]>...<![endif]-->)
        # содержат только служебную разметку — вырезаем целиком.
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        text = text.replace("\xa0", " ")
        text = re.sub(r"[ \t]+", " ", text)
        # После замены </p> на \n в начале строк остаётся пробел от съеденного <p>
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"\n\s+\n", "\n\n", text)
        return text.strip()

    @staticmethod
    def _decode_payload(payload: bytes, charset: Optional[str]) -> str:
        """
        Декодирует байты текстовой части письма в unicode.
        """
        try:
            return payload.decode(charset or "utf-8", errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")

    def _build_attachment(self, part, index: int, payload: bytes) -> FirstMailAttachment:
        """
        Собирает вложение из MIME-части.

        Слишком большие файлы не тащим в память: отдаём метаданные
        с пустым content, чтобы пользователь хотя бы узнал о файле.
        """
        content_type = part.get_content_type()
        size = len(payload)

        filename = self._decode_str(part.get_filename() or "").strip()
        if not filename:
            ext = mimetypes.guess_extension(content_type) or ".bin"
            filename = f"attachment_{index}{ext}"

        if size > FIRSTMAIL_MAX_ATTACHMENT_BYTES:
            return FirstMailAttachment(
                filename=filename,
                content_type=content_type,
                size=size,
                content=None,
                skip_reason="too_large",
                charset=part.get_content_charset(),
            )

        return FirstMailAttachment(
            filename=filename,
            content_type=content_type,
            size=size,
            content=payload,
            charset=part.get_content_charset(),
        )

    def _extract_body_and_attachments(self, msg) -> tuple[str, list]:
        """
        Возвращает (текст письма, список вложений).

        Приоритет тела:
        1) text/plain
        2) text/html -> чистим в текст
        3) если тела нет вообще — текстовое вложение
           (некоторые системы шлют тело письма с filename=)
        """
        text_plain = ""
        html_body = ""
        attachments: list[FirstMailAttachment] = []

        index = 0
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue

            index += 1
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "").lower()
            filename = part.get_filename()

            is_attachment = "attachment" in disposition or bool(filename)
            payload = part.get_payload(decode=True) or b""

            if is_attachment:
                if not payload:
                    continue

                # Логотипы из подписей приходят как inline-картинки —
                # засорять ими чат не нужно.
                if (
                    content_type.startswith("image/")
                    and "attachment" not in disposition
                    and len(payload) < INLINE_IMAGE_SKIP_BYTES
                ):
                    continue

                attachments.append(self._build_attachment(part, index, payload))
                continue

            text = self._decode_payload(payload, part.get_content_charset())

            if content_type == "text/plain" and not text_plain:
                text_plain = text
            elif content_type == "text/html" and not html_body:
                html_body = text

        body = ""

        if text_plain.strip():
            body = text_plain.strip()
        elif html_body.strip():
            body = self._clean_html(html_body)

        # Тело письма могло приехать вложением — достаём его оттуда.
        if not body:
            body = self._body_from_text_attachment(attachments)

        return body, attachments

    def _body_from_text_attachment(self, attachments: list) -> str:
        """
        Пытается взять тело письма из текстового вложения.

        Вложение при этом остаётся в списке: пользователь получит
        и текст, и исходный файл.
        """
        for attachment in attachments:
            if not attachment.content:
                continue

            if attachment.content_type == "text/plain":
                text = self._decode_payload(attachment.content, attachment.charset)
                if text.strip():
                    return text.strip()

            if attachment.content_type == "text/html":
                text = self._clean_html(
                    self._decode_payload(attachment.content, attachment.charset)
                )
                if text.strip():
                    return text

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
        content, attachments = self._extract_body_and_attachments(msg)

        return FirstMailMessage(
            uid=uid,
            from_header=from_header,
            subject=subject,
            content=content,
            attachments=attachments,
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