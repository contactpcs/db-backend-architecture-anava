"""Fixed safety screens (Ask ANAVA design brief, "Safety wording").

Checked before anything else, on chat messages and feedback comments. Pure
keyword rules: never waits for, or depends on, the AI provider. Nothing here
notifies staff (out of scope), so every message tells the patient exactly what
to do and which number to call.

Hindi and Marathi wording must be reviewed by a native speaker and every
helpline number verified before launch (brief, "Decisions needed").
"""

from __future__ import annotations

import re

# Priority: the first screen whose words appear wins.
_ORDER = ("S0", "S1", "S4", "S2", "S3")

# Latin-script words are matched on word boundaries ("die" must not hit "diet").
# Devanagari phrases are matched as substrings.
_TRIGGERS: dict[str, dict[str, list[str]]] = {
    "S0": {
        "latin": [
            r"chest pain",
            r"can'?t breathe",
            r"cannot breathe",
            r"not breathing",
            r"unconscious",
            r"fainted",
            r"having a (fit|seizure)",
            r"seizure now",
            r"heavy bleeding",
            r"bleeding a lot",
            r"heart attack",
        ],
        "deva": ["सीने में दर्द", "छाती में दर्द", "सांस नहीं", "साँस नहीं", "बेहोश", "दौरा पड़", "छातीत दुख", "श्वास घेता येत नाही", "बेशुद्ध", "झटका आला"],
    },
    "S1": {
        "latin": [
            r"suicid\w*",
            r"kill myself",
            r"end my life",
            r"end it all",
            r"want to die",
            r"wanna die",
            r"better off dead",
            r"self[- ]?harm",
            r"hurt myself",
            r"cut myself",
            r"no reason to live",
            r"don'?t want to live",
        ],
        "deva": [
            "आत्महत्या",
            "मरना चाहता",
            "मरना चाहती",
            "मर जाना चाहता",
            "मर जाना चाहती",
            "जीना नहीं चाहता",
            "जीना नहीं चाहती",
            "ज़िंदगी खत्म",
            "जिंदगी खत्म",
            "खुद को नुकसान",
            "मरायचं आहे",
            "मरायचे आहे",
            "जगायचं नाही",
            "जगायचे नाही",
            "आयुष्य संपवायचं",
            "स्वतःला इजा",
        ],
    },
    "S4": {
        "latin": [
            r"(someone|he|she|they|husband|wife|father|mother) (is )?(hurting|beating|hitting) me",
            r"being abused",
            r"threaten(ed|ing) me",
        ],
        "deva": ["मुझे मारते", "मुझे मारता", "मुझे पीटते", "मला मारतात", "मला मारतो", "मला धमकी"],
    },
    "S2": {
        "latin": [
            r"burn(t|ed)? (on|my) (head|scalp|skin)",
            r"blister",
            r"severe headache",
            r"can'?t stop vomiting",
            r"wound on (my )?(head|scalp)",
        ],
        "deva": ["छाला", "तेज़ सिरदर्द", "तेज सिरदर्द", "जल गया", "फोड आला", "भाजलं", "खूप डोकेदुखी"],
    },
    "S3": {
        "latin": [
            r"very sad",
            r"feel(ing)? hopeless",
            r"so depressed",
            r"feel(ing)? (very )?low",
            r"(so|very) lonely",
            r"crying a lot",
            r"feel(ing)? empty",
        ],
        "deva": ["बहुत उदास", "अकेला महसूस", "अकेली महसूस", "निराश", "खूप उदास", "एकटं वाटतं", "एकटे वाटते"],
    },
}

_COMPILED = {screen: (re.compile(r"\b(" + "|".join(t["latin"]) + r")\b", re.IGNORECASE), t["deva"]) for screen, t in _TRIGGERS.items()}

MESSAGES: dict[str, dict[str, str]] = {
    "S0": {
        "en": "This sounds like an emergency. Please call 108 (ambulance) or 112 now. If someone is with you, ask them to help.",
        "hi": "यह आपातकाल लगता है। कृपया अभी 108 (एम्बुलेंस) या 112 पर कॉल करें। अगर कोई आपके साथ है, तो उनसे मदद मांगें।",
        "mr": "ही आपत्कालीन स्थिती वाटते. कृपया आत्ता 108 (रुग्णवाहिका) किंवा 112 वर कॉल करा. तुमच्यासोबत कोणी असेल तर त्यांची मदत घ्या.",
    },
    "S1": {
        "en": "I'm really sorry you're feeling this way. You don't have to go through this alone. Please talk to someone now: "
        "call Tele-MANAS on 14416 (free, 24 hours, many languages), or 112 if you are in danger. Reach out to someone you trust "
        "and stay with them. Your doctor at ANAVA can also help: {clinic_phone}.",
        "hi": "मुझे बहुत दुख है कि आप ऐसा महसूस कर रहे हैं। आप अकेले नहीं हैं। कृपया अभी किसी से बात करें: Tele-MANAS 14416 "
        "(मुफ़्त, 24 घंटे) पर कॉल करें, या खतरे में हों तो 112 पर। किसी भरोसेमंद व्यक्ति के पास रहें। आपके ANAVA डॉक्टर भी मदद कर सकते हैं: {clinic_phone}।",
        "mr": "तुम्हाला असं वाटतंय याचं मला खूप वाईट वाटतं. तुम्ही एकटे नाही. कृपया आत्ता कोणाशी तरी बोला: Tele-MANAS 14416 "
        "(मोफत, 24 तास) वर कॉल करा, किंवा धोका असल्यास 112 वर. विश्वासू व्यक्तीसोबत राहा. तुमचे ANAVA डॉक्टरही मदत करू शकतात: {clinic_phone}.",
    },
    "S2": {
        "en": "Please call your clinic now at {clinic_phone} and tell them what you're feeling. Don't put anything on your skin "
        "until you've spoken to them. If you feel very unwell, call 108.",
        "hi": "कृपया अभी अपने क्लिनिक को {clinic_phone} पर कॉल करें और बताएं कि आप क्या महसूस कर रहे हैं। उनसे बात करने से पहले त्वचा पर कुछ न लगाएं। "
        "बहुत अस्वस्थ लगे तो 108 पर कॉल करें।",
        "mr": "कृपया आत्ता तुमच्या क्लिनिकला {clinic_phone} वर कॉल करा आणि तुम्हाला काय जाणवतंय ते सांगा. त्यांच्याशी बोलेपर्यंत त्वचेवर काहीही लावू नका. "
        "खूप अस्वस्थ वाटल्यास 108 वर कॉल करा.",
    },
    "S3": {
        "en": "I'm sorry you're feeling low. It may help to talk to someone you trust. Tele-MANAS (14416) is free and open "
        "24 hours if you'd like to talk to a counsellor. Please also tell your doctor at your next visit.",
        "hi": "मुझे दुख है कि आप उदास महसूस कर रहे हैं। किसी भरोसेमंद व्यक्ति से बात करना मदद कर सकता है। Tele-MANAS (14416) मुफ़्त है और 24 घंटे खुला है। "
        "अपनी अगली विज़िट में डॉक्टर को भी बताएं।",
        "mr": "तुम्हाला उदास वाटतंय याचं मला वाईट वाटतं. विश्वासू व्यक्तीशी बोलल्याने मदत होऊ शकते. Tele-MANAS (14416) मोफत आणि 24 तास सुरू असते. "
        "पुढच्या भेटीत डॉक्टरांनाही सांगा.",
    },
    "S4": {
        "en": "Your safety matters. If you are in danger now, call 112. Women Helpline: 181. You can also speak to someone you trust.",
        "hi": "आपकी सुरक्षा ज़रूरी है। अगर आप अभी खतरे में हैं, तो 112 पर कॉल करें। महिला हेल्पलाइन: 181।",
        "mr": "तुमची सुरक्षितता महत्त्वाची आहे. तुम्ही आत्ता धोक्यात असाल तर 112 वर कॉल करा. महिला हेल्पलाइन: 181.",
    },
}


def detect(text: str) -> str | None:
    """The safety screen this text calls for, or None."""
    if not text:
        return None
    for screen in _ORDER:
        latin, deva = _COMPILED[screen]
        if latin.search(text) or any(phrase in text for phrase in deva):
            return screen
    return None


def message(screen: str, language: str, clinic_phone: str | None) -> str:
    text = MESSAGES[screen].get(language) or MESSAGES[screen]["en"]
    return text.replace("{clinic_phone}", clinic_phone or "your clinic")
