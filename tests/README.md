# Regresi sebelum push ke VPS

Jalankan dari root repository:

```sh
python -m pytest -q
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

Workflow `.github/workflows/tests.yml` menjalankan suite pada Python 3.12 saat push/PR.
Hasil workflow baru dapat dilihat setelah perubahan dipush; workflow belum dijalankan
di GitHub selama sesi lokal ini. Uji MariaDB paralel tetap verifikasi integrasi terpisah.
