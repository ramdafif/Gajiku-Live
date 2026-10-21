Udah gue cocokkan **Redesign lokal dengan konfigurasi dan skema MySQL VPS**. Bisa pakai database yang sekarang, tapi **jangan langsung `git pull` lalu restart service aktif**.

**VPS tetap read-only:** gue tidak mengubah file, ENV, data, atau service.

Panduan setelah `git pull` beserta migrasi lifecycle terbaru ada di
`deploy-vps-after-pull.md`. Kondisi VPS di dokumen ini adalah catatan inspeksi
sebelumnya, bukan verifikasi ulang pada sesi pengujian terbaru.

## Catatan Kondisi VPS
- Production: `/home/gajiku/app/gajiku`, service `gajiku.service`, Gunicorn 3 worker di `127.0.0.1:5000`.
- Nginx meneruskan `gajiku.net`, `app.gajiku.net`, dan `www.gajiku.net` ke port 5000.
- MySQL **8.0.46**, database `gajiku_db`. Kolom approval, cutoff, produk, dan perusahaan induk sudah tersedia.
- Redis aktif di `127.0.0.1:6379`, tetapi aplikasi production belum menggunakannya.
- Port **5001 dipakai demo**, 5002 dipakai aplikasi lain. Saat diperiksa, **5010 kosong** untuk calon instance Redesign.

## 1. Yang Wajib Sebelum Aplikasi Baru Dijalankan

**Dua blocker startup yang sudah ketemu:**
- **`APP_SECRET` belum ada di ENV VPS.** Redesign akan menolak startup tanpa secret yang valid — `app/__init__.py:70`.
- **Pillow belum terpasang**, padahal di-import langsung oleh route. `defusedxml` juga belum terpasang. Keduanya sudah tercantum di `requirements.txt:9`; install dependency ke **virtualenv release baru**, bukan mengutak-atik virtualenv production aktif.

Untuk source yang dipush:
- Sertakan seluruh file baru `app/`, `templates/`, `static/`, `migrasidb/`, dan dependency. Redesign bukan sekadar perubahan CSS.
- Sertakan folder template baru, font, `pages.css`, `responsive.css`, serta `withdrawal.js`. Tidak ada build npm yang dibutuhkan.
- Jangan sertakan `.env`, private key, dump database, atau data pengguna.
- **Ada avatar pengguna yang sudah tracked Git.** `.gitignore` tidak menghapus tracking existing. Pisahkan penyimpanan upload sebelum melakukan cleanup tracking; jangan sampai update Git menghapus avatar production.

## 2. Migrasi Database yang Diperlukan

**Pakai `gajiku_db` existing. Tidak perlu mengganti database atau mengimpor dump lokal.**

Urutan skrip yang diperlukan:

1. **`migrasidb/migrasi_kelola_client.sql:5`**
   Membuat `clients` dan `client_projects`, mengisi katalog dari data existing, menormalisasi sebagian nama PT/project, lalu menyimpan marker `clients_catalog_v1`.

2. **`migrasidb/migrasi_security_hardening.sql:5`**
   Membuat tabel rate limit dan registrasi tertunda, serta menambahkan `transactions.request_key` dengan unique index. `pegawai_archive` sudah ada, sehingga tidak perlu dibuat ulang.

3. **`migrasidb/migrasi_company_lifecycle.sql:1`**
   Menambahkan konfigurasi penagihan, tagihan unik per periode, tindak lanjut MoU,
   event notifikasi, dan status baca per akun tanpa mengubah status pribadi pegawai.

**Migrasi lama untuk approval/cutoff/produk tidak perlu diulang berdasarkan skema yang gue periksa.**

Catatan penting:
- Migrasi client **bukan hanya menambah tabel**; ada `UPDATE pegawai`. Ada **39 pegawai dengan perusahaan induk kosong** yang perlu divalidasi mapping-nya, bukan asal diisi.
- Backup database dan verifikasi pemulihannya sebelum migrasi. Tahan sementara perubahan master pegawai/admin/project selama backfill dan cutover.
- Jalankan migrasi manual **sebelum menyalakan candidate**. Startup aplikasi masih otomatis memanggil `ensure_db()`.
- DDL MySQL tetap bisa menunggu metadata lock meskipun operasinya mendukung online DDL. Jadi ukuran database kecil bukan jaminan tidak ada blocking. 
- **Jangan menjalankan `app/migrasi_local.py`** untuk deployment; itu jalur penggantian data, bukan migrasi rilis.

## 3. ENV Production

**Pertahankan nilai `MYSQL_*` dan `SMTP_*` production yang sekarang. Jangan menimpa `.env` VPS dengan `.env` laptop.**

Tambahkan/verifikasi konfigurasi berikut. Nilai secret dan penerima email harus diisi, bukan disalin sebagai placeholder:

```dotenv
APP_SECRET=ISI_SECRET_ACAK_KUAT
PUBLIC_BASE_URL=https://app.gajiku.net
EMAIL_ADMIN_LIST=ISI_EMAIL_PENERIMA_RESMI

FLASK_DEBUG=0
DB_TYPE=mysql
SESSION_COOKIE_SECURE=1
TRUST_PROXY_HEADERS=1

ALLOW_DATE_OVERRIDE=0
ALLOW_DESTRUCTIVE_RESET=0
ENABLE_TEST_ENDPOINTS=0
SEED_DEMO_DATA=0
```

- `PUBLIC_BASE_URL`: pilih satu domain HTTPS kanonik untuk link verifikasi/reset password.
- `EMAIL_ADMIN_LIST`: belum ada di VPS; tanpa ini notifikasi/export yang memakai penerima default tidak akan terkirim.
- `TRUST_PROXY_HEADERS=1`: sesuai konfigurasi Nginx yang diperiksa; tetap verifikasi IP pengguna terbaca benar.
- Akun admin dan superadmin database sudah tersedia. Tidak perlu menambah akun ENV/default hanya untuk deploy.

**Konsekuensi sesi:** kode production masih memakai fallback secret development. Secret itu jangan dipertahankan. Setelah diganti, pengguna perlu login ulang; form pengajuan lama juga perlu dimuat ulang. Jadi **ketersediaan service bisa dijaga, tetapi sesi pengguna tidak akan sepenuhnya seamless**.

## 4. Worker, Scheduler, Upload, dan Nginx

**Untuk notifikasi yang tidak bergantung pada umur proses web, gunakan Redis/RQ worker terpisah.** Entrypoint-nya sudah ada:

```bash
python -m app.tasks.worker
```

Target ENV web/RQ setelah worker tersebut siap:

```dotenv
REDIS_URL=redis://127.0.0.1:6379/1
START_INLINE_WORKER=0
EXPORT_CSV_ENABLED=0
```

Jangan mematikan inline worker tanpa RQ worker yang berfungsi.

**Perubahan yang gue sarankan disiapkan di repo/config deploy:**
- Tambahkan service RQ dan **runner/service scheduler terpisah**. Scheduler sekarang masih thread di proses web, belum punya entrypoint standalone — `app/tasks/export_scheduler.py:204`. Jangan membiarkan export mati permanen tanpa penggantinya.
- Green/candidate harus memakai `EXPORT_CSV_ENABLED=0` saat warmup. Scheduler production lama belum memakai lock yang sama dengan versi baru; menjalankan keduanya bisa menggandakan email export.
- Siapkan persistence Redis sesuai kebutuhan. Saat ini **AOF nonaktif** dan hanya memakai snapshot; job terbaru masih berpotensi hilang saat crash. RQ sendiri mendukung shutdown worker yang menunggu job aktif selesai. 
- Pisahkan upload dan CSV menjadi storage bersama antar-release. Jangan pakai sinkronisasi `--delete` sembarangan pada direktori production.
- Tambahkan batas upload Nginx yang konsisten dengan aplikasi, misalnya `client_max_body_size 10m;`. Tidak ditemukan override pada konfigurasi terkait; default Nginx adalah 1 MB. 

## 5. Urutan Deploy Minim Downtime

1. **Commit/push Redesign secara terpilih**, lalu ambil commit yang pasti ke direktori release terpisah; jangan menimpa source yang sedang melayani production.
2. Siapkan virtualenv release, dependency, ENV, dan shared storage.
3. Backup DB, jalankan tiga migrasi, lalu verifikasi skema dan mapping client.
4. Jalankan candidate di **loopback port 5010**, scheduler nonaktif. Periksa `/ping`, `/health/db`, login, template, aset, serta worker.
5. Setelah candidate sehat, ubah target Nginx dari 5000 ke 5010, validasi konfigurasi, lalu **graceful reload Nginx**. Nginx mempertahankan worker lama untuk menyelesaikan request yang sedang dilayani. 
6. Setelah traffic lama selesai, tangani antrean inline yang tersisa sebelum menghentikan instance lama, lalu aktifkan satu scheduler baru. **Kode lama belum memberikan jaminan drain antrean inline**, jadi zero-loss notifikasi belum bisa dijanjikan.

Service saat ini belum memiliki `ExecReload`; **`systemctl restart gajiku` bukan mekanisme zero-downtime**. Gunicorn mendukung graceful reload, tetapi tidak menggantikan pemeriksaan ENV, dependency, migrasi, dan kompatibilitas antarversi. 

RAM tersedia saat pemeriksaan sekitar **500 MB tanpa swap**. Ukur penggunaan candidate sebelum menjalankan tambahan worker penuh agar cutover tidak memicu OOM.

## Hasil Verifikasi
**53 tes + 3 subtes lokal lulus.** Sintaks aplikasi juga lolos pemeriksaan grammar Python 3.10, sesuai versi Python VPS. Namun tes tersebut memakai SQLite fixture; migrasi/write-path MySQL, pengiriman SMTP, dan cutover production **belum dijalankan**.

**Kesimpulan:** Redesign memungkinkan dideploy menggunakan MySQL existing. Yang perlu disiapkan terlebih dahulu adalah **secret, dependency, dua migrasi, service background, shared storage, dan konfigurasi blue-green**. Rollback utamanya mengalihkan traffic ke release sebelumnya setelah kompatibilitas dipastikan—**bukan restore backup lama yang bisa menghilangkan transaksi baru**.
