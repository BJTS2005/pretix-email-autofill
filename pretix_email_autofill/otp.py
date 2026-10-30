"""
One-time codes sent by email. Proving access to the mailbox is what keeps someone who only
knows a buyer's email address from seeing that buyer's personal data.

The code is never stored in clear text: only an HMAC of it lives in the buyer's (server-side)
cart session, bound to a random nonce and the email address.
"""
import hashlib
import logging
import secrets
import time

from django.core.cache import cache
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.translation import get_language, gettext as _

from pretix.helpers.http import get_client_ip

from .compat import mail_supports

logger = logging.getLogger(__name__)

CODE_TTL = 10 * 60          # seconds a code stays valid
MAX_TRIES_PER_CODE = 5
SESSION_SENDS = (3, 10 * 60)       # max codes per cart session per 10 minutes
EMAIL_SENDS = (5, 60 * 60)         # max codes per email address per hour
IP_SENDS = (20, 60 * 60)           # max codes per IP address per hour
EMAIL_VERIFIES = (10, 60 * 60)     # max wrong guesses per email address per hour

OK, INVALID, EXPIRED, LOCKED = "ok", "invalid", "expired", "locked"
_SALT = "pretix_email_autofill.otp"


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def _limit_reached(key, limit, window):
    """Counts one hit; True if more than ``limit`` hits happened inside ``window`` seconds."""
    try:
        if cache.add(key, 1, window):
            return False
        return cache.incr(key) > limit
    except ValueError:  # key vanished between add() and incr()
        cache.set(key, 1, window)
        return False
    except Exception:  # cache backend down: fail open, the per-session limits still apply
        logger.exception("Rate limiting cache unavailable")
        return False


def _hash(nonce, email, code):
    return salted_hmac(_SALT, f"{nonce}|{email.lower()}|{code}").hexdigest()


def issue_code(request, state, email, deliver):
    """
    Prepares a new code for ``email`` inside ``state`` (the cart-session dict of the plugin).
    ``deliver`` is False when the address has no previous orders: the buyer sees exactly the
    same screen, but no email is sent and no code can ever match, so the page does not reveal
    who bought before.
    Returns False if a rate limit was hit.
    """
    now = time.time()
    sends = [t for t in state.get("sends", []) if now - t < SESSION_SENDS[1]]
    if len(sends) >= SESSION_SENDS[0]:
        return False
    if _limit_reached(f"email_autofill:send:ip:{_digest(get_client_ip(request) or '-')}", *IP_SENDS):
        return False
    if _limit_reached(f"email_autofill:send:email:{_digest(email.lower())}", *EMAIL_SENDS):
        return False

    sends.append(now)
    code = f"{secrets.randbelow(10 ** 6):06d}"
    nonce = secrets.token_hex(16)
    state.update({
        "email": email,
        "sends": sends,
        "otp": {
            "nonce": nonce,
            "hash": _hash(nonce, email, code) if deliver else None,
            "expires": now + CODE_TTL,
            "tries": 0,
        },
    })
    if deliver:
        send_code_mail(request.event, email, code)
    return True


def verify_code(state, code):
    otp = state.get("otp")
    if not otp or time.time() > otp["expires"]:
        return EXPIRED
    if otp["tries"] >= MAX_TRIES_PER_CODE:
        return LOCKED
    otp["tries"] += 1
    if _limit_reached(f"email_autofill:verify:email:{_digest(state['email'].lower())}", *EMAIL_VERIFIES):
        return LOCKED
    if otp["hash"] and constant_time_compare(otp["hash"], _hash(otp["nonce"], state["email"], code)):
        return OK
    return LOCKED if otp["tries"] >= MAX_TRIES_PER_CODE else INVALID


def send_code_mail(event, email, code):
    from pretix.base.services.mail import mail

    kwargs = {}
    if mail_supports("sensitive"):  # pretix >= 2026.x: keep the code out of the mail log
        kwargs["sensitive"] = True
    try:
        mail(
            email,
            _("Tu código para {event_name}"),
            "pretix_email_autofill/email/code.txt",
            {"code": code, "event_name": str(event.name), "minutes": CODE_TTL // 60},
            event=event,
            locale=get_language(),
            **kwargs,
        )
    except Exception:
        # Never leak delivery problems to the page (that would reveal previous buyers).
        logger.exception("Could not send autofill code")
