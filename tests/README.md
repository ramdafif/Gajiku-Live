# Regresi sebelum push ke VPS

Jalankan dari root repository:

```sh
python -m pytest -q
node --test tests/theme.test.cjs
node --test tests/notifications.test.cjs
```

Suite membuat fixture SQLite di direktori temporary dan menonaktifkan worker/export
background. Data pegawai, transaksi, token, dan kredensial di suite adalah data sintetis.
Test config memilih DB sendiri, termasuk ketika DB_PATH tersedia di environment.
Tidak membutuhkan koneksi MySQL atau SMTP. Bukan pengganti smoke test konfigurasi VPS.

Cakupan: halaman admin/pegawai, cache, isolasi antar-PT, CSRF, role/status akun,
approval berlapis dan Windu, plafon/request paralel, cancel, idempotency, registrasi,
reset/password, CSV, Excel, avatar, schema legacy, dan entry point deployment.
Tambahan audit mencakup pembulatan fee, error pembacaan PPN, pergantian bulan Jakarta,
header halaman token, config DB pengujian, race password/verifikasi, dan payload XML Excel.

Tes tema memakai Node.js 22 tanpa npm/dependensi tambahan. Cakupannya meliputi
preferensi device, pemuatan awal, toggle, penyimpanan manual, perubahan tema device,
sinkronisasi tab, dan storage tidak tersedia. Regresi Flask memeriksa toggle navbar
seluruh role, akses bantuan sidebar, serta pewarisan tema di halaman autentikasi.

Workflow `.github/workflows/tests.yml` menjalankan suite pada Python 3.12 dan Node.js 22 saat push/PR.
Hasil workflow baru dapat dilihat setelah perubahan dipush; workflow belum dijalankan
di GitHub selama sesi lokal ini. Uji MariaDB paralel tetap verifikasi integrasi terpisah.

`test_company_lifecycle.py` menambahkan fixture penagihan/MoU terisolasi: batas WIB,
akhir bulan/tahun kabisat, tunggakan lintas periode, pembayaran awal/terlambat,
aktivasi massal tanpa mengubah status pribadi, sesi/form lama, approval lama,
isolasi tenant, CSRF, status baca per akun, pagination, serta retry runner/CLI.
Tes polling notifikasi menggunakan Node.js tanpa dependency tambahan.
Petunjuk pemasangan timer dan monitoring ada di `deploy-company-lifecycle.md`.

`test_period_filters.py` memeriksa default bulan kalender berjalan (WIB) pada riwayat
semua role, rekap biaya admin, dan formulir/hasil ekspor. Termasuk pemilihan periode
historis yang eksplisit, reset/parameter kosong atau tidak valid, cache hangat,
siklus mingguan/lintas bulan, pergantian bulan/tahun, dan Februari tahun kabisat.
