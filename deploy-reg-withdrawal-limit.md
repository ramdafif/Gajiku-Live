# Limit penarikan reguler per perusahaan

## Deployment

1. Backup database dan pastikan migrasi katalog perusahaan (`migrasidb/migrasi_kelola_client.sql`) sudah diterapkan.
2. Pada database MySQL production, jalankan `migrasidb/migrasi_reg_withdrawal_max.sql` sebelum restart aplikasi. Script aman dijalankan ulang dan tidak menimpa limit yang sudah diatur.
3. Deploy kode dan restart aplikasi dengan prosedur deployment yang biasa. Startup juga memeriksa kolom ini; SQLite lama diperbarui otomatis.
4. Login sebagai superadmin, buka Kelola Perusahaan, pilih perusahaan, lalu Edit perusahaan. Atur Maksimal penarikan reguler per transaksi (Rp).
5. Verifikasi form penarikan karyawan perusahaan tersebut menampilkan batas yang sesuai. Perusahaan lain tetap memakai Rp1.000.000 kecuali diubah.

## Perilaku

- Perusahaan lama dan baru memiliki default Rp1.000.000. Isi kembali 1000000 untuk mengembalikan default.
- Nominal wajib rupiah bulat positif tanpa pemisah ribuan, maksimal Rp2.147.483.647.
- Limit berlaku untuk semua karyawan berdasarkan perusahaan induk, termasuk seluruh project-nya. Karyawan tanpa perusahaan yang cocok memakai default.
- Batas pengajuan reguler adalah nilai terkecil antara saldo reguler tersedia dan limit perusahaan. Plafon, biaya admin, dan urgent tidak berubah.
- Setiap pengajuan membaca setting terbaru, termasuk pengajuan dari form yang dibuka sebelum setting berubah. Transaksi yang sudah diajukan tidak diubah.
- Jika rollback kode diperlukan, biarkan kolom tambahan tetap ada; jangan menghapus data atau menjalankan ulang migrasi penggantian database.
