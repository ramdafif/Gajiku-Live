# Migrasi Redesign ke VPS setelah git pull

Runbook ini memakai maintenance singkat, BUKAN zero-downtime. Untuk blue-green,
ikuti `deploy.md`. Folder Live lokal dan DB production tidak disentuh selama tes.
Catatan deployment lama menyebut `/home/gajiku/app/gajiku`, `gajiku.service`,
port 5000, dan MySQL `gajiku_db`; sesi ini tidak mengakses VPS untuk membuktikan
bahwa jalur, service, port, atau versi tersebut masih sama.

## 1. Preflight release, ENV, dan venv

Semua file baru harus sudah di-commit dan dipush dari Redesign SEBELUM `git pull`.
Untracked lokal tidak ikut terpasang. Jangan mengirim `.env`, dump DB, atau data
laptop. Update berikutnya: maintenance sebelum pull jika direktori sedang melayani
production, karena template/static dapat berubah sebelum worker di-restart.

```sh
sudo systemctl show gajiku.service -p User -p Group -p WorkingDirectory -p ExecStart -p EnvironmentFiles
timedatectl status
```

Sesuaikan SEMUA variabel berikut dari konfigurasi sebenarnya. `venv` dan `.env`
adalah contoh: bisa `.venv` atau EnvironmentFile external pada VPS. Jangan lanjut
jika tidak cocok. Akun DB harus memiliki izin backup/DDL, bukan asal memakai akun
aplikasi yang mungkin tidak mempunyai izin migrasi.

```sh
set -euo pipefail
APP_DIR=/home/gajiku/app/gajiku
SERVICE=gajiku.service
PYTHON="$APP_DIR/venv/bin/python"
ENV_FILE="$APP_DIR/.env"
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=gajiku_db
DB_ADMIN=ISI_USER_DB_YANG_PUNYA_HAK_MIGRASI

cd "$APP_DIR"
test -x "$PYTHON"
test -f "$ENV_FILE"
test "$DB_ADMIN" != ISI_USER_DB_YANG_PUNYA_HAK_MIGRASI
git status --short
git rev-parse HEAD
test -f app/routes/notifications.py
test -f app/services/company_lifecycle.py
test -f app/tasks/company_lifecycle.py
test -f static/theme.js
test -f migrasidb/migrasi_company_lifecycle.sql
test -f deploy/gajiku-company-lifecycle.service
```

Worktree VPS yang berisi perubahan harus ditangani dulu; jangan `reset --hard`
atau menghapus upload. Pertahankan ENV production, terutama `MYSQL_*`, `SMTP_*`,
`APP_SECRET` kuat dan tetap, serta domain HTTPS `PUBLIC_BASE_URL`. Pastikan:

```dotenv
DB_TYPE=mysql
FLASK_DEBUG=0
SESSION_COOKIE_SECURE=1
ALLOW_DATE_OVERRIDE=0
ALLOW_DESTRUCTIVE_RESET=0
ENABLE_TEST_ENDPOINTS=0
SEED_DEMO_DATA=0
```

Jangan mengganti APP_SECRET kuat setiap deploy. Bila migrasi dari secret lama yang
tidak aman, penggantian memang mengharuskan pengguna login ulang. Pastikan jam/NTP
server benar. Worker email/export existing tetap dibutuhkan; runner penagihan tidak
menggantikan RQ atau tugas export tersebut.

## 2. Maintenance dan backup

Selesaikan/drain pekerjaan berjalan, kemudian hentikan web dan worker/scheduler
existing yang menulis DB selama backup/migrasi. Gunakan nama unit worker yang
sebenarnya, jangan membunuh semua proses Python/Redis. Web yang dihentikan bisa
membuat Nginx menampilkan 502 jika belum ada halaman maintenance.

```sh
sudo systemctl stop "$SERVICE"
if systemctl is-active --quiet gajiku-company-lifecycle.timer; then
  sudo systemctl stop gajiku-company-lifecycle.timer
fi
if systemctl is-active --quiet gajiku-company-lifecycle.service; then
  sudo systemctl stop gajiku-company-lifecycle.service
fi

umask 077
BACKUP_DIR="$HOME/gajiku-backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
sudo cp -p "$ENV_FILE" "$BACKUP_DIR/application.env"
sudo systemctl cat "$SERVICE" > "$BACKUP_DIR/web-service.txt"
git rev-parse HEAD > "$BACKUP_DIR/release-after-pull.txt"
"$PYTHON" -m pip freeze > "$BACKUP_DIR/requirements-before.txt"
if test -d static/uploads; then
  tar -czf "$BACKUP_DIR/uploads.tar.gz" static/uploads
fi

mysqldump --single-transaction --routines --events --triggers --no-tablespaces \
  -h "$DB_HOST" -P "$DB_PORT" -u "$DB_ADMIN" -p "$DB_NAME" \
  > "$BACKUP_DIR/database.sql"
test -s "$BACKUP_DIR/database.sql"
```

Password diminta interaktif; jangan memakai `-pPASSWORD` atau menaruhnya di history.
`--single-transaction` menjaga konsistensi tabel InnoDB; verifikasi engine tabel dan
jangan menjalankan DDL lain selama dump. `test -s` HANYA memeriksa file tidak kosong.
Verifikasi restore ke database STAGING TERPISAH sebelum migrasi production. Jangan
restore dump ke `gajiku_db` sebagai tes. Simpan backup di luar direktori web.

## 3. Dependency dan tiga migrasi

Jalankan pip dengan user pemilik venv, bukan `sudo pip` global. Tidak ada build npm.
Migrasi katalog pertama bisa memperbarui mapping `pegawai`; validasi nilai
`perusahaan_induk` dan project terlebih dahulu, jangan mengisi induk kosong asal.

```sh
"$PYTHON" -m pip install -r requirements.txt
"$PYTHON" -m pip check

mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_ADMIN" -p "$DB_NAME" < migrasidb/migrasi_kelola_client.sql
mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_ADMIN" -p "$DB_NAME" < migrasidb/migrasi_security_hardening.sql
mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_ADMIN" -p "$DB_NAME" < migrasidb/migrasi_company_lifecycle.sql

mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_ADMIN" -p "$DB_NAME" <<'SQL'
SHOW TABLES;
SELECT value FROM app_settings WHERE `key`='clients_catalog_v1';
SELECT COUNT(*) AS induk_kosong FROM pegawai
WHERE perusahaan_induk IS NULL OR TRIM(perusahaan_induk)='';
SQL
```

Ketiganya dapat diulang; backfill katalog memakai marker. Harus muncul tabel
`client_billing`, `client_bills`, `client_mou_actions`, `notifications`, dan
`notification_reads`. Nomor/periode MoU memakai kolom `clients` existing.
Lifecycle SQL tidak mengubah status pribadi pegawai; perusahaan belum memiliki
jadwal sampai superadmin menyetelnya, jadi tidak langsung diberi tunggakan lama.

Startup juga mengecek skema, tetapi restart pertama bukan pengganti backup/migrasi
terencana. DDL MySQL tidak bisa dianggap rollback satu transaksi. Jangan menjalankan
`app/migrasi_local.py`, reset data, atau mengimpor SQLite/dump laptop ke production.
Jika perintah gagal, berhenti dan periksa error; jangan memakai `mysql --force`.

## 4. Pasang runner satu menit

```sh
sudo install -m 0644 deploy/gajiku-company-lifecycle.service /etc/systemd/system/gajiku-company-lifecycle.service
sudo install -m 0644 deploy/gajiku-company-lifecycle.timer /etc/systemd/system/gajiku-company-lifecycle.timer
sudoedit /etc/systemd/system/gajiku-company-lifecycle.service
```

Sesuaikan User, Group, WorkingDirectory, EnvironmentFile, dan path Python pada
ExecStart dengan service production. Contoh nilai DI DALAM unit yang sudah disalin:

```ini
User=gajiku
Group=gajiku
WorkingDirectory=/home/gajiku/app/gajiku
EnvironmentFile=/home/gajiku/app/gajiku/.env
ExecStart=/usr/bin/env START_INLINE_WORKER=0 EXPORT_CSV_ENABLED=0 /home/gajiku/app/gajiku/venv/bin/python -m flask --app wsgi:app sync-company-lifecycle
```

Itu bukan unit lengkap pengganti. Pertahankan `[Unit]`, `[Service]`, hardening,
dan prefix `/usr/bin/env`: EnvironmentFile dapat menimpa Environment pada unit.
Jangan menaruh password di unit; batasi izin ENV sesuai user service. Systemd
memuat EnvironmentFile, sedangkan CLI langsung dari shell belum tentu memakai
ENV external yang sama.

```sh
sudo systemd-analyze verify /etc/systemd/system/gajiku-company-lifecycle.service /etc/systemd/system/gajiku-company-lifecycle.timer
sudo systemctl daemon-reload
sudo systemctl start gajiku-company-lifecycle.service
sudo systemctl show gajiku-company-lifecycle.service -p Result -p ExecMainStatus
sudo journalctl -u gajiku-company-lifecycle.service -n 50 --no-pager
```

Harapkan `Result=success`, `ExecMainStatus=0`, dan log sinkronisasi selesai.
Service oneshot boleh kembali `inactive (dead)` setelah sukses. Jika gagal,
jangan mengaktifkan timer atau mengatur tagihan pelanggan sebelum diperbaiki.

## 5. Nyalakan web, timer, dan verifikasi

```sh
sudo systemctl start "$SERVICE"
sudo systemctl status "$SERVICE" --no-pager
curl -fsS http://127.0.0.1:5000/ping
curl -fsS http://127.0.0.1:5000/health/db
sudo systemctl enable --now gajiku-company-lifecycle.timer
systemctl list-timers gajiku-company-lifecycle.timer --all
sudo journalctl -u "$SERVICE" -n 80 --no-pager
sudo journalctl -u gajiku-company-lifecycle.service --since '5 minutes ago' --no-pager
```

Ganti port 5000 bila bind service berbeda. Hidupkan lagi worker/email existing
yang tadi dihentikan. Kode/static ini tidak memerlukan reload Nginx jika konfigurasinya
tidak berubah. Jika Nginx diubah, `sudo nginx -t` harus lolos sebelum reload.

Periksa melalui domain HTTPS: login setiap role, riwayat default bulan ini, filter
manual, export, lonceng, tab status baca, MoU, dan dashboard perusahaan. Uji alur
tagihan/aktivasi di staging, jangan memajukan waktu server atau membuat pelanggan
sengaja menunggak untuk smoke test. Setelah verifikasi, superadmin baru menyetel
tanggal tagihan perusahaan satu per satu. Jangan menyalakan ALLOW_DATE_OVERRIDE
di production untuk mempercepat pengujian.

## Jika ada error

- Hentikan proses rilis, catat error dan commit, periksa journal web/runner.
- Matikan timer untuk menghentikan rekonsiliasi, bukan untuk membuka blokir.
- Perbaiki penyebab dan ulangi migrasi/runner; jangan menghapus lima tabel baru.
- Jangan rollback ke kode lama tanpa koordinasi jika jadwal penagihan sudah aktif;
  kode lama tidak menegakkan blokir. Jangan restore backup lama setelah transaksi
  baru masuk karena transaksi tersebut dapat hilang.
- Bila user sudah kembali bertransaksi, utamakan perbaikan ke depan; pemulihan DB
  harus direncanakan DBA, bukan dilakukan sebagai tombol undo deployment.
