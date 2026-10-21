# Cutover pegawai, plafon, dan approval

Jangan langsung restart produksi setelah pull tanpa maintenance dan migrasi di bawah.
Tidak ada perintah dokumen ini yang perlu dijalankan pada database produksi untuk pengujian lokal.

## Perubahan perilaku

- Gaji aktual nullable; plafon nominal/persentase disimpan terpisah.
- Admin PT: tambah/impor, semua gaji/plafon, aktivasi kembali dan siklus menunggu superadmin.
- Data pribadi/rekening langsung berlaku dan menghasilkan notifikasi; transaksi existing tidak diganti tujuannya.
- Nonaktifkan langsung berlaku dan membatalkan usulan terkait. Hapus hanya tanpa riwayat transaksi, dengan arsip.
- Fee kosong mengikuti PT. Angka 0 merupakan pengecualian gratis. Fee hanya dapat diubah superadmin.
- Cutoff mingguan membatasi 25% per minggu sekaligus plafon bulanan total. Tidak ada akumulasi sisa minggu.
- Minggu yang melintasi periode bulanan tetap satu jatah minggu. Reguler tetap mengikuti akumulasi hari minggu tersebut.
- Tunggakan periode sebelumnya mengunci periode baru, tanpa menunggu jatuh tempo. Pelunasan memerlukan aktivasi manual.
- Kelima endpoint reset massal dihapus. Reset daftar diganti label Hapus filter; reset password tidak dihapus.

## Sebelum cutover

1. Uji versi ini di staging dengan salinan database yang sudah disanitasi.
2. Hentikan traffic penarikan, CRUD pegawai, worker lama dan timer lifecycle selama cutover. Pastikan tidak ada instance versi lama yang masih menulis.
3. Ambil backup lengkap dan verifikasi restore di database terpisah. Simpan konfigurasi MoU, periode, dan daftar tagihan.
4. Pastikan user migrasi memiliki izin ALTER/CREATE. Startup menambah schema; konversi nilai gaji hanya melalui CLI eksplisit.
5. Gunakan interpreter virtualenv aplikasi. Semua perintah berikut dari root aplikasi dengan environment database tujuan yang benar.

```bash
START_INLINE_WORKER=0 EXPORT_CSV_ENABLED=0 python -m flask --app wsgi:app migrate-employee-finance
```

Default adalah dry-run. Periksa total konversi, ID akun yang belum terhubung dan tagihan legacy.
Relasi ambigu/akun tanpa pegawai harus direkonsiliasi berdasarkan identitas sebenarnya, bukan dihapus atau ditebak.
Periksa pula pegawai tanpa perusahaan terdaftar; akses keuangan mereka ditutup sampai relasinya benar.

```bash
START_INLINE_WORKER=0 EXPORT_CSV_ENABLED=0 python -m flask --app wsgi:app migrate-employee-finance --apply
```

Konversi satu kali: plafon = floor(gaji lama / 2), gaji pokok menjadi NULL.
Nilai fee 15000 menjadi ikuti PT; fee lain tetap pengecualian. Akun users dihubungkan ke ID pegawai stabil.
Transaksi historis tidak diubah. Marker mencegah pemotongan dua kali pada pengulangan perintah.

## Konfigurasi MoU dan tagihan

1. Pada detail perusahaan, superadmin mengonfirmasi tanggal awal plafon dan default fee sesuai MoU.
2. Atur jatuh tempo secara terpisah. Tanggal 29-31 dijepit ke hari terakhir bulan tanpa mengubah konfigurasi aslinya.
3. Konfirmasi PT mingguan wajib sebelum akses plafon tersedia. Perubahan awal periode/cutoff existing baru berlaku setelah periode berjalan selesai.
4. Tagihan lama tidak otomatis diubah maknanya. Jika rentangnya belum dipetakan atau beririsan, sistem menutup akses dan menunjukkan ID tagihan yang perlu direkonsiliasi.

Contoh berikut hanya ilustrasi; gunakan ID dan rentang layanan hasil pemeriksaan MoU/tagihan sebenarnya.
`service-end` adalah tanggal akhir eksklusif: 16 Oktober sampai sebelum 16 November berarti 16 Oktober-15 November.

```bash
START_INLINE_WORKER=0 EXPORT_CSV_ENABLED=0 python -m flask --app wsgi:app map-legacy-bill --bill-id 123 --service-start 2026-10-16 --service-end 2026-11-16
START_INLINE_WORKER=0 EXPORT_CSV_ENABLED=0 python -m flask --app wsgi:app map-legacy-bill --bill-id 123 --service-start 2026-10-16 --service-end 2026-11-16 --apply
```

Pemetaan hanya menambahkan rentang layanan; identitas periode lama, tanggal jatuh tempo dan pencatatan pelunasan tetap.
Tidak boleh ada dua tagihan yang beririsan. Tanggal jatuh tempo tagihan baru adalah kesempatan pertama tanggal yang dipilih setelah akhir layanan.
Rentang periode baru tampil pada riwayat tagihan. Pengingat tetap mengikuti jatuh tempo, tetapi akses mengikuti batas periode.

## Verifikasi sebelum membuka traffic

- Cocokkan plafon lama efektif dengan plafon hasil konversi, termasuk jatah mingguan 25%.
- Periksa gaji kosong tidak menghalangi plafon nominal, dan mode persentase menolak gaji kosong.
- Uji satu Admin PT: perubahan nama langsung, kenaikan/penurunan gaji tertunda, impor menjadi batch, fee ditolak.
- Uji approval/penolakan parsial dan hasil notifikasi, serta bahwa akses PT lain ditolak.
- Cocokkan sisa bulanan/mingguan, transaksi sedang diproses, minggu kelima dan minggu lintas periode.
- Periksa tunggakan, pelunasan dan aktivasi kembali. Aktivasi PT tidak mengaktifkan pegawai yang nonaktif pribadi.
- Jalankan pengujian pada fixture, bukan produksi: `python -m pytest -q --basetemp=tmp/pytest-finance-UNIQUE`.
- Restart seluruh instance pada versi baru, hidupkan timer/worker yang dibutuhkan, lalu buka traffic.

## Rollback

Jangan menjalankan aplikasi versi lama terhadap data yang sudah dikonversi: gaji lama sudah NULL dan versi lama tidak mengenal approval/plafon baru.
Jika rollback diperlukan, tutup traffic dan hentikan seluruh penulis, lalu restore pasangan kode serta backup database sebelum cutover.
Jika sudah ada aktivitas baru setelah cutover, rekonsiliasi aktivitas tersebut dahulu; restore buta akan menghilangkan transaksi baru.
