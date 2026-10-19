import click
from flask import current_app

from app.db import get_db
from app.services.company_lifecycle import business_now, sync_clients, timestamp, visible_clients


def register_command(app):
    @app.cli.command("sync-company-lifecycle")
    def sync_company_lifecycle():
        db = get_db()
        started = business_now()
        try:
            clients = visible_clients(db, True, None)
            sync_clients(db, clients, started)
            value = timestamp(business_now())
            if app.config["DB_TYPE"] == "sqlite":
                db.execute("INSERT INTO app_settings (`key`,value) VALUES ('company_lifecycle_last_success',?) "
                           "ON CONFLICT(`key`) DO UPDATE SET value=excluded.value", (value,))
            else:
                db.execute("INSERT INTO app_settings (`key`,value) VALUES ('company_lifecycle_last_success',?) "
                           "ON DUPLICATE KEY UPDATE value=VALUES(value)", (value,))
            db.commit()
        except Exception as error:
            db.rollback()
            current_app.logger.exception("[COMPANY] sinkronisasi gagal")
            raise click.ClickException("Sinkronisasi perusahaan gagal; periksa log server.") from error
        current_app.logger.info("[COMPANY] sinkronisasi selesai: %s perusahaan", len(clients))
        click.echo(f"Sinkronisasi selesai: {len(clients)} perusahaan; waktu UTC {value}.")
