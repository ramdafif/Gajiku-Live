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

`test_deployment_config.py` memeriksa timer satu menit dan memastikan runner
memaksa worker/export background mati lewat `/usr/bin/env`, termasuk ketika
EnvironmentFile systemd berisi nilai yang mengaktifkannya.

## Integrasi MySQL/MariaDB terisolasi

Suite default tetap memakai SQLite. Untuk mengulang pengujian lifecycle dan
periode pada MySQL/MariaDB, siapkan instance lokal BARU dengan datadir di `tmp/`
repository dan port acak 10000-65535. Jangan memakai service/DB aplikasi existing.
Manifest JSON di `tmp/` harus berisi `data` (path absolut datadir) dan `port`.
Set `GAJIKU_MYSQL_FIXTURE_PASSWORD` ke password root instance sintetis tersebut,
kemudian jalankan:

```sh
python -m pytest -q tests/test_company_lifecycle.py tests/test_period_filters.py tests/test_deployment_config.py tests/test_mysql_migrations.py --mysql-fixture-instance tmp/NAMA-INSTANCE/instance.json
```

Fixture hanya menghubungi loopback, memverifikasi `@@datadir` sama dengan manifest,
lalu membuat/menghapus database bernama `gajiku_fixture_<UUID>` miliknya sendiri.
Skema dasar berisi definisi sintetis, bukan dump pengguna. Ketiga skrip migrasi
dijalankan sebelum startup app. Tes retry migrasi memastikan catatan lunas,
konfigurasi, notifikasi, dan status baca tidak hilang/terduplikasi. Subprocess CLI
`wsgi:app sync-company-lifecycle` juga diuji dengan ENV database fixture.
Tanpa argumen MySQL tersebut, dua tes khusus MySQL ditandai skip secara sengaja.
