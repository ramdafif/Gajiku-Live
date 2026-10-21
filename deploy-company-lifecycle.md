# Penagihan perusahaan dan pusat notifikasi

Implementasi berada di Redesign. Dokumen dan unit systemd adalah persiapan deploy,
bukan bukti fitur sudah terpasang di production. Jangan menjalankan perintah di
VPS sebelum backup dan verifikasi konfigurasi release.

Tutorial operasional setelah `git pull` ada di `deploy-vps-after-pull.md`.

## Perilaku

- Superadmin mengatur tanggal bulanan melalui detail perusahaan, bagian Penagihan.
  Perusahaan tanpa konfigurasi tidak diblokir. Jatuh tempo pertama adalah tanggal
  yang belum lewat, tanpa backfill tunggakan sebelum pengaturan dibuat.
- Periode tagihan adalah bulan sebelum bulan jatuh tempo. Tanggal 10 November 2026
  menagih periode Oktober 2026. Peringatan tanggal 7-10; blokir mulai tanggal 11
  pukul 00.00 WIB. Seluruh perhitungan menggunakan WIB, bukan jam host atau simulasi.
- Tanggal 29-31 dipotong ke akhir bulan. Jadwal baru hanya memengaruhi tagihan yang
  belum tercatat; tagihan yang ditampilkan mempertahankan jatuh tempo aslinya.
- Tandai Lunas hanya mencatat satu periode, beserta identitas pencatat/waktu UTC.
  Pembayaran tepat waktu mencegah blokir. Pembayaran setelah lewat batas tetap
  membutuhkan Aktifkan Kembali secara manual setelah seluruh tunggakan lunas.
- Satu aktivasi perusahaan membuka akses semua karyawan yang terblokir tagihan,
  termasuk project berbeda dan karyawan baru. Status pribadi pegawai/akun tidak
  ditulis ulang; pegawai yang memang sudah nonaktif tetap tidak dapat mengajukan.
  Login, riwayat, dan penyelesaian pengajuan lama tetap tersedia.
- Pengingat MoU H-30 hanya untuk superadmin. Abaikan berlaku bersama untuk periode
  yang sama. Perpanjangan harus menyimpan tanggal valid dan akhir yang lebih panjang.
  Pembaruan periode dari Edit perusahaan juga mengakhiri pengingat periode lama.
- Riwayat notifikasi dimulai sejak fitur aktif, bukan rekonstruksi flash/email lama.
  Notifikasi transaksi gaji tidak berubah. Status baca terpisah per akun; membaca
  tidak menyelesaikan masalah. Notifikasi yang sudah ditindaklanjuti tetap ada di
  riwayat, dan dapat tetap belum dibaca oleh akun lain.

## Migrasi dan peluncuran

1. Backup DB, uji restore, dan pastikan katalog `clients`/`perusahaan_induk` benar.
2. Uji `migrasidb/migrasi_company_lifecycle.sql` pada salinan MySQL staging dahulu.
   SQL hanya membuat lima tabel/index baru, tidak mengubah status pegawai. Startup
   aplikasi juga memastikan skema ini secara idempoten di SQLite dan MySQL.
3. Siapkan release dengan `APP_SECRET`, konfigurasi DB, dan izin yang sudah benar.
   Jangan memakai DB live untuk menjalankan fixture/test.
4. Sesuaikan User, Group, WorkingDirectory, EnvironmentFile, dan ExecStart pada
   `deploy/gajiku-company-lifecycle.service` dengan jalur release/venv VPS sebenarnya.
   `/opt/gajiku` adalah contoh, bukan hasil deteksi VPS. Simpan secret di file ENV
   dengan izin terbatas, bukan di unit atau repository. Pertahankan prefix
   `/usr/bin/env START_INLINE_WORKER=0 EXPORT_CSV_ENABLED=0` pada ExecStart;
   EnvironmentFile systemd dapat menimpa nilai Environment pada unit.
5. Uji runner pada staging dengan worker/email export dimatikan:

   ```sh
   START_INLINE_WORKER=0 EXPORT_CSV_ENABLED=0 python -m flask --app wsgi:app sync-company-lifecycle
   ```

6. Setelah cutover release disetujui, pasang service/timer ke systemd, lalu:

   ```sh
   sudo systemctl daemon-reload
   sudo systemctl enable --now gajiku-company-lifecycle.timer
   systemctl list-timers gajiku-company-lifecycle.timer
   journalctl -u gajiku-company-lifecycle.service --since '10 minutes ago'
   ```

7. Atur jadwal satu perusahaan uji, verifikasi tanggal tagihan pertama, kedua role,
   badge belum dibaca, dan alur lunas/aktivasi sebelum mengatur perusahaan lainnya.
   Tidak perlu mengedit status aktif setiap pegawai.

Runner tidak membuat thread di Gunicorn dan dapat dijalankan ulang. Lock baris
perusahaan/MySQL atau transaksi SQLite serta unique event/period menjaga retry dan
eksekusi bersamaan. GET halaman management menyinkronkan perusahaan yang berhak
dilihat sebagai catch-up; tidak menciptakan notifikasi harian yang sudah terlewat.
Runner membuat periode tagihan yang terlewat agar tunggakan tidak hilang.

Pemblokiran pengajuan dihitung lagi dari DB dan waktu WIB di server, termasuk tepat
sebelum INSERT transaksi. Jadi scheduler terlambat tidak menjadi celah tarik gaji.

## Monitoring dan pemulihan

- Pantau exit code service dan log `[COMPANY]`. Keberhasilan runner terakhir disimpan
  sebagai UTC pada `app_settings.company_lifecycle_last_success`. Alert jika tidak
  ada sukses selama lebih dari lima menit saat timer aktif.
- Jika runner gagal, perbaiki koneksi/izin DB lalu jalankan ulang; jangan mengubah
  waktu tagihan atau status pegawai untuk mengakali blokir.
- Mematikan timer tidak membuka blokir dan bukan rollback fitur. Jangan rollback ke
  kode lama ketika ada konfigurasi penagihan aktif tanpa koordinasi operasional,
  karena kode lama tidak menegakkan aturan ini. Tabel tambahan dapat dipertahankan.
- Migrasi, pemasangan timer, dan pengubahan perusahaan production belum dijalankan
  dalam implementasi lokal ini.

## Pengujian

```sh
python -m pytest -q
node --test tests/theme.test.cjs
node --test tests/notifications.test.cjs
```

Fixture SQLite bersifat sementara dan tidak menghubungi MySQL/SMTP. Bila direktori
temporary pytest Windows tidak dapat diakses, gunakan `--basetemp` dengan direktori
baru/unik di dalam `tmp/`; pytest dapat menghapus isi basetemp, jadi jangan menunjuk
folder kerja atau direktori yang sudah berisi data. Jalur MySQL telah diuji pada
MariaDB lokal terisolasi, termasuk pengulangan ketiga migrasi. Verifikasi versi
MySQL production, systemd, SMTP, dan Nginx tetap perlu dilakukan di staging VPS.
