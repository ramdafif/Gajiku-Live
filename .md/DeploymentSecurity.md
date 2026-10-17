# Deployment setelah perbaikan audit

Repo ini adalah staging lokal sebelum kode dipush ke VPS. Gunakan checklist ini saat
merilis perubahan yang sama ke VPS; tidak perlu membuat proses staging tambahan.
Dokumen ini mendampingi perubahan kode, bukan sertifikasi bahwa aplikasi bebas bug.

## Perubahan perilaku yang wajib diketahui

- Pembulatan PPN mengikuti aritmetika integer pada server dan preview/invoice; tidak mengubah persentase produk. Error pembacaan status PPN menggagalkan request, bukan diam-diam menghapus pajak.
- Export bulanan memakai tanggal Jakarta. Token verifikasi diperiksa ulang di dalam transaksi; ganti password mencegah request lama menimpa perubahan kredensial yang lebih baru.
- Registrasi pegawai sekarang harus memverifikasi kepemilikan email. Membuka link saja tidak membuat akun; pengguna harus menekan tombol konfirmasi.
- Lupa password mengirim link berlaku 30 menit, sekali pakai. Meminta reset tidak langsung mengubah password.
- Status aktif, perusahaan, role, dan hak approval admin diperiksa ulang dari DB pada request berikutnya. Password yang berubah membatalkan sesi lama.
- Transaksi legacy bertanggal masa depan tetap mereservasi plafon pada periodenya.
- Tanggal transaksi/limit memakai tanggal Jakarta dari server. Override tanggal hanya untuk demo/staging dengan ALLOW_DATE_OVERRIDE=1; production harus 0.
- Setiap form tarik gaji mempunyai token pengajuan. Request ulang dengan token yang sama tidak membuat transaksi kedua. Form lama yang terbuka sebelum deploy perlu dimuat ulang.
- Pembatalan hanya selama window 25 detik dan sebelum approval admin. Keputusan approval/cancel diserialisasi.
- Pegawai dengan riwayat transaksi tidak dapat dihapus permanen melalui menu pegawai. Nonaktifkan pegawai agar jejak finansial tetap tersedia.
- Reset massal dan endpoint tulis percobaan dinonaktifkan secara default. Admin PT tidak boleh mengubah produk/pajak global.
- Superadmin aktif terakhir dilindungi dari penghapusan/nonaktif. Admin Windu terakhir tidak dapat dilepas ketika masih ada pengajuan yang membutuhkan approval-nya.
- Konfirmasi approval final diperjelas: aplikasi mencatat status, bukan otomatis mengirim dana ke bank. Prosedur transfer manual dan penyimpanan bukti tetap diperlukan.
- Struktur satu client = satu PT, project per MoU, legacy PT kosong, dan aturan approval Windu tetap dipertahankan.

## Prasyarat sebelum cutover VPS

1. Pastikan file perubahan sudah di-commit/push secara terpilih. Jangan sertakan dump DB, private key, .env, atau data avatar pengguna.
2. Gunakan virtualenv VPS yang sesuai. Install requirements.txt; Pillow memvalidasi avatar dan defusedxml melindungi parser XML import Excel.
3. Backup DB dan uji pemulihannya. Tahan perubahan karyawan/project/admin terkait selama migrasi dan cutover; drain request yang sedang menulis.
4. Jalankan migrasidb/migrasi_kelola_client.sql bila belum pernah dijalankan.
5. Jalankan migrasidb/migrasi_security_hardening.sql. Script ini menambah security_rate_limits, pending_registrations, request_key unik pada transactions, dan archive bila belum tersedia. Tidak menghapus transaksi/karyawan existing.
6. Periksa ENV berikut, lalu jalankan smoke test pada instance baru sebelum mengalihkan traffic.
7. Aktifkan release sesuai process manager VPS dan lanjutkan perubahan data setelah verifikasi. Jangan mengasumsikan systemctl restart berarti zero downtime.

Script manual dan bootstrap aplikasi memakai pemeriksaan schema idempotent. DDL MySQL melakukan implicit commit;
backup tetap wajib. Setelah migrasi manual, runtime tidak harus mempunyai hak CREATE/ALTER untuk tabel-tabel baru.
Akun runtime tetap memerlukan hak baca/tulis yang memang dipakai aplikasi.

## ENV production

- APP_SECRET: wajib terisi dan bukan dev-secret-change-me. Gunakan secret acak kuat. Pertahankan secret production yang sudah kuat; menggantinya membuat sesi lama tidak berlaku.
- PUBLIC_BASE_URL: origin publik HTTPS yang benar, tanpa trailing slash. Dipakai untuk link registrasi/reset agar tidak mempercayai Host header request.
- MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASS, MYSQL_DB: pertahankan target production yang benar. Pakai akun aplikasi dengan hak minimum, bukan root bila memungkinkan.
- SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM: wajib diverifikasi untuk alur email. Port 465 memakai SSL; port lain memakai STARTTLS.
- EMAIL_ADMIN_LIST: penerima notifikasi/export yang disetujui. Tidak lagi mempunyai fallback ke email pribadi di kode.
- SESSION_COOKIE_SECURE=1: production HTTPS. Nilai 0 hanya untuk development HTTP lokal.
- ALLOW_DATE_OVERRIDE=0, ALLOW_DESTRUCTIVE_RESET=0, ENABLE_TEST_ENDPOINTS=0, SEED_DEMO_DATA=0: tetap nonaktif di production.
- ADMIN_PASS: tidak lagi memiliki password default. Jika memakai akun ENV legacy, isi kredensial eksplisit dan ADMIN_COMPANY yang tepat; lebih baik kelola admin PT melalui database.
- SUPERADMIN_USERNAME dan SUPERADMIN_PASSWORD: hanya bila memang memakai akun Superadmin ENV. Password akun ENV dikelola lewat konfigurasi server, tidak mengubah akun DB lain melalui Settings.
- TRUST_PROXY_HEADERS=1: hanya jika aplikasi memang berada di belakang satu reverse proxy tepercaya dan port backend tidak bisa diakses publik. Jangan aktifkan tanpa memastikan topologi proxy.
- REDIS_URL: bila dipakai, jalankan worker terpisah dengan python -m app.tasks.worker. Pemilihan backend sekarang membaca ENV setelah dotenv dimuat.
- START_INLINE_WORKER: jangan diubah ke 0 tanpa Redis/RQ worker yang berfungsi; antrean memory bukan antrean lintas-proses yang durable.
- EXPORT_CSV_ENABLED: pertahankan sesuai kebutuhan operasional. Lock DB mencegah dua scheduler bersamaan mengirim export periode yang sama.

Rate limit aplikasi memakai remote_addr; jika konfigurasi proxy salah, semua pengguna dapat terlihat berasal dari satu IP.
Verifikasi IP yang tercatat sebelum menjadikan rate limit aplikasi satu-satunya pengaman. Tetap sediakan rate limit di reverse proxy.

## Smoke test sebelum traffic production

- /ping dan /health/db mengembalikan status berhasil.
- Login Superadmin dan Admin PT; ulangi Dashboard -> Settings -> Kembali ketika cache masih aktif.
- Periksa daftar client, MoU, project per PT, daftar pegawai, riwayat, CSV, dan template Excel 14 kolom.
- Login admin PT berbeda dan pastikan tidak dapat membaca export atau mengubah/approve transaksi PT lain.
- Nonaktifkan akun uji atau ubah hak/perusahaannya, pastikan sesi aktif mengikuti perubahan pada request berikutnya.
- Uji registrasi email dan reset password menggunakan mailbox staging; link harus memakai domain yang benar.
- Uji akun aktif/nonaktif, token form, double submit, limit, pembatalan, approval berlapis, dan finalisasi manual.
- Uji SMTP, status worker RQ/Redis bila digunakan, notifikasi, export berkala, disk writable untuk avatar/export, dan log request ID.
- Pastikan tidak ada akun contoh berpassword publik di production. Menonaktifkan seed tidak otomatis menghapus akun contoh yang sudah ada.

## Verifikasi lokal yang sudah dilakukan

- 53 skenario HTTP/regresi permanen tersedia di tests/test_audit_regression.py, termasuk 3 subtest race password. Jalankan dengan python -m pytest -q sebelum push.
- Migrasi otomatis pada salinan dump terbaru dan kedua script manual masing-masing dua kali diuji di MariaDB sementara pada port loopback terpisah.
- Pada snapshot 16 Oktober 2026: 218 pegawai tetap ada; PT kosong 38 dan project kosong 63 setelah koreksi yang sebelumnya disetujui.
- Startup setelah migrasi manual diuji menggunakan user MySQL dengan hak SELECT saja; startup paralel diserialisasi.
- Request tarik gaji paralel di MariaDB tidak melewati plafon; cancel vs approve tidak menghasilkan status dibatalkan sekaligus approved.
- Dua export scheduler bersamaan menghasilkan satu panggilan kirim email pada fixture.
- Tambahan MariaDB: fee dasar 10.000 tersimpan 11.100, token verifikasi yang diganti ditolak, dan verifikasi paralel membuat satu akun saja.
- Render ulang 15 halaman menghasilkan 33 inline script yang lolos kompilasi; 200.002 pembandingan perhitungan fee JavaScript lulus.
- Chrome headless: desktop 1440px dan mobile 390px; alur Settings/Kembali, halaman pegawai, dan form pengajuan diuji tanpa error JavaScript/overflow halaman pada skenario yang dijalankan.
- SMTP, Redis service, payout bank, TLS/Nginx, systemd, backup production, dan deployment Vercel sungguhan belum diverifikasi.

## Batas dan tindak lanjut operasional

- Lock dan idempotency melindungi transaksi DB, bukan idempotency transfer bank di luar aplikasi.
- Email inline masih best effort dan dapat hilang saat proses berhenti. Produksi yang membutuhkan jaminan kirim perlu antrean durable, retry, dan monitoring failed jobs/outbox.
- Pengiriman export melalui SMTP dan penulisan marker DB bukan transaksi atomik lintas-sistem. Crash setelah email terkirim tetapi sebelum marker commit masih dapat menghasilkan pengiriman ulang pada retry berikutnya.
- Template tetap menggunakan asset CDN eksternal. Pertimbangkan self-hosting font/chart untuk mengurangi ketergantungan layanan pihak ketiga.
- Beberapa avatar legacy sudah berada dalam histori Git. Tidak dihapus otomatis karena merupakan data existing; evaluasi akses repo dan prosedur pembersihan histori secara terpisah.
- Script add_superadmin/ubah_pw/ubah_role/cek_db masih utilitas SQLite offline, bukan maintenance MySQL production. Jangan menjalankannya sebagai langkah deploy.
- migrasi_local.py adalah pemindahan penuh dengan TRUNCATE, bukan migrasi rilis. Sekarang ditolak secara default kecuali ALLOW_LEGACY_DB_REPLACE=1 diberikan secara sadar setelah backup.
- Entry point Vercel yang menunjuk index.py yang tidak ada diperbaiki menjadi wsgi.py dan dependensi disamakan. Runtime serverless, storage avatar, dan background jobs tetap membutuhkan validasi khusus bila platform itu benar-benar digunakan.
- Suite regresi permanen dan workflow GitHub Actions sudah ditambahkan. Workflow belum dijalankan di GitHub; pengujian MariaDB tetap terpisah dari job SQLite.
- Rollback kode tidak mengembalikan normalisasi data. Jangan restore backup lama setelah transaksi production baru masuk tanpa rencana rekonsiliasi.

## Prioritas produk dan bisnis berikutnya

1. Kontrol pencairan: bukti transfer/referensi pembayaran wajib, status payout terpisah dari approval, rekonsiliasi bank, dan idempotency provider bila kelak diintegrasikan.
2. Keamanan operasional: MFA Superadmin, audit trail perubahan yang immutable, pemisahan pembuat/pemeriksa untuk perubahan rekening dan limit, serta recovery drill backup.
3. Lifecycle client: pengingat MoU 30/14/7 hari, histori perubahan kontrak, onboarding checklist, admin pengganti, dan status onboarding tanpa otomatis memblokir transaksi dari tanggal MoU saja.
4. Risiko dan pendanaan: dashboard outstanding dan aging per PT, batas eksposur yang disetujui per client, serta pemantauan kewajiban penggantian dana.
5. Unit economics: margin bersih per client setelah biaya transfer, pajak yang berlaku, pendanaan, dan support; hindari menilai kesehatan bisnis hanya dari jumlah transaksi/fee bruto.
6. Kualitas layanan: ukur waktu pengajuan -> approval -> dana diterima, kegagalan pencairan, keluhan, dan waktu penyelesaian. Tetapkan target setelah ada baseline data nyata.
7. Pengembangan: pertahankan regresi/CI, tambahkan dependency lock dan observability, pagination katalog, serta pecah web.py per domain secara bertahap.

Kebijakan pajak, kontrak, perlindungan data, retensi, dan perizinan produk harus ditinjau bersama pihak profesional terkait;
perbaikan teknis ini tidak menyatakan pemenuhan kepatuhan tersebut.
