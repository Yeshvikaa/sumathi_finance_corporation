import json, urllib.parse, urllib.request
from django.conf import settings

def send_sms(phone, message):
    """Returns True only if the provider accepted the message. Uses Fast2SMS (India)."""
    key = settings.FAST2SMS_API_KEY
    digits = "".join(c for c in phone if c.isdigit())[-10:]
    if not key or len(digits) != 10:
        return False
    data = urllib.parse.urlencode({"route": "q", "message": message, "numbers": digits}).encode()
    req = urllib.request.Request("https://www.fast2sms.com/dev/bulkV2", data=data,
                                 headers={"authorization": key, "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return bool(json.loads(r.read()).get("return"))
    except Exception:
        return False
