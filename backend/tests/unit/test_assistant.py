"""Ask ANAVA assistant + session feedback (app/modules/assistant). No database:
the repository and the AI provider are replaced by fakes."""

from contextlib import asynccontextmanager
from datetime import date, time, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.exceptions import BusinessRuleError, ConflictError, PermissionError_
from app.integrations.ai_provider import AIResult
from app.modules.assistant import data_answers, safety
from app.modules.assistant import service as svc
from app.modules.assistant.schemas import AskRequest, FeedbackSubmit
from app.modules.assistant.textproc import MATCH_THRESHOLD, fill, output_ok, rank_faq, redact

# -- safety --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "screen"),
    [
        ("I want to kill myself", "S1"),
        ("मैं अपनी ज़िंदगी खत्म करना चाहता हूँ, आत्महत्या", "S1"),
        ("मला मरायचं आहे", "S1"),
        ("I have chest pain and can't breathe", "S0"),
        ("my husband is hitting me", "S4"),
        ("there is a blister on my scalp", "S2"),
        ("I feel very low today", "S3"),
        # chest pain outranks feeling low
        ("feeling low and chest pain", "S0"),
    ],
)
def test_safety_detects(text, screen):
    assert safety.detect(text) == screen


@pytest.mark.parametrize("text", ["What diet should I follow?", "When is my next session?", "Studies show it works", ""])
def test_safety_ignores_ordinary_text(text):
    assert safety.detect(text) is None


def test_safety_message_has_helpline_and_clinic_in_every_language():
    for lang in ("en", "hi", "mr"):
        msg = safety.message("S1", lang, "+91 80000 00000")
        assert "14416" in msg and "+91 80000 00000" in msg


# -- text helpers --------------------------------------------------------------

FAQ = [
    {"faq_id": uuid4(), "intent": "refund_policy", "questions": ["What is the refund policy?", "Do I get money back if I cancel?"],
     "answer": "Refunds depend on how early you cancel.", "reply_type": "faq", "action": "refund_policy"},
    {"faq_id": uuid4(), "intent": "prepare", "questions": ["How should I prepare for my session?"],
     "answer": "Wash your hair and don't apply oil.", "reply_type": "faq", "action": None},
]  # fmt: skip


def test_faq_matches_reworded_question():
    best_score, best = rank_faq("whats the refund policy", FAQ, "en")[0]
    assert best["intent"] == "refund_policy" and best_score >= MATCH_THRESHOLD


def test_faq_unrelated_question_stays_below_threshold():
    assert rank_faq("Can I bring my dog to the clinic?", FAQ, "en")[0][0] < MATCH_THRESHOLD


def test_redact_removes_identifiers():
    out = redact("I am Aarav, call 98765 43210 or aarav@x.com, id 0198a1b2-3c4d-7e5f-8a9b-0c1d2e3f4a5b", ["Aarav"])
    assert "Aarav" not in out and "98765" not in out and "@" not in out and "0198a1b2" not in out


@pytest.mark.parametrize(
    "text", ["Take 50 mg twice a day.", "You may have depression.", "Stop taking your tablets.", "दवा लें", "", "x" * 1300]
)
def test_output_check_blocks_medical_advice(text):
    assert not output_ok(text)


def test_output_check_allows_plain_help():
    assert output_ok("Open the visit and tap Reschedule.")


def test_fill_keeps_unknown_placeholders_visible():
    assert fill("Call {clinic_phone} on {next_date}", {"clinic_phone": "123"}) == "Call 123 on {next_date}"


# -- own-data questions --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("When is my next session?", "next_session"),
        ("मेरा अगला सेशन कब है?", "next_session"),
        ("माझं पुढचं सेशन कधी आहे?", "next_session"),
        ("Do I have to pay anything?", "payment_due"),
        ("मला काही पैसे भरायचे आहेत का?", "payment_due"),
        ("Where is my refund?", "refund_status"),
        ("माझा रिफंड कुठे आहे?", "refund_status"),
        ("How many sessions are left?", "sessions_left"),
        ("किती सेशन बाकी आहेत?", "sessions_left"),
        ("Where is the clinic?", "clinic_address"),
        ("क्लिनिक का नंबर दीजिए", "clinic_phone"),
        ("Do I have a session today?", "today"),
        ("Who is my doctor?", "my_doctor"),
    ],
)
def test_data_intent_detected(text, intent):
    assert data_answers.detect(text) == intent


def test_data_intent_not_triggered_by_general_question():
    assert data_answers.detect("Does tDCS hurt?") is None


VISIT = {"appointment_type": "device_session", "appointment_date": date(2026, 10, 22), "start_time": time(10, 30),
         "status": "paid", "session_number": 5, "session_count": 20, "device_name": "Flow", "amount_due": None}  # fmt: skip


def test_next_session_answer_in_three_languages():
    facts = {"clinic": {"clinic_name": "Anava Clinic"}, "upcoming": [VISIT], "today": date(2026, 10, 21)}
    en, action = data_answers.answer("next_session", facts, "en")
    assert en == "Your next visit is Flow Session 5 / 20 on Thu, 22 Oct at 10:30 AM at Anava Clinic. It is confirmed and paid."
    assert action == "appointment_detail"
    assert "अक्टूबर" in data_answers.answer("next_session", facts, "hi")[0]
    assert "ऑक्टोबर" in data_answers.answer("next_session", facts, "mr")[0]


def test_payment_due_includes_amount_and_pay_button():
    due = {**VISIT, "status": "selected", "amount_due": 1500}
    reply, action = data_answers.answer("payment_due", {"upcoming": [due], "today": date(2026, 10, 21)}, "en")
    assert "₹1,500" in reply and action == "pay"


def test_nothing_due():
    assert data_answers.answer("payment_due", {"upcoming": [VISIT], "today": date(2026, 10, 21)}, "en")[0] == "Nothing is due right now."


# -- chat pipeline -------------------------------------------------------------


class FakeRepo:
    def __init__(self, faq=None, consent=True, used=0):
        self.messages, self._faq, self._consent, self._used = [], faq or [], consent, used

    async def consent(self, pid, kind, version):
        return {"withdrawn_at": None, "accepted_at": None} if self._consent else None

    async def clinic(self, pid):
        return {"clinic_name": "Anava Clinic", "phone": "+91 80000 00000", "address": "MG Road", "city": "Pune"}

    async def profile(self, pid):
        return {"first_name": "Aarav", "last_name": "Sharma"}

    async def patient_messages_since(self, pid, since):
        return self._used

    async def add_message(self, data):
        self.messages.append(data)
        return data

    async def upcoming(self, pid, today):
        return [VISIT]

    async def faq(self, lang):
        return self._faq

    async def recent_turns(self, pid, cid, n):
        return [{"role": m["role"], "body": m["body"], "answered_by": m.get("answered_by")} for m in self.messages][-n:]


@asynccontextmanager
async def _no_db():
    yield None


@pytest.fixture
def ai_calls(monkeypatch):
    calls = []

    async def fake_generate(system, turns, **_):
        calls.append({"system": system, "turns": turns})
        return AIResult("You can bring a family member along.", "ok", "gemini", "test-model", 100, 10, 50)

    async def no_calls_yet(conn, since):
        return 0

    async def log(conn, data):
        calls.append({"logged": data})

    monkeypatch.setattr(svc.ai_provider, "generate", fake_generate)
    monkeypatch.setattr(svc.ai_provider, "provider_enabled", lambda: True)
    monkeypatch.setattr(svc, "system_transaction", _no_db)
    monkeypatch.setattr(svc, "ai_calls_since", no_calls_yet)
    monkeypatch.setattr(svc, "log_ai_call", log)
    return calls


def _service(repo):
    service = svc.AssistantService(session=None)
    service.repo = repo
    return service


CTX = SimpleNamespace(user_id=str(uuid4()))


def _ask(text, lang="en"):
    return AskRequest(conversation_id=uuid4(), message=text, language=lang)


async def test_consent_required():
    with pytest.raises(PermissionError_):
        await _service(FakeRepo(consent=False)).ask(CTX, _ask("hi"))


async def test_safety_answers_before_anything_else(ai_calls):
    reply = await _service(FakeRepo()).ask(CTX, _ask("I want to end my life"))
    assert reply["answered_by"] == "safety" and reply["safety_screen"] == "S1" and not ai_calls


async def test_own_data_never_goes_to_ai(ai_calls):
    reply = await _service(FakeRepo()).ask(CTX, _ask("When is my next session?"))
    assert reply["answered_by"] == "data" and "Flow Session 5" in reply["reply"] and not ai_calls


async def test_stored_answer_used_without_ai(ai_calls):
    reply = await _service(FakeRepo(faq=FAQ)).ask(CTX, _ask("What's your refund policy?"))
    assert reply["answered_by"] == "faq" and reply["action"] == "refund_policy" and not ai_calls


async def test_ai_gets_no_identity_or_records(ai_calls):
    repo = FakeRepo(faq=FAQ)
    service = _service(repo)
    await service.ask(CTX, _ask("When is my next session?"))  # data reply, must not become AI context
    reply = await service.ask(CTX, _ask("I'm Aarav, can my wife come along? my number is 98765 43210"))
    assert reply["answered_by"] == "ai" and reply["demo"] is True
    sent = ai_calls[0]
    everything = sent["system"] + " ".join(t for _, t in sent["turns"])
    assert "Aarav" not in everything and "98765" not in everything and "Flow Session 5" not in everything
    assert [r for r, _ in sent["turns"]][0] == "patient"
    assert ai_calls[-1]["logged"]["outcome"] == "ok"


async def test_ai_no_answer_falls_back_to_clinic(ai_calls, monkeypatch):
    async def says_no(system, turns, **_):
        return AIResult("NO_ANSWER", "ok", "gemini", "m")

    monkeypatch.setattr(svc.ai_provider, "generate", says_no)
    reply = await _service(FakeRepo()).ask(CTX, _ask("Can I bring my dog?"))
    assert reply["answered_by"] == "fallback" and "+91 80000 00000" in reply["reply"]


async def test_ai_medical_advice_is_blocked(ai_calls, monkeypatch):
    async def advises(system, turns, **_):
        return AIResult("Take 50 mg of sertraline daily.", "ok", "gemini", "m")

    monkeypatch.setattr(svc.ai_provider, "generate", advises)
    reply = await _service(FakeRepo()).ask(CTX, _ask("What should I take for sleep?"))
    assert reply["answered_by"] == "fallback"
    assert ai_calls[-1]["logged"]["outcome"] == "blocked_output"


async def test_daily_limit_stops_without_storing(ai_calls):
    repo = FakeRepo(used=30)
    reply = await _service(repo).ask(CTX, _ask("hello"))
    assert reply["answered_by"] == "limit" and reply["remaining_today"] == 0 and not repo.messages


def test_merge_turns_alternates_from_patient():
    assert svc.merge_turns([("assistant", "a"), ("patient", "1"), ("patient", "2"), ("assistant", "b")]) == [
        ("patient", "1\n2"),
        ("assistant", "b"),
    ]


# -- feedback ------------------------------------------------------------------


def test_feedback_rejects_out_of_range_rating():
    with pytest.raises(ValidationError):
        FeedbackSubmit(form_version="device-2026-10-v1", overall_rating=6)


@pytest.mark.parametrize(
    ("severity", "comment", "screen"),
    [("severe", None, "S2"), ("mild", None, None), ("severe", "I want to die", "S1"), (None, "very sad today", "S3")],
)
def test_feedback_safety_screen(severity, comment, screen):
    assert svc.feedback_safety_screen(severity, comment) == screen


def test_comment_tags():
    assert svc.tag_comment("Waited 40 minutes and the pad was too wet") == (["wait", "device"], "negative")
    assert svc.tag_comment("Very helpful staff, thank you") == (["staff", "praise"], "negative")
    assert svc.tag_comment("Excellent, thank you") == (["praise"], "positive")


class FeedbackRepo(FakeRepo):
    def __init__(self, status="completed", days_ago=0, exists=False):
        super().__init__()
        self.appt = {**VISIT, "status": status, "appointment_date": svc._now_ist().date() - timedelta(days=days_ago),
                     "clinic_id": uuid4(), "doctor_id": None, "protocol_id": None}  # fmt: skip
        self.exists, self.saved = exists, None

    async def appointment_for_feedback(self, pid, aid):
        return self.appt

    async def feedback_exists(self, aid):
        return self.exists

    async def add_feedback(self, data):
        self.saved = data
        return {"feedback_id": uuid4(), "status": data["status"]}


async def test_feedback_saved_with_question_ids():
    repo = FeedbackRepo()
    body = FeedbackSubmit(
        form_version="device-2026-10-v1", overall_rating=4, side_effects=["headache"], side_effect_severity="severe", comment="long wait"
    )
    result = await _service(repo).submit_feedback(CTX, uuid4(), body)
    assert repo.saved["answers"] == {"F1": 4, "F4": ["headache"], "F4a": "severe", "F11": "long wait"}
    assert repo.saved["comment_tags"] == ["wait"] and result["safety_screen"] == "S2" and "+91 80000 00000" in result["safety_message"]


async def test_feedback_only_after_completion_and_within_window():
    with pytest.raises(BusinessRuleError):
        await _service(FeedbackRepo(status="paid")).submit_feedback(CTX, uuid4(), FeedbackSubmit(form_version="v1"))
    with pytest.raises(BusinessRuleError):
        await _service(FeedbackRepo(days_ago=3)).submit_feedback(CTX, uuid4(), FeedbackSubmit(form_version="v1"))
    with pytest.raises(ConflictError):
        await _service(FeedbackRepo(exists=True)).submit_feedback(CTX, uuid4(), FeedbackSubmit(form_version="v1"))
