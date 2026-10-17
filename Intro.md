# GajiKu-Live - Panduan Project untuk AI dan Developer

> Baca dokumen ini sebelum mengerjakan task. Gunakan sebagai peta awal, lalu periksa delta kode yang relevan; tidak perlu audit penuh untuk setiap task rutin.
> Dokumen ini adalah snapshot source, bukan jaminan bahwa aplikasi production sudah aman.

## 1. Identitas dan Cakupan

- Project: **GajiKu / Gajiku-Live**, aplikasi web dana talangan gaji / Earned Wage Access (EWA). Footer menampilkan **PT Solusi Akses Nusantara**.
- Workspace: `D:\Projects\GajikuEWA\Gajiku-Live`. Audit: **16 Oktober 2026**, konteks waktu Asia/Jakarta.
- Acuan Git: HEAD `716d1b5`, commit 4 Oktober 2026, `Perubahan scrolling modul`. Label versi konfigurasi: `v1.2.0-register`, bukan bukti versi deployment.
- Cakupan: audit statis entrypoint, konfigurasi, skema/migrasi, alur auth/limit/transaksi/approval, task background, frontend, dan dokumen historis. Bukan pentest atau pemeriksaan setiap baris repository.
- Tidak mengakses server, database/data pegawai, SMTP, Redis, atau endpoint live. Nilai secret `.env` dan private key tidak dibuka; hanya nama key `.env` yang diinventarisasi.
- Sesi ini hanya menambahkan `Intro.md`; temuan audit di sini **belum diperbaiki**.

Worktree sudah dirty sebelum audit: dokumen dan migrasi root tercatat dihapus, `.md/` dan `migrasidb/` belum tracked, `desktop.ini` berubah, dan ada ikon baru. Ini konsisten dengan reorganisasi lokal, tetapi maksud pemilik belum dikonfirmasi. Jangan revert atau commit perubahan tersebut tanpa instruksi.

## 2. Ringkasan Project

GajiKu mengelola master pegawai beberapa perusahaan, registrasi akun pegawai, plafon berdasarkan gaji, pengajuan REG/URG, approval admin perusahaan, dan finalisasi superadmin. Tersedia riwayat, ekspor CSV, impor pegawai Excel, notifikasi email, serta aturan khusus Windu Karya dan project-nya.

Arsitektur: **monolit Python Flask + Jinja**, bukan React/Next.js atau Supabase. Tidak ada frontend build npm. Database default adalah **MySQL**; SQLite tersedia untuk lokal/legacy tetapi kompatibilitas route belum lengkap.

**Tidak ditemukan integrasi API payout/bank/payment gateway pada jalur approval yang ditinjau.** Finalisasi hanya mengubah status DB. Transfer merupakan proses operasional di luar aplikasi; klik `Done` bukan bukti pemanggilan API bank atau keberhasilan transfer aktual.

## 3. Peta File

- `app.py`: entrypoint development, port default `5001`, bind `0.0.0.0`, debug mengikuti `FLASK_DEBUG`.
- `wsgi.py`: entrypoint WSGI, mengekspor objek `app`.
- `app/__init__.py`: `create_app()`, loader `.env`, blueprint, session/CSRF, request ID, inisialisasi DB dan background task.
- `app/config.py`: konfigurasi DB, SMTP, fee, secret, fallback akun admin.
- `app/db.py`: MySQL/SQLite, wrapper SQL, bootstrap SQLite, auto-migrasi fitur.
- `app/routes/web.py`: hampir seluruh route, query, otorisasi, validasi, dan rumus finansial; snapshot **4.451 baris / 50 dekorator route**.
- `app/repositories/user_repo.py`: helper lookup `users`; bukan repository layer lengkap.
- `app/services/email_service.py`: SMTP SSL, attachment, enqueue email.
- `app/tasks/queue.py`, `app/tasks/worker.py`, `app/tasks/jobs.py`: antrean inline atau Redis/RQ, worker dan job email.
- `app/tasks/export_scheduler.py`: CSV otomatis bulan kalender berjalan dan email laporan.
- `app/utils/cache.py`, `app/utils/logging.py`: cache per proses/TTL/invalidation dan logging dengan request ID.
- `templates/`: Jinja auth/pegawai/admin/superadmin; ada CSS/JS khusus halaman.
- `static/style.css`, `static/app.js`, `static/img/`: UI, JavaScript, brand. Tujuan upload aktif `static/uploads/avatars/`; berbeda dari gambar lama tracked di `app/static/uploads/avatars/`.
- `data/`: default SQLite dan output `data/csv/`, di-ignore Git. Jangan dianggap dummy.
- `migrasidb/`: skrip fitur MySQL, bukan sistem migrasi bernomor terkelola framework.
- `.md/Audit.md`: audit historis; beberapa temuan/nomor baris tertinggal.
- `.md/PerubahanFiles.md`: riwayat fee manual, approval, Windu, impor, hak approval, dan pengujian yang dilaporkan penulis sebelumnya.
- `.md/PlanningGPT.md`: rencana/deskripsi UI, bukan bukti semua item selesai.
- `app/add_superadmin.py`, `app/ubah_pw.py`, `app/ubah_role.py`, `app/cek_db.py`, `app/inspect_sqlite_schema.py`: maintenance SQLite.
- `app/migrasi_local.py`: migrasi SQLite ke MySQL, **destruktif pada target**, bukan startup harian.
- `requirements.txt`: Flask, Gunicorn, Redis, RQ, pytest, mysql-connector-python, python-dotenv, openpyxl; belum dipin.
- `requirements_vercel.txt`, `vercel.json`: jalur deploy alternatif yang tidak selaras dengan source.

## 4. Peran dan Model Data

### Pegawai

`pegawai` adalah master: ID, nama, email, jabatan, gaji, status aktif, perusahaan/project, perusahaan induk, siklus gaji, rekening, telepon, dan fee. ID pegawai alfanumerik maksimal 16 karakter.

Registrasi hanya menerima email yang sudah ada di master. `user_accounts` menyimpan akun formal, hash password Werkzeug, dan `pegawai_id`. `users` adalah identitas transaksi/legacy; saat login, nama/gaji disinkronkan dari master melalui email, lalu ID `users` masuk session.

**Jangan menyamakan ID ketiga tabel tersebut.** Banyak join memakai email, kadang `LOWER/TRIM`. Perubahan email dapat memengaruhi linkage akun, histori, scope, dan limit. Perubahan gaji master belum tentu langsung berlaku untuk pengguna yang belum login ulang.

### Admin dan Superadmin

`admins` menyimpan kredensial, role, company, status, `hak_approval`, cutoff, dan izin produk. Session penting: `is_admin`, `is_superadmin`, `admin_id`, `admin_email`, `company`, `hak_approval`.

Admin biasa umumnya mengelola perusahaan sendiri; admin induk mencakup pegawai dengan `perusahaan_induk` yang sesuai. Ada pengecualian email admin global legacy di kode; jangan mengasumsikan semua route terisolasi tenant.

Superadmin mengelola lintas perusahaan, admin/hak approval, konfigurasi, laporan, dan finalisasi. Login dapat memakai role database atau pasangan konfigurasi `SUPERADMIN_USERNAME`/`SUPERADMIN_PASSWORD` bila keduanya diisi.

### Tabel Pendukung

- `transactions`: FK ke `users.id`; tanggal/periode, nominal, fee, produk, status, snapshot rekening, deadline cancel, dan metadata approval.
- `app_settings`: key/value global, termasuk `enabled_products`, `ppn_enabled`, `runtime_force_limit`, `export_csv_last_run`.
- `windu_projects`: daftar resmi project Windu Karya.
- `pegawai_archive`: snapshot arsip dalam alur penghapusan; jangan menganggap seluruh histori selalu dipertahankan tanpa mengecek route terkait.
- `txn_tests`: tabel diagnostik endpoint tulis `/txn/test`.

Tidak ada skema ORM tunggal. Skema tersebar antara bootstrap, auto-migrasi, SQL manual, dan DB yang sudah tersedia.

## 5. Aturan Plafon, Produk, dan Biaya

Sumber utama: `compute_limits()`, `get_weekly_cutoff_config()`, helper periode/fee, `tarik_gaji()` di `app/routes/web.py`.

- Plafon bulanan = `floor(0.5 * gaji)`.
- Mode bulanan: plafon periode = plafon bulanan; limit harian = `floor(plafon / 30)`.
- Siklus A/B/C/D mulai tanggal **1/16/21/26**. Kunci `YYYY-MM` mengikuti bulan awal siklus, tidak selalu bulan transaksi.
- Cutoff mingguan: plafon periode = `floor(plafon_bulanan * 0.25)`; limit harian = `floor(plafon_periode / 7)`.
- Hari cutoff: Senin=0 sampai Minggu=6, default 2/Rabu. Periode mulai sehari setelah cutoff, misalnya Kamis-Rabu; kunci `W:YYYY-MM-DD` memakai tanggal awal periode.
- Pemakaian menjumlahkan **nominal** transaksi `sukses` dan `on-proses` dalam periode sampai tanggal acuan. Pending ikut mengurangi plafon; ditolak/dibatalkan tidak. Bila email tersedia, dihitung lintas `users` dengan email sama.
- Saldo REG = `max(limit_harian * hari_ke - total_pemakaian, 0)`; sisa plafon = `max(plafon - total_pemakaian, 0)`.
- REG dibatasi saldo akumulatif dan **Rp1.000.000 per pengajuan**.
- URG boleh memakai sisa plafon periode. Bagian dalam saldo REG dikenai satu fee dasar bila ada; kelebihan dikenai `ceil(bagian_urg / limit_harian) * fee_dasar` bila limit harian positif.
- Fee dasar dari `pegawai.admin_fee_flat`, fallback Rp15.000. Input manual valid 0 sampai Rp1.000.000, bukan hanya Rp15.000/Rp17.000. Tuple pilihan lama pada `/health` bukan batas validasi.
- Bila PPN aktif, kode memakai `ceil(fee * 1.11)`. Ini deskripsi implementasi, **bukan verifikasi aturan pajak**.
- Produk aktif adalah irisan setting global dan company. Lookup company pegawai memakai `perusahaan_induk`; tanpa company terkait, helper memakai kebijakan global.
- Rekening tujuan dari master: bank utama, bank kedua, e-wallet. Nomor/label pilihan disalin ke transaksi.

**Jangan diam-diam mengubah rumus, default siklus, atau arti periode.** Tanggal acuan masih dapat dipengaruhi client; lihat temuan audit.

## 6. Alur Transaksi dan Aturan Windu

1. Admin memasukkan/mengimpor master; pegawai registrasi memakai email master lalu login.
2. Pegawai aktif membuka `/tarik-gaji`, memilih produk/rekening/nominal, melihat invoice preview, lalu submit.
3. Server memvalidasi ulang produk, rekening, nominal, dan plafon. Transaksi dibuat `on-proses`, `cancel_until` sekitar 25 detik, lalu email diantrekan best-effort.
4. Admin sesuai scope dengan `hak_approval=1` approve layer pertama: `admin_approved_at/by` terisi; status **tetap `on-proses`**.
5. Superadmin melihatnya di **Menunggu Transfer**, kemudian finalisasi setelah transfer operasional: status `sukses`, `final_approved_at/by` terisi.
6. Admin boleh menolak sebelum approval pertamanya. Superadmin boleh menolak selama masih `on-proses`.

Status database: `on-proses`, `sukses`, `ditolak`, `dibatalkan`. **Menunggu Transfer / menunggu_superadmin adalah tahap UI/helper, bukan status database tambahan.**

- `hak_approval` default **OFF/0**. Non-Windu tanpa admin perusahaan/induk dengan hak ON boleh langsung difinalisasi superadmin.
- Windu Karya **tidak boleh bypass**: wajib admin Windu sendiri, lalu superadmin. Admin holding/global bukan penggantinya.
- Deteksi Windu exact match `Windu Karya`, case-insensitive dan trim, bukan substring `windu`.
- Windu dianggap induk meskipun belum ada pegawai. Project wajib dari `windu_projects`; admin Windu/superadmin dapat mengelola daftar.
- Minimal satu admin Windu perlu hak approval ON. Warning saat hak terakhir dimatikan tidak memblokir penyimpanan; antrean bisa macet.
- Hak/company tersimpan di session saat login; perubahan DB belum otomatis memperbarui session lama.

Bypass, Windu, dan formula finansial adalah keputusan bisnis yang memerlukan persetujuan serta regresi sebelum diubah.

## 7. Route Penting

- Auth: `/login`, `/register`, `/forgot`, POST `/logout`.
- Pegawai: `/dashboard`, `/tarik-gaji`, `/riwayat`, `/settings`, `/avatar/upload`, `/avatar/delete`.
- Transaksi: POST `/tx/<txid>/cancel`, GET `/api/tx_status`; `/pencairan` redirect legacy.
- Admin: `/admin/dashboard`, `/admin/pegawai`, `/admin/settings`, `/admin/riwayat`, `/admin/export`.
- Master: add/update/delete di bawah `/admin/pegawai`; impor `/admin/pegawai/import`, template `/admin/pegawai/import/template`.
- Windu: `/admin/windu-projects/add`, `/admin/windu-projects/<proj_id>/delete`.
- Approval: POST `/admin/tx/<txid>/approve` atau `/reject`; finalisasi lewat `/superadmin/tx/<txid>/approve` atau `/reject`.
- Superadmin: `/superadmin/dashboard`, `/superadmin/admins`, `/superadmin/settings`, `/superadmin/riwayat`, `/superadmin/export_range`.
- Operasional: `/ping`, `/health/db`, `/health`, `/metrics`, POST `/txn/test`.
- **Destruktif, jangan dipanggil untuk audit/smoke test:** POST `/reset`, `/admin/reset`, `/admin/reset_all`, `/superadmin/reset`, `/superadmin/reset_all`.

## 8. Frontend, Impor, dan Ekspor

UI Jinja memakai base template, CSS/JS bersama, serta CSS/JS khusus halaman. Tema `legacy`/`new` disimpan pada localStorage `gajiku-theme`. Invoice adalah preview browser; server tetap sumber validasi. Dashboard polling status sekitar 5 detik dan menampilkan countdown lokal.

Impor openpyxl hanya `.xlsx`, maksimal **5 MB / 500 baris data**; ada validasi duplikat ID/email dan scope perusahaan/project. **Partial success:** baris valid disimpan walau lainnya error. Fee Excel hanya dihormati untuk superadmin; admin biasa memakai default. Siklus impor default B, berbeda dari bootstrap yang default A.

Ekspor manual CSV; scheduler menulis `data/csv/` dan mengirim attachment berisi data pegawai/transaksi. File dan mailbox penerima adalah permukaan data sensitif.

## 9. Environment dan Startup Aman

### Nama Environment

- Aplikasi: `APP_SECRET`, `FLASK_DEBUG`, `PORT`.
- DB: `DB_TYPE`, `DB_PATH`, `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASS`, `MYSQL_DB`.
- Email: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `EMAIL_FROM`, `EMAIL_ADMIN_LIST`.
- Admin fallback: `ADMIN_USER`, `ADMIN_EMAIL`, `ADMIN_PASS`, `SUPERADMIN_USERNAME`, `SUPERADMIN_PASSWORD`.
- Background/deploy: `START_INLINE_WORKER`, `REDIS_URL`, `EXPORT_CSV_ENABLED`, `VERCEL`.

Jangan salin nilai secret ke dokumen/chat/test/commit. File `.env` lokal memiliki key DB/SMTP; nama `APP_SECRET` tidak ditemukan pada daftar key file. Environment proses/server mungkin tetap mengisinya; nilainya tidak diverifikasi.

### Side Effect App Factory

`create_app()` **bukan read-only**: memuat `.env`, menjalankan `ensure_db()`, dapat melakukan DDL/seed/migrasi, lalu menyalakan background task. Import entrypoint, Flask CLI, atau test client yang membuat app dapat memicunya.

- MySQL: cek koneksi dan auto-migrasi fitur; **tidak bootstrap skema inti lengkap**. DB/tabel inti harus sudah tersedia.
- SQLite: membuat DB/tabel/migrasi/indeks dan seed akun/data demo bila kosong. Foreign key enforcement dimatikan pada koneksi.
- Loader `.env` tidak menimpa environment proses yang sudah ada.
- Default `START_INLINE_WORKER=1` dan `EXPORT_CSV_ENABLED=1`. Scheduler bisa ekspor/email segera ketika belum ada catatan run.
- Sandbox harus memakai DB dummy dan `START_INLINE_WORKER=0`, `EXPORT_CSV_ENABLED=0`, `FLASK_DEBUG=0` secara **eksplisit di environment proses sebelum startup**.
- **SQLite kosong belum sandbox siap pakai:** bootstrap belum menyediakan semua kolom route. Jangan menunjuk DB live untuk mengakali error lokal.

### Cara Jalan Berdasarkan Source

- Dependency: `python -m pip install -r requirements.txt` di virtual environment bila diperlukan, mengikuti izin akses jaringan.
- Development: `python app.py`, setelah DB dummy/background aman. Sintaks mengisyaratkan Python minimal 3.10; validasi AST memakai Python 3.12 lokal, bukan konfirmasi versi server.
- WSGI Linux: entrypoint sesuai source `gunicorn --bind 127.0.0.1:5001 wsgi:app`; konfigurasi proxy/worker/service production belum diketahui. Gunicorn bukan jalur native Windows.
- RQ: entrypoint tersedia `python -m app.tasks.worker`, dengan `REDIS_URL` diekspor sebelum import. Kompatibilitas versi RQ belum diuji.
- Tidak ditemukan Dockerfile, service/reverse proxy, CI, lockfile dependency, atau test suite pada inventaris file kerja. Jangan mengarang domain/IP/topology/jumlah worker/deploy aktual.
- `vercel.json` menunjuk `index.py` yang tidak ada. Requirements Vercel juga tidak mencakup dependensi import utama. Jangan menganggap deploy Vercel berfungsi; SQLite `/tmp` bukan penyimpanan finansial permanen.

## 10. Antrean, Scheduler, dan Cache

- Tanpa Redis/RQ aktif, email memakai queue memory/thread daemon. Pending job tidak durable saat proses mati; worker inline perlu flag startup aktif.
- Mode RQ dipilih saat **import** `app/tasks/queue.py` berdasarkan library dan `REDIS_URL`, sebelum loader `.env` di `create_app()`. Menaruh URL hanya di `.env` belum menjamin RQ aktif; ekspor ke environment proses sebelum import.
- Scheduler mengecek sekitar tiap 1 jam; interval ekspor logis **2 hari**. `export_csv_last_run` diperbarui hanya setelah email sukses. Tanpa catatan, job layak jalan segera.
- Dataset scheduler adalah **bulan kalender berjalan**, bukan otomatis periode gaji/cutoff tiap pegawai.
- Started flag worker/scheduler dan cache bersifat per proses. Banyak WSGI worker dapat punya scheduler sendiri; tidak terlihat distributed lock pencegah ekspor/email ganda.
- Job RQ membuat app factory lagi. Mematikan inline worker tidak otomatis mematikan scheduler.
- `clear_cache_prefix('admin_kpi:')` hanya membersihkan memory proses request itu, bukan seluruh instance.

## 11. Database dan Migrasi: Larangan Operasional

- Jangan membuka/mengubah DB live untuk mengenali repository.
- Jangan menjalankan `app/migrasi_local.py` untuk setup biasa: `migrate_table()` melakukan **TRUNCATE tabel MySQL target** sebelum menyalin; beberapa error ditangani sambil melanjutkan proses.
- Maintenance SQLite tidak otomatis berlaku ke MySQL production. Beberapa skrip punya kode tingkat module; jangan import untuk inspeksi.
- `migrasidb/migrasi_2layer_approval.sql`: metadata approval; periksa seluruh isi, bukan hanya judul.
- `migrasidb/migrasi_hak_approval.sql`: hak approval, default OFF.
- `migrasidb/migrasi_cutoff_mingguan.sql`: cutoff company.
- `migrasidb/migrasi_produk_admin.sql`: izin REG/URG company.
- `migrasidb/migrasi_windu_projects.sql`: daftar project Windu.
- `migrasidb/migrasi_perusahaan_induk_guixu.sql`: update relasi data Guixu, bukan hanya skema.
- Wrapper MySQL mengganti `?` ke `%s`, **tidak menerjemahkan dialek SQL**. Upsert MySQL/placeholder `%s` langsung tetap tidak portable ke SQLite.
- Beberapa auto-migrasi menangkap exception dan hanya mencatat error. Startup berhasil bukan bukti seluruh kolom sehat. Migrasi/deploy perlu review SQL, backup, pengecekan skema target, dan izin pemilik.

## 12. Temuan Audit Prioritas

Urutan menunjukkan prioritas penanganan, bukan klaim eksploit telah diuji. Referensi baris mengikuti snapshot dan dapat bergeser. Seluruh temuan ini **masih terbuka saat dokumen dibuat**.

### P0 - Isolasi dan Integritas Data

1. **Reset global tanpa autentikasi/otorisasi role.** `app/routes/web.py:4435`, `reset_db()` menghapus seluruh `transactions` dan `users`. Middleware CSRF tetap berlaku, tetapi token tersedia pada form auth publik; CSRF bukan pengganti login/role. Kunci/nonaktifkan jalur ini sebelum dianggap aman untuk live.
2. **Admin perusahaan bisa reset lintas tenant.** `app/routes/web.py:4160` dan `app/routes/web.py:4174` hanya `require_admin()` lalu DELETE tanpa scope company; reset_all juga menghapus akun/master. Perlu pembatasan role/scope/kontrol operasional.
3. **Tanggal client memengaruhi plafon.** `app/routes/web.py:601`, `app/routes/web.py:755`, `app/routes/web.py:1224`: query/session/form tanggal dipakai menghitung limit/periode tanpa pembatasan ke tanggal server sebenarnya. Tetapkan kebijakan tanggal produksi sebelum memperbaiki.
4. **Secret/fallback kredensial lemah masih ada.** `app/config.py:5`, fallback login dalam `login()`, seed `app/db.py:291`. Jangan menyalin password bawaan. Perlu secret unik, penonaktifan fallback production, pemeriksaan akun seed. Override environment server belum diverifikasi.

### P1 - Auth dan Konsistensi Transaksi

5. **Pemakaian plafon belum atomik.** `app/routes/web.py:1224`: agregat limit dibaca lalu INSERT terpisah tanpa lock per pegawai/idempotency key. Request paralel berpotensi sama-sama lolos; perlu uji concurrency DB target dan perbaikan transaksi.
6. **Cancel tidak menegakkan batas 25 detik/tahap approval.** `app/routes/web.py:1456` membaca `cancel_until` tetapi tidak mengeceknya; hanya pemilik/status `on-proses`. Setelah admin approve, status tetap sama sehingga cancel bisa berbenturan dengan Menunggu Transfer. UPDATE tidak mengulang syarat status, berisiko race dengan finalisasi.
7. **Admin nonaktif masih bisa login.** Query admin di `login()` tidak memilih/mengecek `status_aktif`, padahal edit admin menyimpannya. Session lama juga tidak memvalidasi ulang hak/company tiap request.
8. **Forgot langsung mengganti password berdasarkan email.** `app/routes/web.py:891`: tanpa verifikasi token pemilik dahulu; password dikirim email dan hasil `send_email()` tidak dicek sebelum commit. Permintaan publik dapat mengganggu akses; UI bisa mengklaim terkirim saat SMTP gagal. Tidak terlihat limiter aplikasi.
9. **Polling tidak memfilter pemilik.** `app/routes/web.py:1475`: pengguna login bisa meminta ID transaksi lain dan menerima ID/status/countdown. Response tidak memuat nominal, tetapi otorisasi kepemilikan tetap kurang.
10. **Tenant admin bisa mengubah produk/PPN global.** `app/routes/web.py:4224`, `admin_products()` hanya perlu admin lalu mengubah setting global. Konfirmasi apakah kewenangan ini memang disengaja sebelum mempersempit.

### P2 - Operasional dan Pengembangan

11. **SQLite belum parity.** Bootstrap `app/db.py` belum menyediakan kolom route seperti `admins.role/status_aktif/no_telp` dan `pegawai.perusahaan_induk`. Forgot memakai `%s` langsung, setter setting memakai upsert MySQL. DB berhasil dibuat bukan bukti semua fitur lokal bekerja.
12. **Diagnostik menulis tanpa auth.** POST `/txn/test` di `app/__init__.py` tidak punya guard login dan dikecualikan dari CSRF; bisa menambah tabel diagnostik. `/health/db` juga publik; tentukan exposure operasional.
13. **Hardening/upload belum eksplisit.** Tidak ditemukan konfigurasi khusus cookie Secure/SameSite/expiry atau limiter aplikasi. Avatar hanya diperiksa ekstensi, tanpa batas ukuran eksplisit seperti impor Excel. Proteksi proxy/platform di luar repo tidak diverifikasi.
14. **Background berisiko di multiworker.** Startup dapat memicu email/ekspor; started flag per proses; mode RQ mendahului loader `.env`. Perlu verifikasi environment deployment dan penjadwal tunggal.
15. **Deploy/pengujian belum reproducible.** Dependency tidak dipin, test suite tidak ditemukan, Vercel tidak sesuai file tersedia, dan `web.py` monolit. Banyak skenario lolos disebut dalam dokumen lama, tetapi skrip/fixture tidak tersedia untuk direproduksi.

Jangan menyalin temuan `.md/Audit.md` sebagai fakta terkini tanpa cek: `hak_approval`, exact-match Windu, dan sebagian perbaikan query/error handling sudah ada. Utamakan source aktual dan bukti terverifikasi.

## 13. Hasil Validasi dan Ketidakpastian

- **Lulus parsing syntax AST untuk 25 file Python**, termasuk entrypoint dan seluruh `.py` di `app/`, tanpa import aplikasi atau bytecode/cache.
- Ukuran monolit/route dihitung dari source; status Git diperiksa untuk membedakan perubahan awal dari dokumen baru.
- Tidak menjalankan app factory/server, pytest integration, migrasi, endpoint, email, worker/scheduler, atau uji concurrency. Tidak ada klaim smoke test fungsional lulus.
- Belum diketahui: skema/data DB live, versi server/dependency, environment runtime, admin aktif, domain/TLS/proxy, proses transfer, backup/restore, retensi CSV, jumlah instance, dan deploy di luar repository.
- Ini bukan verifikasi kepatuhan finansial/pajak atau sertifikasi keamanan production.

## 14. Protokol AI Berikutnya

1. Baca `Intro.md`, `git status --short`, dan `AGENTS.md` yang berlaku bila kemudian ditambahkan. Jangan mulai audit penuh untuk task rutin.
2. Pilih area dari peta file; baca fungsi/template/query relevan serta delta sejak snapshot. Audit ulang hanya bagian terdampak atau asumsi yang tidak lagi cocok.
3. Perlakukan ini project live: jangan jalankan app/migrasi/reset/maintenance sebelum memastikan target non-production dan izin yang diperlukan.
4. Pisahkan fakta source, kebijakan bisnis, asumsi, dan deployment yang belum diverifikasi. Dokumentasi mengurangi audit berulang, bukan menghilangkan verifikasi selamanya.
5. Prioritaskan P0 lalu auth/integritas transaksi. Jangan gabungkan refactor besar/perubahan rumus dengan bugfix kecil tanpa diminta.
6. Jangan membuka/menampilkan secret, key, DB, rekening/gaji, atau CSV kecuali diperlukan dan diizinkan. Ignore Git bukan bukti tidak pernah bocor di histori.
7. Hormati perubahan lokal: jangan revert/hapus/stage/commit/deploy di luar scope permintaan.
8. Untuk perubahan finansial/auth, verifikasi pada DB dummy: scope tenant, admin nonaktif, perubahan hak/session, REG/URG/fee, batas siklus, cutoff, Windu tanpa bypass, cancel vs approve, request paralel, SMTP gagal. Ini rekomendasi regresi, bukan suite yang sudah lulus.
9. Perbarui dokumen setelah task: tanggal/acuan commit, perilaku baru, temuan selesai, dan validasi yang benar-benar dijalankan.

Prompt awal untuk AI baru: **"Baca Intro.md dulu, cek perubahan sejak snapshot, lalu kerjakan task saya tanpa audit penuh ulang atau menyentuh data live."**
