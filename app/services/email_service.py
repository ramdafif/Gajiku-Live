import ssl
from email.message import EmailMessage

from flask import current_app
import smtplib

from app.tasks.queue import enqueue, get_mode
from app.tasks.jobs import send_email_job

def send_email(subject: str, body: str, to_list=None, attachments=None):
    """Kirim email sederhana via SMTP SSL. Return True kalau sukses."""
    try:
        to_list = to_list or current_app.config.get("EMAIL_ADMIN_LIST")
        if not to_list:
            return False
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = current_app.config.get("EMAIL_FROM")
        msg["To"] = ", ".join(to_list)
        msg.set_content(body)
        for att in (attachments or []):
            filename = att.get("filename") or "attachment"
            content = att.get("content") or b""
            mimetype = att.get("mimetype") or "application/octet-stream"
            maintype, subtype = mimetype.split("/", 1) if "/" in mimetype else (mimetype, "octet-stream")
            if isinstance(content, str):
                content = content.encode("utf-8")
            msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

        context = ssl.create_default_context()
        
        # 🔄 Ambil port dan paksa konversi ke bentuk Integer (Angka)
        smtp_port = int(current_app.config.get("SMTP_PORT") or 465)
        smtp_host = current_app.config.get("SMTP_HOST")

        if smtp_port == 465:
            connection = smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=20)
        else:
            connection = smtplib.SMTP(smtp_host, smtp_port, timeout=20)
        with connection as smtp:
            if smtp_port != 465:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(current_app.config.get("SMTP_USER"), current_app.config.get("SMTP_PASS"))
            smtp.send_message(msg)
        return True
    except Exception as e:
        current_app.logger.warning(f"[EMAIL] gagal: {e}")
        return False


def enqueue_email(subject: str, body: str, to_list=None, to_addr: str | None = None, attachments=None):
    target_list = [to_addr] if to_addr else to_list
    if get_mode() == "rq":
        enqueue(send_email_job, subject, body, target_list, attachments)
    else:
        enqueue(send_email, subject, body, target_list, attachments)
