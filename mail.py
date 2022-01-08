from os import path

from smtplib import SMTP_SSL as SMTP
from email.mime.text import MIMEText

SMTPserver = "smtp.gmail.com"
sender = "Blender Butler"

def send_email(subject, content, username, password):
    dir = path.dirname(__file__)
    p = path.join(dir, "mail.html")

    with open(p) as f:
        html = f.read().replace("$CONTENT", content, 1)

    msg = MIMEText(html, "html")
    msg["Subject"] = subject
    msg["From"] = sender

    address = username + "@gmail.com"

    conn = SMTP(SMTPserver)
    conn.set_debuglevel(False)
    conn.login(username, password)
    try:
        conn.sendmail(address, [address], msg.as_string())
        print("sned")
    finally:
        conn.quit()
