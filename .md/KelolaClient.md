# Kelola Client Global

Satu client adalah satu PT. Superadmin mendaftarkan perusahaan di Setting > Kelola Client,
mengisi PIC dan MoU opsional, lalu menentukan Allow Projects sesuai kesepakatan MoU.
Akun admin tetap dibuat/diubah melalui Kelola Admin dan hanya dapat ditautkan ke client terdaftar.
Password tidak ditampilkan di detail client. Nama PT existing dikunci untuk menjaga relasi data legacy.

## Alur operasional

- Allow Projects: project harus terdaftar pada PT sebelum karyawan baru ditambahkan.
- Admin PT memiliki menu Kelola Projects jika diizinkan; perusahaan akun terkunci di backend.
- Superadmin mengelola daftar project yang sama melalui detail client.
- Disallow Projects: karyawan baru hanya memiliki perusahaan, dengan project kosong.
- Project yang dipakai karyawan, termasuk nonaktif, tidak dapat dihapus atau diganti namanya.
- PIC, lokasi, dan keterangan project tetap dapat diperbarui.
- Project baru juga memakai ejaan baku untuk nama yang disepakati, misalnya Cakra, PTPI, dan INP.
- Disallow diblokir jika project masih dipakai pegawai; pemindahan data harus diselesaikan secara eksplisit.
- Status MoU dihitung berdasarkan tanggal Jakarta; tidak memblokir akun atau transaksi otomatis.
- Kebijakan approval transaksi tidak diganti dengan pengaturan project.

## Migrasi dan deployment

Backup database sebelum deploy. Startup aplikasi membuat tabel clients dan client_projects,
lalu menjalankan migrasi satu kali dalam transaksi DML. Marker app_settings.clients_catalog_v1
mencegah normalisasi dan seed dijalankan kembali pada restart berikutnya.

Jika akun aplikasi tidak memiliki hak CREATE TABLE, jalankan migrasidb/migrasi_kelola_client.sql
di database target sebelum startup. Script SQL dan bootstrap menggunakan marker yang sama;
jangan menghapus marker atau memakai script Guixu lama sebagai migrasi berulang.
DDL MySQL dapat melakukan implicit commit; transaksi melindungi perubahan data, bukan pembuatan tabel.

Migrasi hanya mengubah kolom pegawai.perusahaan dan pegawai.perusahaan_induk yang disetujui:

- Project Guixu menjadi PT Guixu.
- Project Springhill/SPRING HILL menjadi PT Springhill dan project Springhill.
- Cakra/cakra menjadi Cakra; PTPI/Ptpi/ptpi menjadi PTPI.
- Inp/INP/inp menjadi INP; Sii/SII menjadi SII.
- Pmr bitung/PMR Bitung menjadi PMR Bitung.
- Budidharma/budidharma menjadi Budidharma; Paul/paul menjadi Paul.
- Fleminh/Fleming menjadi Fleming.
- Paul buana dan Pnr cilegon tidak digabung atau diganti.

PT kosong lainnya dan project kosong tetap apa adanya. Tidak ada inferensi dari email,
akun pegawai, nama project lain, atau arsip. Project tanpa PT tidak otomatis menjadi client baru.
Tabel windu_projects lama hanya dibaca untuk kompatibilitas migrasi, tidak lagi dipakai fitur operasional
dan tidak dihapus otomatis. Dump sumber gajiku_db.sql tidak ditulis ulang.

Client awal diambil dari nama perusahaan admin dan perusahaan_induk pegawai yang nonkosong.
Client dengan project existing diberi Allow Projects untuk mempertahankan struktur lama,
disertai catatan agar Superadmin memverifikasi penggunaannya sesuai MoU.
Client tanpa project existing memakai Disallow Projects. PIC dan MoU tidak ditebak dari akun admin.

Pada dump 16 Oktober 2026 yang dianalisis, hasil migrasi yang diharapkan:
218 karyawan tetap ada; GMI 160, Springhill 11, Guixu 8, Elkyno 1, tanpa PT 38;
63 karyawan tetap tanpa project. Data arsip, nominal, rekening, akun, dan transaksi tidak diubah.

## Tambah/edit karyawan dan import

Karyawan baru wajib memakai client terdaftar. Project harus milik PT tersebut dan sesuai flag Allow Projects.
Aturan yang sama dipakai form manual, endpoint backend, dan import Excel.

Template Excel sekarang memiliki 14 kolom: E adalah Project, N adalah Perusahaan.
Superadmin wajib mengisi N; Admin PT boleh mengosongkan N atau mengisi PT akunnya sendiri.
Nilai PT lain ditolak. Dropdown project Superadmin memuat semua project; backend tetap memvalidasi pemilik PT.
Template lama tanpa kolom N tidak cukup untuk import Superadmin. Unduh ulang template dari Kelola Pegawai.

Saat edit data legacy, penugasan PT/project yang tidak berubah dipertahankan, termasuk nilai kosong.
Mengubah nama/gaji tidak otomatis melengkapi perusahaan atau project. Jika penugasan diubah,
aturan master dan penggunaan project diberlakukan. Admin PT tidak dapat mengedit pegawai PT lain.

## Hardening setelah audit

Rilis ini juga memakai migrasidb/migrasi_security_hardening.sql. Lihat .md/DeploymentSecurity.md
untuk ENV wajib, verifikasi email, pembatasan reset, antrean, dan checklist sebelum cutover.

## Verifikasi deployment

1. Periksa jumlah pegawai serta PT/project kosong sebelum dan sesudah migrasi.
2. Login Superadmin: buka Kelola Client, periksa empat client awal dan koreksi Springhill/Guixu.
3. Periksa Allow Projects masing-masing PT sesuai MoU, bukan hanya berdasarkan data lama.
4. Buat client/project baru sebelum menambah karyawan; client baru harus berfungsi meski belum ada karyawan.
5. Login Admin PT: perusahaan terkunci dan project PT lain tidak dapat dipilih atau diubah melalui request manual.
6. Uji PT tanpa project, edit pegawai legacy kosong, dan import menggunakan template terbaru.
7. Uji dashboard, hak approval, dan laporan pada MySQL staging sebelum deploy produksi.
