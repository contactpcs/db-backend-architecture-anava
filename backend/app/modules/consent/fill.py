"""Consent v3 (Documents/design_consent_v2.md, H1-H9): the placeholder
catalogue, the safety check for an uploaded full form, and filling a
template's summary / checkboxes / full HTML with the signer's details.

Pure functions — no DB. The service builds the values dict."""

from __future__ import annotations

import hashlib
import html
import json
import re
from html.parser import HTMLParser

# ─── Placeholder catalogue (H6) ──────────────────────────────────────────

COMMON = (
    "SIGNER_NAME",
    "CLINIC_NAME",
    "CLINIC_ADDRESS",
    "CLINIC_PHONE",
    "CONSENT_DATE",
    "SIGNED_AT",
    "CONSENT_ID",
    "CONSENT_VERSION",
    "SIGNATURE_BLOCK",
)
PATIENT = (
    "PATIENT_NAME",
    "PATIENT_DOB",
    "PATIENT_AGE",
    "PATIENT_GENDER",
    "PATIENT_MRN",
    "PATIENT_PHONE",
    "PATIENT_EMAIL",
    "PATIENT_ADDRESS",
    "GUARDIAN_NAME",
    "GUARDIAN_RELATION",
    "GUARDIAN_PHONE",
    "GUARDIAN_REASON",
)
TRANSFER = ("FROM_CLINIC", "TO_CLINIC")
STAFF = (
    "STAFF_NAME",
    "STAFF_ROLE",
    "ROLE",
    "STAFF_EMAIL",
    "STAFF_PHONE",
    "STAFF_DOB",
    "STAFF_GENDER",
    "DOCTOR_LICENSE_NO",
    "DOCTOR_SPECIALIZATION",
    "REGION_NAME",
)
CLINIC = ("PARTNER_CLINIC_NAME",)

# Change at signing time — kept as tokens when hashing (H8).
TIME_TOKENS = ("CONSENT_DATE", "SIGNED_AT")

# Who the signer is. Only in the full form, never in the summary, title or
# checkbox labels (H16): the summary page shows no personal details.
PERSONAL = frozenset(
    ("SIGNER_NAME", "STAFF_NAME", "STAFF_EMAIL", "STAFF_PHONE", "STAFF_DOB", "STAFF_GENDER", "DOCTOR_LICENSE_NO")
    + PATIENT  # patient + guardian details
)

DESCRIPTIONS = {
    "SIGNER_NAME": "Whoever accepts: the patient, their guardian, or the staff member",
    "CLINIC_NAME": "Clinic name",
    "CLINIC_ADDRESS": "Clinic address",
    "CLINIC_PHONE": "Clinic phone",
    "CONSENT_DATE": "Date of acceptance (IST)",
    "SIGNED_AT": "Date and time of acceptance (IST)",
    "CONSENT_ID": "Consent record ID",
    "CONSENT_VERSION": "Form version",
    "SIGNATURE_BLOCK": "Electronic acceptance record (full form only)",
    "PATIENT_NAME": "Patient full name",
    "PATIENT_DOB": "Patient date of birth",
    "PATIENT_AGE": "Patient age on the day of acceptance",
    "PATIENT_GENDER": "Patient gender",
    "PATIENT_MRN": "Patient medical record number",
    "PATIENT_PHONE": "Patient mobile",
    "PATIENT_EMAIL": "Patient email",
    "PATIENT_ADDRESS": "Patient address",
    "GUARDIAN_NAME": "Guardian name",
    "GUARDIAN_RELATION": "Guardian relation to the patient",
    "GUARDIAN_PHONE": "Guardian mobile",
    "GUARDIAN_REASON": "Why the guardian consents",
    "FROM_CLINIC": "Clinic the patient transfers from",
    "TO_CLINIC": "Clinic the patient transfers to",
    "STAFF_NAME": "Staff member full name",
    "STAFF_ROLE": "Staff role",
    "ROLE": "Staff role (same as STAFF_ROLE)",
    "STAFF_EMAIL": "Staff email",
    "STAFF_PHONE": "Staff mobile",
    "STAFF_DOB": "Staff date of birth",
    "STAFF_GENDER": "Staff gender",
    "DOCTOR_LICENSE_NO": "Doctor licence number",
    "DOCTOR_SPECIALIZATION": "Doctor specialisation",
    "REGION_NAME": "Region name",
    "PARTNER_CLINIC_NAME": "Partner clinic name",
}

GUARDIAN_REASON_LABELS = {
    "minor": "Patient is under 18",
    "lacks_capacity": "Patient lacks capacity to consent",
    "lawful_guardian": "Lawful guardian",
    "nominated_representative": "Nominated representative (Mental Healthcare Act, 2017)",
    "patient_requested": "Patient asked for a guardian's help",
}

MISSING = "—"
PLACEHOLDER_RE = re.compile(r"\[([A-Z][A-Z0-9_]{2,40})\]")
FULL_HTML_MAX_BYTES = 1_048_576


def allowed_placeholders(consent_type: str) -> tuple[str, ...]:
    if consent_type.startswith("patient_"):
        extra = PATIENT + (TRANSFER if consent_type in ("patient_clinic_transfer", "patient_relocation_transfer") else ())
    elif consent_type.startswith("staff_"):
        extra = STAFF
    else:
        extra = CLINIC
    return COMMON + extra


def check_placeholders(consent_type: str, *texts: str | None) -> list[str]:
    """Placeholders used in these texts (full form: also <input name=...>)
    that this consent type cannot fill."""
    allowed = set(allowed_placeholders(consent_type))
    used = {m for t in texts if t for m in PLACEHOLDER_RE.findall(bind_inputs(t))}
    return sorted(used - allowed)


def personal_placeholders(*texts: str | None) -> list[str]:
    return sorted({m for t in texts if t for m in PLACEHOLDER_RE.findall(t)} & PERSONAL)


# ─── Auto-filled input fields in the full form (H17) ─────────────────────
# <input name="PATIENT_NAME"> / <textarea name="patient_address"></textarea>
# become read-only fields carrying the placeholder, which _fill then fills
# (escaped) like any other. Only these tags are rewritten.

_INPUT_RE = re.compile(r"<input\b([^>]*)>", re.IGNORECASE)
_TEXTAREA_RE = re.compile(r"<textarea\b([^>]*)>(.*?)</textarea\s*>", re.IGNORECASE | re.DOTALL)
_NAME_ATTR_RE = re.compile(r"""\sname\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>/]+))""", re.IGNORECASE)
_DROP_ATTRS_RE = re.compile(r"""\s(?:value|readonly|disabled|type)(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'>/]+))?""", re.IGNORECASE)
INPUT_TYPES = {"", "text", "date", "datetime-local", "email", "tel", "number", "search"}


def _field_name(attrs: str) -> str | None:
    m = _NAME_ATTR_RE.search(attrs)
    return (m.group(1) or m.group(2) or m.group(3) or "").strip().upper() if m else None


def bind_inputs(doc: str) -> str:
    def input_tag(m: re.Match) -> str:
        attrs = m.group(1)
        name = _field_name(attrs)
        if not name:
            return m.group(0)
        attrs = _DROP_ATTRS_RE.sub("", attrs.rstrip().rstrip("/").rstrip())
        # type="text": a date input would show a formatted date as blank.
        return f'<input{attrs} type="text" value="[{name}]" readonly>'

    def textarea_tag(m: re.Match) -> str:
        name = _field_name(m.group(1))
        if not name:
            return m.group(0)
        return f"<textarea{_DROP_ATTRS_RE.sub('', m.group(1))} readonly>[{name}]</textarea>"

    return _TEXTAREA_RE.sub(textarea_tag, _INPUT_RE.sub(input_tag, doc))


# ─── Full-form safety check (H4) ─────────────────────────────────────────

_BLOCKED_TAGS = {
    "script",
    "iframe",
    "frame",
    "frameset",
    "object",
    "embed",
    "applet",
    "form",
    "button",
    "select",
    "base",
    "link",
    "portal",
}
# Attributes that LOAD something; only data: (or an in-page #fragment) allowed.
_LOADING_ATTRS = {"src", "srcset", "poster", "background", "xlink:href", "data", "lowsrc", "dynsrc"}
_BAD_SCHEME_RE = re.compile(r"^(javascript|vbscript|livescript):")
_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)\s*([^)'\"]*)", re.IGNORECASE)
_WS_RE = re.compile(r"[\s\x00-\x1f]+")


def _norm(value: str) -> str:
    # Browsers ignore whitespace/control characters inside a URL scheme
    # ("java\tscript:"); HTMLParser has already decoded entities.
    return _WS_RE.sub("", value).lower()


def _css_problems(css: str) -> list[str]:
    problems = []
    if re.search(r"@import", css, re.IGNORECASE):
        problems.append("CSS @import is not allowed (put the CSS in the file)")
    for _q, url in _CSS_URL_RE.findall(css):
        u = _norm(url)
        if u and not (u.startswith("data:") or u.startswith("#")):
            problems.append(f"CSS url() must be an embedded data: URI, not {url.strip()[:80]!r}")
    if re.search(r"expression\s*\(|behavior\s*:|-moz-binding", css, re.IGNORECASE):
        problems.append("Scripted CSS (expression/behavior/-moz-binding) is not allowed")
    return problems


class _Checker(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []
        self._in_style = False

    def _tag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCKED_TAGS:
            self.problems.append(f"<{tag}> is not allowed")
        if tag in ("input", "textarea"):
            a = {k: (v or "") for k, v in attrs}
            field = a.get("name", "").strip().upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,40}", field):
                self.problems.append(
                    f'<{tag}> needs name="..." set to a placeholder, e.g. name="PATIENT_NAME" (it is filled automatically)'
                )
            elif field == "SIGNATURE_BLOCK":
                self.problems.append("[SIGNATURE_BLOCK] cannot be an input field — put it in the text")
            if tag == "input" and a.get("type", "").lower() not in INPUT_TYPES:
                self.problems.append(f'<input type="{a.get("type")}"> is not allowed — only text-style fields, filled automatically')
        for name, value in attrs:
            value = value or ""
            v = _norm(value)
            if name.startswith("on"):
                self.problems.append(f"Event handler {name}= on <{tag}> is not allowed")
            elif _BAD_SCHEME_RE.match(v):
                self.problems.append(f"Script URL in {name}= on <{tag}> is not allowed")
            elif name in _LOADING_ATTRS and v and not (v.startswith("data:") or v.startswith("#")):
                self.problems.append(f"<{tag} {name}=...> must be an embedded data: URI, not {value[:80]!r}")
            elif name == "href" and tag in ("use", "image", "feimage") and v and not (v.startswith("data:") or v.startswith("#")):
                self.problems.append(f"<{tag} href=...> must be an embedded data: URI or #fragment")
            elif name == "style":
                self.problems.extend(_css_problems(value))
            elif tag == "meta" and name == "http-equiv":
                self.problems.append("<meta http-equiv> is not allowed")

    def handle_starttag(self, tag, attrs):
        self._tag(tag, attrs)
        self._in_style = tag == "style"

    def handle_startendtag(self, tag, attrs):
        self._tag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False

    def handle_data(self, data):
        if self._in_style:
            self.problems.extend(_css_problems(data))


def check_full_html(full_html: str) -> list[str]:
    """Reasons this full form cannot be stored (empty = OK). The HTML is never
    rewritten (H3), so anything unsafe is rejected instead of stripped. The
    sandboxed iframe and the network-less PDF renderer are the second line."""
    if len(full_html.encode("utf-8")) > FULL_HTML_MAX_BYTES:
        return [f"The full form is larger than {FULL_HTML_MAX_BYTES // 1024} KB — embed smaller images"]
    checker = _Checker()
    checker.feed(full_html)
    checker.close()
    return list(dict.fromkeys(checker.problems))  # unique, in order


# ─── Filling (H6-H9) ─────────────────────────────────────────────────────


def _fill(text: str, values: dict[str, str], *, escape: bool, raw: dict[str, str] | None = None) -> str:
    """Replaces catalogue placeholders; one with no value shows MISSING. While
    hashing, time tokens and [SIGNATURE_BLOCK] (absent from `values`) stay
    tokens. Non-catalogue tokens in legacy published text stay literal."""
    known = set(DESCRIPTIONS)

    def sub(m: re.Match) -> str:
        name = m.group(1)
        if raw and name in raw:
            return raw[name]
        if name in values:
            value = values[name] or MISSING
        elif name in TIME_TOKENS or name == "SIGNATURE_BLOCK":
            return m.group(0)
        else:
            value = MISSING
        return html.escape(value) if escape else value

    # Full form (escape=True): a name Anava cannot fill (e.g. <input
    # name="VERIFIED_PHONE">) is allowed and shows as blank (H19). Summary
    # text keeps unknown tokens literal (legacy published wording).
    return PLACEHOLDER_RE.sub(lambda m: sub(m) if m.group(1) in known or escape else m.group(0), text)


def _show_if_style(signer_type: str) -> str:
    # The base target makes links open a new tab instead of navigating the
    # sandboxed frame away from the form (uploads may not contain <base>).
    hidden = "self" if signer_type == "guardian" else "guardian"
    return f'<base target="_blank"><style>[data-show-if="{hidden}"]{{display:none!important}}</style>'


def _inject_style(doc: str, style: str) -> str:
    """H7 by CSS, so the uploaded bytes are never restructured: the style goes
    right after <head> (or <html>/doctype) so no doctype ends up after it."""
    for pattern in (r"<head\b[^>]*>", r"<html\b[^>]*>", r"<!doctype[^>]*>"):
        m = re.search(pattern, doc, re.IGNORECASE)
        if m:
            return doc[: m.end()] + style + doc[m.end() :]
    return style + doc


def signature_block(evidence: dict | None) -> str:
    """[SIGNATURE_BLOCK] in the full form. evidence None = not signed yet."""
    if evidence is None:
        return (
            '<div class="anava-signature" style="border:1px dashed #94a3b8;padding:10px 12px;margin:12px 0;'
            'font-size:13px;color:#475569">Your electronic acceptance will be recorded here when you press '
            "<strong>I agree</strong>.</div>"
        )
    e = {k: html.escape(str(v)) if v not in (None, "") else MISSING for k, v in evidence.items()}
    on_behalf = f" ({e['relation']}, on behalf of {e['on_behalf_of']})" if evidence.get("signer_type") == "guardian" else ""
    return (
        '<div class="anava-signature" style="border:1px solid #334155;padding:10px 12px;margin:12px 0;font-size:13px">'
        f"<div><strong>Accepted electronically by {e['name']}</strong>{on_behalf}</div>"
        f"<div>On {e['signed_at']} · IP address {e['ip_address']}</div>"
        f"<div>Consent ID {e['consent_id']} · Text hash {e['rendered_hash']}</div>"
        "</div>"
    )


def render(
    template: dict,
    values: dict[str, str],
    signer_type: str,
    *,
    times: dict[str, str] | None,
    signature: str | None = None,
) -> dict:
    """{title, content, clauses[, full_html]} filled for this signer.
    times None = hashing (time tokens and [SIGNATURE_BLOCK] stay tokens)."""
    vals = {**values, **(times or {})}
    out = {
        "title": _fill(template["title"], vals, escape=False),
        "content": _fill(template["content"], vals, escape=False),
        "clauses": [
            {"code": c["code"], "label": _fill(c["label"], vals, escape=False), "required": bool(c.get("required", True))}
            for c in template["clauses"]
        ],
    }
    if template.get("full_html"):
        raw = {"SIGNATURE_BLOCK": signature} if signature is not None else None
        out["full_html"] = _inject_style(_fill(bind_inputs(template["full_html"]), vals, escape=True, raw=raw), _show_if_style(signer_type))
    return out


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_rendered(rendered: dict) -> str:
    canonical = json.dumps(rendered, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_hex(canonical)


def used_values(template: dict, values: dict[str, str]) -> dict[str, str]:
    """Only the values this template shows (data minimisation) — the
    fill_values snapshot stored at signing. The subject's name is always kept:
    the signature block names whom a guardian signs for."""
    texts = [template["title"], template["content"], bind_inputs(template.get("full_html") or "")] + [
        c["label"] for c in template["clauses"]
    ]
    used = {m for t in texts for m in PLACEHOLDER_RE.findall(t)} | {"PATIENT_NAME", "STAFF_NAME"}
    return {k: v for k, v in values.items() if k in used and k not in TIME_TOKENS and k != "SIGNATURE_BLOCK"}


# ─── What a full form must / should contain, per consent type (H18) ──────

_TRANSFER_TYPES = ("patient_clinic_transfer", "patient_relocation_transfer")


def required_placeholders(consent_type: str, role: str | None = None) -> tuple[str, ...]:
    """Publishing a full form without these is refused: who the consent is
    for, when it was accepted, and the acceptance itself."""
    if consent_type.startswith("patient_"):
        # Any patient may be a minor, so every patient form needs a guardian
        # part (wrapped in data-show-if="guardian") naming the guardian and
        # their relationship to the patient: the proof of their authority to
        # consent (Compliance Policy §3.4, DPDP s.9).
        fields = ("PATIENT_NAME", "PATIENT_DOB", "GUARDIAN_NAME", "GUARDIAN_RELATION") + (
            TRANSFER if consent_type in _TRANSFER_TYPES else ()
        )
    elif consent_type.startswith("staff_"):
        fields = ("STAFF_NAME", "STAFF_ROLE") + (("DOCTOR_LICENSE_NO",) if role == "doctor" else ())
    else:
        fields = ("PARTNER_CLINIC_NAME", "SIGNER_NAME")
    return fields + ("CONSENT_DATE", "SIGNATURE_BLOCK")


def recommended_placeholders(consent_type: str, role: str | None = None) -> tuple[str, ...]:
    if consent_type.startswith("patient_"):
        fields = (
            "PATIENT_AGE",
            "PATIENT_GENDER",
            "PATIENT_MRN",
            "PATIENT_PHONE",
            "PATIENT_ADDRESS",
            "GUARDIAN_PHONE",
            "GUARDIAN_REASON",
        )
    elif consent_type.startswith("staff_"):
        fields = ("STAFF_EMAIL", "STAFF_PHONE", "REGION_NAME") + (("DOCTOR_SPECIALIZATION",) if role == "doctor" else ())
    else:
        fields = ()
    return fields + ("CLINIC_NAME", "CLINIC_ADDRESS", "CONSENT_ID", "CONSENT_VERSION")


def form_fields(full_html: str) -> tuple[set[str], set[str]]:
    """(names used as input fields, names used as [TEXT]) in an uploaded form.
    HTML comments are notes for the author, not fields."""
    full_html = re.sub(r"<!--.*?-->", "", full_html, flags=re.DOTALL)
    inputs = {_field_name(m.group(1)) for m in _INPUT_RE.finditer(full_html)}
    inputs |= {_field_name(m.group(1)) for m in _TEXTAREA_RE.finditer(full_html)}
    inputs.discard(None)
    inputs.discard("")
    return inputs, set(PLACEHOLDER_RE.findall(full_html))  # type: ignore[return-value]


def analyze_full_html(consent_type: str, role: str | None, full_html: str) -> dict:
    """What the admin sees before saving/publishing: every field the form will
    capture, what is missing, and anything that blocks it."""
    inputs, text = form_fields(full_html)
    used = inputs | text
    have = used | ({"STAFF_ROLE"} if "ROLE" in used else set())  # [ROLE] = [STAFF_ROLE]
    allowed = set(allowed_placeholders(consent_type))
    required = required_placeholders(consent_type, role)
    recommended = recommended_placeholders(consent_type, role)
    return {
        "fields": [
            {
                "name": n,
                "description": DESCRIPTIONS.get(n, "Not an Anava field — it will show blank (—)"),
                "as_input": n in inputs,
                "as_text": n in text,
                "known": n in allowed,
                "required": n in required,
            }
            for n in sorted(used)
        ],
        "missing_required": [n for n in required if n not in have],
        "missing_recommended": [n for n in recommended if n not in have],
        "unknown": sorted(used - allowed),
        "problems": check_full_html(full_html),
        "has_guardian_section": bool(re.search(r"""data-show-if\s*=\s*["']?guardian""", full_html, re.IGNORECASE)),
    }
