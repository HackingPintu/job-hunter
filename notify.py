"""Optional alerts for the CLI. Configure with environment variables, never commit secrets."""
import os
import smtplib
from email.message import EmailMessage

import requests


def _summary(result, limit=10):
    jobs = [j for j in result["jobs"] if j["is_new"] or j["hot"]][:limit]
    lines = [f"JobHunter: {result['stats']['matches']} matches, {result['stats']['new']} new, {result['stats']['hot']} apply-now"]
    for j in jobs:
        lines.append(f"[{j['score']}] {j['company']} - {j['title']} ({j['location']})\n{j['url']}")
    return "\n\n".join(lines)


def send_telegram(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                  data={"chat_id": chat, "text": text[:4000], "disable_web_page_preview": "true"}, timeout=20)
    return True


def send_email(subject, text):
    host, to = os.getenv("SMTP_HOST"), os.getenv("NOTIFY_EMAIL")
    if not (host and to):
        return False
    user, pw = os.getenv("SMTP_USER"), os.getenv("SMTP_PASS")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user or to, to
    msg.set_content(text)
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=30) as s:
        s.starttls()
        if user and pw:
            s.login(user, pw)
        s.send_message(msg)
    return True


def notify(result):
    """Send an alert only if there is something new or apply-now."""
    if not (result["stats"]["new"] or result["stats"]["hot"]):
        return []
    text = _summary(result)
    sent = []
    for name, fn in (("telegram", lambda: send_telegram(text)),
                     ("email", lambda: send_email("JobHunter: new matches", text))):
        try:
            if fn():
                sent.append(name)
        except Exception as e:
            print(f"  {name} alert failed: {e}")
    return sent
