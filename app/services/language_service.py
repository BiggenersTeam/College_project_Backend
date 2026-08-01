"""
FaceTracer Language Service
===========================
Handles language detection, voice mapping, and multilingual TwiML
message generation for the AI Voice Calling Assistant.

Supported languages: EN (English), HI (Hindi), MR (Marathi)
Voice provider: Twilio Amazon Polly
"""
from typing import Optional


# ── Voice configuration per language ─────────────────────────────────────────
# NOTE: Using Twilio built-in "alice" voice (supports multiple languages)
# instead of Polly.* voices — Polly requires upgraded Twilio accounts.
# "alice" works on all Twilio plans including trial accounts.
VOICE_CONFIG = {
    "EN": {
        "voice":    "alice",
        "language": "en-US",
        "label":    "English",
    },
    "HI": {
        "voice":    "alice",
        "language": "hi-IN",
        "label":    "Hindi",
    },
    "MR": {
        "voice":    "alice",
        "language": "hi-IN",   # Marathi uses hi-IN locale (closest available)
        "label":    "Marathi",
    },
}

FALLBACK_LANG = "EN"
SUPPORTED_LANGS = set(VOICE_CONFIG.keys())


# ── Language normalisation ────────────────────────────────────────────────────
def normalize_language(raw: Optional[str]) -> str:
    """
    Normalize a language string to one of EN / HI / MR.
    Handles: None, empty, wrong case, partial strings.
    Always returns a valid key from VOICE_CONFIG.
    """
    if not raw:
        return FALLBACK_LANG
    upper = raw.strip().upper()
    # Direct match
    if upper in SUPPORTED_LANGS:
        return upper
    # Partial / alias mapping
    aliases = {
        "ENGLISH": "EN", "ENG": "EN",
        "HINDI": "HI", "HIN": "HI", "हिंदी": "HI", "हिन्दी": "HI",
        "MARATHI": "MR", "MAR": "MR", "MRT": "MR", "मराठी": "MR",
    }
    return aliases.get(upper, FALLBACK_LANG)


def get_voice_config(language: str) -> dict:
    """Return Twilio voice config dict for a given language code."""
    lang = normalize_language(language)
    return VOICE_CONFIG.get(lang, VOICE_CONFIG[FALLBACK_LANG])


# ── TwiML message templates ───────────────────────────────────────────────────
def build_twiml_message(
    person_name: str,
    case_id:     str,
    case_status: str,
    language:    str = "EN",
) -> str:
    """
    Build a complete TwiML XML response with natural AI voice message.

    Args:
        person_name: Name of the resolved missing person
        case_id:     MongoDB ObjectId string (last 8 chars used as reference)
        case_status: "Resolved" / "Found" / "Solved"
        language:    "EN" / "HI" / "MR"

    Returns:
        TwiML XML string ready to pass to Twilio calls.create(twiml=...)
    """
    lang   = normalize_language(language)
    cfg    = get_voice_config(lang)
    voice  = cfg["voice"]
    xlang  = cfg["language"]
    ref    = str(case_id)[-8:].upper()
    status_label = case_status.capitalize()

    if lang == "HI":
        intro = (
            f"नमस्ते. "
            f"मैं FaceTracer AI Assistant बोल रहा हूँ. "
            f"हमें आपको यह बताते हुए खुशी हो रही है कि "
            f"आपके द्वारा दर्ज किया गया गुमशुदा व्यक्ति, {person_name}, "
            f"सुरक्षित मिल गया है. "
            f"केस की स्थिति अब {status_label} है. "
            f"केस संदर्भ संख्या है {ref}. "
            f"कृपया अधिक जानकारी के लिए FaceTracer Portal में लॉगिन करें. "
            f"धन्यवाद."
        )
        outro = (
            f"यह संदेश FaceTracer Government Portal द्वारा स्वचालित रूप से उत्पन्न किया गया है. "
            f"संदर्भ संख्या {ref}. धन्यवाद."
        )

    elif lang == "MR":
        intro = (
            f"नमस्कार. "
            f"मी FaceTracer AI Assistant बोलत आहे. "
            f"आम्हाला तुम्हाला सांगताना आनंद होत आहे की "
            f"तुम्ही नोंदवलेली हरवलेली व्यक्ती, {person_name}, "
            f"सुरक्षितपणे सापडली आहे. "
            f"केसची सद्यस्थिती {status_label} आहे. "
            f"केस संदर्भ क्रमांक {ref} आहे. "
            f"कृपया अधिक माहितीसाठी FaceTracer Portal मध्ये लॉगिन करा. "
            f"धन्यवाद."
        )
        outro = (
            f"हा संदेश FaceTracer Government Portal द्वारे आपोआप तयार केला गेला आहे. "
            f"संदर्भ क्रमांक {ref}. धन्यवाद."
        )

    else:  # EN
        intro = (
            f"Hello. "
            f"This is FaceTracer AI Assistant. "
            f"We are happy to inform you that the missing person case you registered has been successfully resolved. "
            f"Please login to the FaceTracer Portal for complete details. "
            f"Thank you."
        )
        outro = (
            f"Case reference is {ref}. Thank you for using FaceTracer Portal."
        )

    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        f'    <Say voice="{voice}" language="{xlang}">{intro}</Say>\n'
        '    <Pause length="1"/>\n'
        f'    <Say voice="{voice}" language="{xlang}">{outro}</Say>\n'
        "</Response>"
    )
    return twiml
