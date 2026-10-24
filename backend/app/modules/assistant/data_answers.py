"""Questions about the patient's own records ("When is my next session?").

Detected with keyword rules in English, Hindi and Marathi and answered from the
database with fixed templates. The AI provider never sees this data and is
never asked these questions.
"""

from __future__ import annotations

import re
from datetime import date, time

# Checked in order: more specific intents first ("refund" before "payment due").
_INTENTS: list[tuple[str, str, list[str]]] = [
    (
        "refund_status",
        # The patient's own refund only; "what is the refund policy?" is a stored FAQ answer.
        r"my refunds?\b|(where|when|status).{0,20}\brefund|refund.{0,25}(status|received|credited|come|not (got|received))",
        ["मेरा रिफंड", "रिफंड कहाँ", "रिफंड कहां", "रिफंड कब", "माझा रिफंड", "रिफंड कुठे", "रिफंड कधी", "पैसे वापस कब"],
    ),
    (
        "total_paid",
        r"how much (have i|did i|i have) paid|total (paid|payment|amount paid)|paid (so far|in total)",
        ["कुल कितना", "कितना पैसा दिया", "एकूण किती", "एकूण भरणा"],
    ),
    (
        "payment_due",
        r"(payment|amount|fee)s? (is )?(due|pending)|do i (have|need) to pay|anything to pay"
        r"|any(thing)? (due|pending)|how much (do i|should i) pay|what do i owe",
        ["पैसे देने", "भुगतान बाकी", "पेमेंट बाकी", "पैसे भरायचे", "पेमेंट बाकी आहे", "भरणा बाकी"],
    ),
    (
        "sessions_left",
        r"(how many )?sessions? (are )?(left|remaining|pending)|how many (more )?sessions",
        ["कितने सेशन", "सेशन बाकी", "किती सेशन"],
    ),
    (
        "today",
        r"(session|appointment|visit)s? today|today'?s? (session|appointment|visit)|anything today",
        ["आज मेरा", "आज कोई", "आज सेशन", "आज अपॉइंटमेंट", "आज माझं", "आज काही"],
    ),
    (
        "last_session",
        r"(last|previous) (session|appointment|visit)",
        ["पिछला सेशन", "पिछली विज़िट", "पिछली अपॉइंटमेंट", "मागचं सेशन", "मागील सेशन", "शेवटचं सेशन"],
    ),
    (
        "next_session",
        r"next (session|appointment|visit)|when is my (session|appointment|visit)|upcoming (session|appointment|visit)",
        ["अगला सेशन", "अगली अपॉइंटमेंट", "अगला अपॉइंटमेंट", "अगली विज़िट", "पुढचं सेशन", "पुढील सेशन", "पुढची अपॉइंटमेंट", "पुढचं अपॉइंटमेंट"],
    ),
    (
        "my_doctor",
        r"who is my doctor|my doctor'?s? name|which doctor",
        ["मेरे डॉक्टर कौन", "डॉक्टर कौन", "माझे डॉक्टर कोण", "डॉक्टर कोण"],
    ),
    (
        "clinic_phone",
        r"clinic'?s? (phone|number|contact)|(phone|contact) number of (the )?clinic|call (the|my) clinic",
        ["क्लिनिक का नंबर", "क्लिनिक का फोन", "क्लिनिक का फ़ोन", "क्लिनिकचा नंबर", "क्लिनिकचा फोन"],
    ),
    (
        "clinic_address",
        r"where is (the|my) clinic|clinic'?s? (address|location)|address of (the|my) clinic"
        r"|how (do i|to) (get|reach|go) to (the|my) clinic",
        ["क्लिनिक कहाँ", "क्लिनिक कहां", "क्लिनिक का पता", "क्लिनिक कुठे", "क्लिनिकचा पत्ता"],
    ),
]
_COMPILED = [(intent, re.compile(latin, re.IGNORECASE), deva) for intent, latin, deva in _INTENTS]


def detect(text: str) -> str | None:
    for intent, latin, deva in _COMPILED:
        if latin.search(text) or any(p in text for p in deva):
            return intent
    return None


# -- formatting ------------------------------------------------------------------

_MONTHS = {
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "hi": ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"],
    "mr": ["जानेवारी", "फेब्रुवारी", "मार्च", "एप्रिल", "मे", "जून", "जुलै", "ऑगस्ट", "सप्टेंबर", "ऑक्टोबर", "नोव्हेंबर", "डिसेंबर"],
}
_DAYS = {
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "hi": ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
    "mr": ["सोमवार", "मंगळवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
}
_TYPES = {
    "initial": {"en": "Initial consultation", "hi": "पहला परामर्श", "mr": "पहिला सल्ला"},
    "follow_up": {"en": "Follow-up", "hi": "फ़ॉलो-अप", "mr": "फॉलो-अप"},
    "protocol_followup": {"en": "Protocol follow-up", "hi": "प्रोटोकॉल फ़ॉलो-अप", "mr": "प्रोटोकॉल फॉलो-अप"},
    "device_session": {"en": "Device session", "hi": "डिवाइस सेशन", "mr": "डिव्हाइस सेशन"},
}
_STATUS = {
    "planned": {
        "en": "planned, you still need to pick a time",
        "hi": "तय है, आपको अभी समय चुनना है",
        "mr": "ठरलेली आहे, तुम्हाला अजून वेळ निवडायची आहे",
    },
    "selected": {
        "en": "held for you, waiting for payment",
        "hi": "आपके लिए रोकी गई है, भुगतान बाकी है",
        "mr": "तुमच्यासाठी राखून ठेवली आहे, पेमेंट बाकी आहे",
    },
    "paid": {"en": "confirmed and paid", "hi": "पक्की है और भुगतान हो चुका है", "mr": "निश्चित आहे आणि पैसे भरलेले आहेत"},
    "scheduled": {"en": "confirmed", "hi": "पक्की है", "mr": "निश्चित आहे"},
    "confirmed": {"en": "confirmed", "hi": "पक्की है", "mr": "निश्चित आहे"},
    "checked_in": {"en": "checked in", "hi": "चेक-इन हो चुका है", "mr": "चेक-इन झाले आहे"},
    "in_progress": {"en": "in progress", "hi": "चल रही है", "mr": "सुरू आहे"},
}


def fmt_date(d: date, lang: str) -> str:
    return f"{_DAYS[lang][d.weekday()]}, {d.day} {_MONTHS[lang][d.month - 1]}"


def fmt_time(t: time | None) -> str:
    if not t:
        return ""
    hour = t.hour % 12 or 12
    return f"{hour}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def fmt_money(amount) -> str:
    try:
        return f"₹{float(amount):,.0f}"
    except (TypeError, ValueError):
        return ""


def visit_label(row: dict, lang: str) -> str:
    label = _TYPES.get(row.get("appointment_type") or "", {}).get(lang) or _TYPES["follow_up"][lang]
    if row.get("appointment_type") == "device_session" and row.get("session_number"):
        name = (row.get("device_name") or "").strip()
        label = f"{name + ' ' if name else ''}{'Session' if lang == 'en' else 'सेशन'} {row['session_number']}"
        if row.get("session_count"):
            label += f" / {row['session_count']}"
    return label


def when(row: dict, lang: str) -> str:
    t = fmt_time(row.get("start_time"))
    joiner = {"en": " at ", "hi": ", ", "mr": ", "}[lang]
    return fmt_date(row["appointment_date"], lang) + (joiner + t if t else "")


# -- answers -------------------------------------------------------------------
# Each takes the facts the service gathered and returns (reply, action).

T = {
    "next_session": {
        "en": "Your next visit is {what} on {when} at {clinic}. It is {status}.",
        "hi": "आपकी अगली विज़िट {what} है, {when}, {clinic} में। यह {status}।",
        "mr": "तुमची पुढची भेट {what} आहे, {when}, {clinic} येथे. ही {status}.",
    },
    "no_upcoming": {
        "en": "You have no upcoming visits. You can book one from Appointments.",
        "hi": "आपकी कोई आने वाली विज़िट नहीं है। आप अपॉइंटमेंट में जाकर बुक कर सकते हैं।",
        "mr": "तुमची कोणतीही आगामी भेट नाही. अपॉइंटमेंटमध्ये जाऊन बुक करू शकता.",
    },
    "today_yes": {
        "en": "Yes, today you have {list}.",
        "hi": "हाँ, आज आपकी {list} है।",
        "mr": "हो, आज तुमची {list} आहे.",
    },
    "today_no": {
        "en": "You have nothing booked today. Your next visit is {next}.",
        "hi": "आज आपकी कोई विज़िट नहीं है। आपकी अगली विज़िट {next} है।",
        "mr": "आज तुमची कोणतीही भेट नाही. तुमची पुढची भेट {next} आहे.",
    },
    "sessions_left": {
        "en": "You have completed {done} of your device sessions, and {left} more are planned.",
        "hi": "आपने {done} डिवाइस सेशन पूरे कर लिए हैं, और {left} और तय हैं।",
        "mr": "तुम्ही {done} डिव्हाइस सेशन पूर्ण केले आहेत, आणि आणखी {left} ठरले आहेत.",
    },
    "no_sessions": {
        "en": "You don't have device sessions in a treatment plan yet. Your doctor adds them when your treatment starts.",
        "hi": "आपकी उपचार योजना में अभी कोई डिवाइस सेशन नहीं है। उपचार शुरू होने पर डॉक्टर इन्हें जोड़ते हैं।",
        "mr": "तुमच्या उपचार योजनेत अजून डिव्हाइस सेशन नाहीत. उपचार सुरू झाल्यावर डॉक्टर ते जोडतात.",
    },
    "payment_due": {
        "en": "Yes, payment is due for your {what} on {when}{amount}. Pay before the hold ends to keep the slot.",
        "hi": "हाँ, {when} की आपकी {what} का भुगतान बाकी है{amount}। स्लॉट बनाए रखने के लिए समय रहते भुगतान करें।",
        "mr": "हो, {when} च्या तुमच्या {what} चे पेमेंट बाकी आहे{amount}. स्लॉट राखण्यासाठी वेळेत पेमेंट करा.",
    },
    "nothing_due": {
        "en": "Nothing is due right now.",
        "hi": "अभी कोई भुगतान बाकी नहीं है।",
        "mr": "सध्या कोणतेही पेमेंट बाकी नाही.",
    },
    "refund": {
        "en": "Your refund of {amount} is {state}. Refunds go back to the account you paid from, usually within 5–7 working days.",
        "hi": "आपका {amount} का रिफंड {state}। रिफंड उसी खाते में जाता है जिससे भुगतान किया था, आमतौर पर 5–7 कार्य दिवसों में।",
        "mr": "तुमचा {amount} चा रिफंड {state}. रिफंड ज्या खात्यातून पैसे भरले त्याच खात्यात जातो, साधारण 5–7 कामकाजी दिवसांत.",
    },
    "no_refund": {
        "en": "You have no refunds in progress.",
        "hi": "आपका कोई रिफंड प्रक्रिया में नहीं है।",
        "mr": "तुमचा कोणताही रिफंड प्रक्रियेत नाही.",
    },
    "total_paid": {
        "en": "You have paid {amount} for {count} visit(s) so far.",
        "hi": "आपने अब तक {count} विज़िट के लिए {amount} का भुगतान किया है।",
        "mr": "तुम्ही आतापर्यंत {count} भेटींसाठी {amount} भरले आहेत.",
    },
    "last_session": {
        "en": "Your last completed visit was {what} on {when}.",
        "hi": "आपकी पिछली पूरी हुई विज़िट {what} थी, {when}।",
        "mr": "तुमची मागील पूर्ण झालेली भेट {what} होती, {when}.",
    },
    "no_last": {
        "en": "You don't have any completed visits yet.",
        "hi": "अभी तक आपकी कोई विज़िट पूरी नहीं हुई है।",
        "mr": "अजून तुमची कोणतीही भेट पूर्ण झालेली नाही.",
    },
    "my_doctor": {
        "en": "Your doctor is {doctor}.",
        "hi": "आपके डॉक्टर {doctor} हैं।",
        "mr": "तुमचे डॉक्टर {doctor} आहेत.",
    },
    "no_doctor": {
        "en": "A doctor is assigned after your first consultation.",
        "hi": "पहले परामर्श के बाद डॉक्टर तय किए जाते हैं।",
        "mr": "पहिल्या सल्ल्यानंतर डॉक्टर नेमले जातात.",
    },
    "clinic_phone": {
        "en": "{clinic} can be reached at {phone}.",
        "hi": "{clinic} का नंबर {phone} है।",
        "mr": "{clinic} चा नंबर {phone} आहे.",
    },
    "clinic_address": {
        "en": "{clinic}: {address}.",
        "hi": "{clinic}: {address}।",
        "mr": "{clinic}: {address}.",
    },
    "no_clinic": {
        "en": "I couldn't find your clinic's details. Please check Home › Your clinic.",
        "hi": "आपके क्लिनिक की जानकारी नहीं मिली। कृपया होम › आपका क्लिनिक देखें।",
        "mr": "तुमच्या क्लिनिकची माहिती मिळाली नाही. कृपया होम › तुमचे क्लिनिक पहा.",
    },
}
_REFUND_STATE = {
    "pending": {"en": "being processed", "hi": "प्रक्रिया में है", "mr": "प्रक्रियेत आहे"},
    "processed": {"en": "credited", "hi": "जमा हो चुका है", "mr": "जमा झाला आहे"},
    "failed": {"en": "delayed, please call your clinic", "hi": "रुका हुआ है, कृपया क्लिनिक को कॉल करें", "mr": "अडकला आहे, कृपया क्लिनिकला कॉल करा"},
}


def answer(intent: str, facts: dict, lang: str) -> tuple[str, str | None]:
    """facts are what the service read for this intent (see AssistantRepository)."""
    clinic = facts.get("clinic") or {}
    clinic_name = clinic.get("clinic_name") or {"en": "your clinic", "hi": "आपका क्लिनिक", "mr": "तुमचे क्लिनिक"}[lang]
    upcoming = facts.get("upcoming") or []

    if intent == "next_session":
        if not upcoming:
            return T["no_upcoming"][lang], "book"
        a = upcoming[0]
        status = _STATUS.get(a["status"], {}).get(lang, a["status"])
        return T["next_session"][lang].format(
            what=visit_label(a, lang), when=when(a, lang), clinic=clinic_name, status=status
        ), "appointment_detail"

    if intent == "today":
        today = facts["today"]
        todays = [a for a in upcoming if a["appointment_date"] == today]
        if todays:
            listed = ", ".join(f"{visit_label(a, lang)} {fmt_time(a.get('start_time'))}".strip() for a in todays)
            return T["today_yes"][lang].format(list=listed), "appointment_detail"
        if upcoming:
            return T["today_no"][lang].format(next=when(upcoming[0], lang)), "appointment_detail"
        return T["no_upcoming"][lang], "book"

    if intent == "sessions_left":
        counts = facts.get("session_counts") or {}
        if not (counts.get("done") or counts.get("left")):
            return T["no_sessions"][lang], None
        return T["sessions_left"][lang].format(done=counts.get("done", 0), left=counts.get("left", 0)), "sessions"

    if intent == "payment_due":
        due = [a for a in upcoming if a["status"] == "selected"]
        if not due:
            return T["nothing_due"][lang], None
        a = due[0]
        amount = fmt_money(a.get("amount_due"))
        return T["payment_due"][lang].format(what=visit_label(a, lang), when=when(a, lang), amount=f" ({amount})" if amount else ""), "pay"

    if intent == "refund_status":
        r = facts.get("refund")
        if not r:
            return T["no_refund"][lang], "payments"
        state = _REFUND_STATE.get(r.get("refund_status") or "pending", _REFUND_STATE["pending"])[lang]
        return T["refund"][lang].format(amount=fmt_money(r.get("refund_amount")), state=state), "payments"

    if intent == "total_paid":
        p = facts.get("paid") or {}
        return T["total_paid"][lang].format(amount=fmt_money(p.get("total") or 0), count=p.get("count") or 0), "payments"

    if intent == "last_session":
        last = facts.get("last")
        if not last:
            return T["no_last"][lang], None
        return T["last_session"][lang].format(what=visit_label(last, lang), when=when(last, lang)), None

    if intent == "my_doctor":
        if not facts.get("doctor_name"):
            return T["no_doctor"][lang], None
        return T["my_doctor"][lang].format(doctor=facts["doctor_name"]), "care_team"

    if intent == "clinic_phone":
        if not clinic.get("phone"):
            return T["no_clinic"][lang], None
        return T["clinic_phone"][lang].format(clinic=clinic_name, phone=clinic["phone"]), "call_clinic"

    if intent == "clinic_address":
        address = ", ".join(x for x in [clinic.get("address"), clinic.get("city")] if x)
        if not address:
            return T["no_clinic"][lang], None
        return T["clinic_address"][lang].format(clinic=clinic_name, address=address), "clinic_map"

    raise ValueError(f"unknown data intent {intent}")
