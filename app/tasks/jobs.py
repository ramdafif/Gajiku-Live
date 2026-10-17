import os


def send_email_job(subject: str, body: str, to_list=None, attachments=None):
    os.environ["START_INLINE_WORKER"] = "0"
    os.environ["EXPORT_CSV_ENABLED"] = "0"
    from app import create_app
    from app.services.email_service import send_email

    app = create_app()
    with app.app_context():
        if not send_email(subject, body, to_list, attachments):
            raise RuntimeError("Pengiriman email gagal; periksa failed jobs dan konfigurasi SMTP.")
