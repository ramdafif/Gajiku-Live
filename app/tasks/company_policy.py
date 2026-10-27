import json
from datetime import timedelta

import click

from app.db import get_db


def register_policy_verifier(app):
    @app.cli.command("verify-company-policy")
    @click.option("--at-date", required=True, type=click.DateTime(formats=["%Y-%m-%d"]), help="Tanggal pembanding WIB; jalankan pada clone staging sebelum mengubah kebijakan.")
    @click.option("--days", default=1, type=click.IntRange(1, 366), help="Jumlah hari berturut-turut, termasuk batas cutoff/bulan/tahun.")
    def verify_company_policy(at_date, days):
        from app.routes import web
        db = get_db()
        employees = db.execute("""SELECT u.id,u.gaji,p.id_pegawai,p.perusahaan_induk FROM users u
            JOIN pegawai p ON LOWER(p.email)=LOWER(u.email) ORDER BY u.id""").fetchall()
        legacy_resolver = web.get_weekly_cutoff_config
        def legacy(company):
            row = db.execute("""SELECT cutoff_hari FROM admins
                WHERE LOWER(TRIM(company))=LOWER(TRIM(?)) AND COALESCE(cutoff_mingguan_aktif,0)=1
                ORDER BY id DESC LIMIT 1""", (company,)).fetchone()
            if not row:
                return None
            day = int(row["cutoff_hari"] if row["cutoff_hari"] is not None else 2)
            return {"cutoff_hari": day if day in range(7) else 2}
        mismatches = []
        checks = 0
        for offset in range(days):
            day = at_date.date() + timedelta(days=offset)
            for employee in employees:
                try:
                    web.get_weekly_cutoff_config = legacy
                    before = web.compute_limits(int(employee["gaji"] or 0), day, employee["id"])
                finally:
                    web.get_weekly_cutoff_config = legacy_resolver
                after = web.compute_limits(int(employee["gaji"] or 0), day, employee["id"])
                checks += 1
                if before != after:
                    mismatches.append({"user_id": employee["id"], "date": day.isoformat(), "before": before, "after": after})
        report = {"passed": bool(employees) and not mismatches, "employees_with_accounts": len(employees), "checks": checks,
                  "at_date": at_date.date().isoformat(), "days": days, "mismatches": mismatches}
        click.echo(json.dumps(report, default=str))
        if not employees:
            raise click.ClickException("Tidak ada pegawai dengan akun; gate belum bisa dinyatakan lulus.")
        if mismatches:
            raise click.ClickException("Perhitungan sebelum/sesudah berbeda. Batalkan rilis dan periksa snapshot migrasi.")
