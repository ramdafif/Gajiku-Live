# Hasil audit dan perbaikan - 16 Oktober 2026

Repo ini adalah staging lokal sebelum push ke VPS. Perubahan tetap berada di
working tree; belum di-commit, dipush, atau dideploy oleh sesi ini.
Dokumen ini merangkum pekerjaan sebelumnya yang dilanjutkan dan verifikasi ulangnya.
Ini bukan pernyataan bahwa seluruh kemungkinan bug atau risiko sudah hilang.

## Cakupan yang ditelusuri

- Login, sesi, status akun, role, CSRF, registrasi/verifikasi email, reset dan ganti password.
- Cakupan admin antar-PT, pegawai, client/project, hak approval, aturan Windu,
  dan approval final oleh Superadmin.
- Plafon REG/URG, siklus gaji/cutoff, nominal/fee, double submit, transaksi paralel,
  cancel vs approval, polling, riwayat, dashboard/cache, dan export.
- Import Excel, CSV formula injection, avatar, schema/migrasi SQLite dan MariaDB,
  entry point deployment, worker/queue, scheduler, konfigurasi, dan penanganan error.
- Render halaman/JavaScript, asset lokal, dependensi yang terpasang, dan jalur rilis.

## Perbaikan yang ada dan lulus uji ulang

1. Akses antar-PT diperiksa pada pembacaan data dan perubahan pegawai/transaksi;
   admin tanpa perusahaan tidak mendapat akses global sebagai fallback.
2. Role, perusahaan, status admin, dan hak approval disegarkan dari database.
   Sesi lama gugur setelah kredensial berubah. Akun nonaktif tidak bisa mengajukan.
3. Pengajuan memakai lock dan request key unik. Transaksi pending ikut menggunakan
   plafon; double submit tidak membuat transaksi baru. Cancel dan approval diserialisasi.
4. Reset password membutuhkan tautan email sekali pakai. Registrasi membutuhkan
   konfirmasi email. Akun DB nonaktif tidak bisa melewati pemeriksaan melalui akun ENV.
5. Reset massal, endpoint percobaan, seed contoh, dan override tanggal dikendalikan
   konfigurasi. Admin PT tidak boleh mengubah produk/pajak global.
6. Pegawai dengan histori transaksi tidak dihapus permanen melalui UI. Perlindungan
   Superadmin aktif terakhir dan approver Windu dengan pending request tersedia.
7. Upload/import dibatasi dan divalidasi, avatar di-reencode, nilai CSV berisiko
   diperlakukan sebagai teks, dan error internal tidak ditampilkan mentah di UI.
8. Migrasi dijalankan idempotent, startup paralel dan scheduler export memakai lock,
   pemilihan queue membaca konfigurasi environment, dan entry point Vercel diperbaiki.

## Tambahan yang diperbaiki pada lanjutan sesi ini

- **Pembulatan uang:** perkalian float 1.11 dapat membuat fee dasar Rp10.000 menjadi
  Rp11.101, bukan Rp11.100. Server dan preview/invoice sekarang memakai aritmetika
  integer dengan kebijakan pembulatan ke atas yang sama. Angka 11% mengikuti konfigurasi
  produk yang sudah ada, bukan penetapan atau penilaian kewajiban pajak baru.
- **Gagal membaca pajak:** error database saat membaca status PPN tidak lagi diam-diam
  membuat transaksi tanpa pajak. Request gagal dan transaksi tidak ditulis.
- **Periode export:** pemilihan bulan memakai tanggal Jakarta, bukan zona waktu OS;
  diuji pada pergantian bulan dan tahun ketika tanggal host berbeda.
- **Race password:** penggantian password pegawai, Admin, dan Superadmin memakai
  compare-and-swap agar request lama tidak menimpa password yang baru diganti.
- **Race verifikasi:** token diperiksa ulang dan dikunci bersama data pegawai sebelum
  akun dibuat. Token yang kedaluwarsa/diganti saat request diproses tidak diterima lagi.
- **Halaman token:** reset/verifikasi mengirim no-store dan no-referrer agar halaman
  sensitif tidak disimpan cache atau meneruskan URL token sebagai referrer.
- **Config test:** DB_PATH eksplisit pada test config tidak ditimpa environment.
- **XML Excel:** defusedxml dimasukkan ke requirements agar pengaman XML bukan sekadar
  kebetulan tersedia di komputer developer. Payload entity diuji ditolak tanpa import.
- **Regresi:** harness sementara dipindah menjadi suite permanen di tests/ dan
  workflow GitHub Actions ditambahkan. pytest hanya mengoleksi direktori tests/.

## Bukti pengujian

- `python -m pytest -q`: **53 passed, 3 subtests passed** pada Python 3.12.10.
  Termasuk reproduksi token lama yang gagal sebelum perbaikannya dan lolos sesudahnya.
- MariaDB 10.4.32 sementara: migrasi otomatis/manual berulang, startup read-only
  setelah migrasi, startup paralel, halaman admin, cakupan export, approval berlapis,
  PPN, plafon paralel, cancel vs approval, dan scheduler ganda lulus.
- Tambahan MariaDB: fee Rp10.000 tersimpan Rp11.100, token verifikasi yang diganti
  ditolak, dan dua verifikasi bersamaan hanya menghasilkan satu akun.
- Snapshot uji tetap 218 pegawai, 38 perusahaan induk kosong, dan 63 project kosong.
  Pengujian menggunakan salinan dump untuk memeriksa pelestarian data, bukan mengisi
  perusahaan/project kosong secara otomatis.
- Render ulang 15 halaman untuk tiga role: 33 inline script lolos kompilasi JavaScript
  dan asset lokal yang dirujuk tersedia.
- Preview fee JavaScript lolos 200.002 pembandingan integer untuk PPN aktif/nonaktif.
  Ini uji perhitungan/kompilasi, bukan pengganti uji interaksi browser.
- Verifikasi browser sesi sebelumnya mencakup desktop 1440px dan mobile 390px.
- `node --check` pada kedua script aplikasi dan `python -m pip check` lulus.
- Workflow GitHub Actions baru disiapkan; hasil CI remote belum tersedia sebelum push.

## Risiko yang masih perlu ditangani

- **Pencairan bukan integrasi bank.** Status sukses masih dicatat manual oleh
  Superadmin. Keberhasilan transfer, bukti, referensi, dan rekonsiliasi belum menjadi
  jaminan otomatis aplikasi. Jangan menafsirkan approval sebagai instruksi payout.
- **Jejak finansial belum ledger immutable.** Perubahan master pegawai dapat mengubah
  label perusahaan/nama pada laporan historis yang masih menggunakan join master.
  Rencanakan snapshot atribut transaksi, audit log perubahan/rejection, dan ledger
  sebelum memperluas integrasi pembayaran. Jangan memindahkan pegawai antar-PT tanpa
  memeriksa transaksi/penagihan yang masih outstanding.
- **Email belum exactly-once.** Queue memory dapat kehilangan job saat proses berhenti.
  Lock mencegah scheduler berjalan bersamaan, tetapi crash setelah SMTP berhasil
  sebelum marker commit masih dapat mengirim email ulang. Butuh outbox/retry durable.
- **Dependency belum dikunci.** pip check memastikan kompatibilitas metadata paket yang
  terpasang, bukan audit CVE atau reproduksi versi pada VPS. Tambahkan lock/constraints
  dari environment rilis yang disetujui dan pemindaian dependensi berkala.
- Avatar legacy masih terlacak di Git. Tidak dihapus karena merupakan data existing;
  tinjau akses repo dan prosedur pembersihan histori secara terpisah.
- SMTP nyata, Redis worker nyata, reverse proxy/TLS, process manager, backup/restore,
  dan transfer bank belum diuji melalui infrastruktur VPS pada sesi ini.
- Background thread tidak menggantikan service terkelola. Jika memakai Gunicorn
  preload/multiple worker, pastikan scheduler/worker benar-benar hidup sesudah fork;
  lebih baik jadwalkan export sebagai satu service terpisah.

## Prioritas bisnis yang disarankan

### Prioritas 1 - Kepercayaan dan kontrol dana

Pisahkan status persetujuan, pencairan, gagal transfer, dan rekonsiliasi. Wajibkan
bukti/referensi transfer, tampilkan status yang tidak menyesatkan pengguna, dan buat
laporan selisih harian antara transaksi aplikasi, mutasi bank, serta tagihan ke PT.
Tambahkan kontrol pembuat/pemeriksa untuk perubahan rekening dan limit yang berisiko.

### Prioritas 2 - Risiko client dan pendanaan

Pantau outstanding, aging, tanggal payroll, eksposur, dan keterlambatan settlement
per PT. Usulkan limit client sesuai kontrak dan kapasitas pendanaan; perubahan limit
atau pembekuan transaksi perlu persetujuan kebijakan bisnis, bukan aturan otomatis
baru yang disisipkan saat memperbaiki bug.

### Prioritas 3 - Margin dan pengalaman pengguna

Hitung kontribusi bersih per PT dari pendapatan fee dikurangi biaya transfer,
pendanaan, pajak sesuai kebijakan, dan support. Ukur waktu pengajuan sampai dana
diterima, rasio gagal pencairan, keluhan, dan penyelesaiannya. Gunakan baseline nyata
sebelum menjanjikan SLA atau memperluas promo; tidak ada proyeksi omzet buatan di audit ini.

### Prioritas 4 - Operasional yang bisa diskalakan

Tambahkan pengingat MoU, onboarding/offboarding admin, admin pengganti, MFA akun
istimewa, outbox email, pemantauan gagal job, dan latihan pemulihan backup. Pertahankan
regresi sebelum push, lalu pecah web.py per domain secara bertahap. Penerapan aturan
kontrak/pajak/perizinan/retensi perlu pemeriksaan pihak profesional yang relevan.

## Rilis ke VPS

1. Jalankan `python -m pytest -q`, tinjau diff, dan pilih file perubahan untuk commit.
   Jangan ikutkan .env, key, dump DB, data pengguna, atau isi tmp/.
2. Backup database dan jalankan dua migrasi di migrasidb/ sesuai DeploymentSecurity.md.
3. Install requirements pada virtualenv VPS, lalu restart service aplikasi/worker
   dengan konfigurasi yang sesuai setup VPS yang sudah digunakan.
4. Smoke test login, Settings/Kembali, satu alur admin antar-PT, pengajuan sampai
   finalisasi, email reset/registrasi, dan export. Pantau log/error setelah rilis.

Detail environment dan langkah migrasi tersedia di `.md/DeploymentSecurity.md`.
