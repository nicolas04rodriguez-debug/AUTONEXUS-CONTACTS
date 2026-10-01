"""Outbound email + reply detection.

GmailAPIMailer (recommended): OAuth2 installed-app flow, sends through the
account's own Gmail (threads follow-ups correctly, replies land in your inbox,
reply/bounce detection reads the thread back).
SMTPMailer: fallback via app password; no reply detection.
"""
from __future__ import annotations

import base64
import email.utils
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from .config import settings

log = logging.getLogger(__name__)

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.readonly"]


@dataclass
class SendResult:
    message_id_header: str
    provider_message_id: str | None = None
    thread_id: str | None = None


@dataclass
class ThreadState:
    replied: bool
    bounced: bool
    reply_snippet: str | None = None


def build_mime(to_email: str, subject: str, body: str, in_reply_to: str | None = None,
               to_name: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    from_name = settings.from_name or settings.sender_full_name
    msg["From"] = email.utils.formataddr((from_name, settings.from_email)) if from_name else settings.from_email
    msg["To"] = email.utils.formataddr((to_name, to_email)) if to_name else to_email
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    domain = (settings.from_email.split("@")[-1] if "@" in settings.from_email else "urbex.local")
    msg["Message-ID"] = email.utils.make_msgid(domain=domain)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    return msg


class SMTPMailer:
    def send(self, to_email: str, subject: str, body: str, in_reply_to: str | None = None,
             thread_id: str | None = None, to_name: str | None = None) -> SendResult:
        msg = build_mime(to_email, subject, body, in_reply_to, to_name)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as s:
            s.starttls()
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
        return SendResult(message_id_header=msg["Message-ID"])

    def thread_state(self, thread_id: str, sent_after_message_id: str | None = None) -> ThreadState:
        return ThreadState(replied=False, bounced=False)


class GmailAPIMailer:
    def __init__(self, credentials_file: Path | None = None, token_file: Path | None = None):
        self.credentials_file = Path(credentials_file or settings.gmail_credentials_file)
        self.token_file = Path(token_file or settings.gmail_token_file)
        self._service = None

    # -- auth -----------------------------------------------------------------
    def authorize(self, headless: bool = True):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        creds = None
        if self.token_file.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_file), GMAIL_SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.credentials_file.exists():
                    raise FileNotFoundError(
                        f"Gmail OAuth client file missing: {self.credentials_file}. "
                        "Create a Desktop OAuth client in Google Cloud Console and download it here.")
                flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_file), GMAIL_SCOPES)
                creds = flow.run_local_server(port=0, open_browser=not headless) if not headless else flow.run_local_server(port=0, open_browser=False)
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.token_file.write_text(creds.to_json())
        return creds

    @property
    def service(self):
        if self._service is None:
            from googleapiclient.discovery import build
            self._service = build("gmail", "v1", credentials=self.authorize(), cache_discovery=False)
        return self._service

    # -- send -----------------------------------------------------------------
    def send(self, to_email: str, subject: str, body: str, in_reply_to: str | None = None,
             thread_id: str | None = None, to_name: str | None = None) -> SendResult:
        msg = build_mime(to_email, subject, body, in_reply_to, to_name)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        payload = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id
        sent = self.service.users().messages().send(userId="me", body=payload).execute()
        return SendResult(message_id_header=msg["Message-ID"], provider_message_id=sent.get("id"), thread_id=sent.get("threadId"))

    # -- reply / bounce detection --------------------------------------------
    def thread_state(self, thread_id: str, sent_after_message_id: str | None = None) -> ThreadState:
        thread = self.service.users().threads().get(userId="me", id=thread_id, format="metadata",
                                                    metadataHeaders=["From", "Subject"]).execute()
        me = settings.from_email.lower()
        replied = bounced = False
        snippet = None
        for m in thread.get("messages", []):
            headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
            sender = headers.get("from", "").lower()
            if "mailer-daemon" in sender or "postmaster" in sender or "delivery status notification" in headers.get("subject", "").lower():
                bounced = True
                continue
            if me and me in sender:
                continue
            if "SENT" in m.get("labelIds", []):
                continue
            replied = True
            snippet = m.get("snippet")
        return ThreadState(replied=replied, bounced=bounced, reply_snippet=snippet)

    def profile_email(self) -> str:
        return self.service.users().getProfile(userId="me").execute().get("emailAddress", "")


def get_mailer():
    if settings.mail_backend == "smtp":
        return SMTPMailer()
    return GmailAPIMailer()
