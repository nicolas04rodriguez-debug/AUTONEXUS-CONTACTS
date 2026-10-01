import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ["SEND_WEEKDAYS_ONLY"] = "false"
os.environ["SEND_WINDOW_START"] = "00:00"
os.environ["SEND_WINDOW_END"] = "23:59"
os.environ["DAILY_SEND_CAP"] = "5"
os.environ["AUTO_APPROVE_FIRST_TOUCH"] = "false"
os.environ["AUTO_APPROVE_FOLLOWUPS"] = "true"
os.environ["SENDER_FULL_NAME"] = "Test Sender"
os.environ["FROM_EMAIL"] = "me@urbex.test"
os.environ["APOLLO_API_KEY"] = ""
os.environ["HUNTER_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from urbex_crm import db as dbmod  # noqa: E402
from urbex_crm.drafting import seed_templates  # noqa: E402
from urbex_crm.models import Base  # noqa: E402
from urbex_crm.sequences import ensure_default_sequences  # noqa: E402


@pytest.fixture()
def session():
    engine = dbmod.get_engine("sqlite:///:memory:")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with dbmod.session_scope() as s:
        seed_templates(s)
        ensure_default_sequences(s)
    with dbmod.session_scope() as s:
        yield s


class FakeMailer:
    def __init__(self):
        self.sent = []
        self.replies: set[str] = set()
        self.bounces: set[str] = set()

    def send(self, to_email, subject, body, in_reply_to=None, thread_id=None, to_name=None):
        from urbex_crm.mailer import SendResult
        self.sent.append({"to": to_email, "subject": subject, "body": body, "in_reply_to": in_reply_to, "thread_id": thread_id})
        tid = thread_id or f"thread-{len(self.sent)}"
        return SendResult(message_id_header=f"<msg{len(self.sent)}@urbex.test>", provider_message_id=f"m{len(self.sent)}", thread_id=tid)

    def thread_state(self, thread_id, sent_after_message_id=None):
        from urbex_crm.mailer import ThreadState
        return ThreadState(replied=thread_id in self.replies, bounced=thread_id in self.bounces, reply_snippet="gracias, me interesa" if thread_id in self.replies else None)


@pytest.fixture()
def mailer():
    return FakeMailer()
