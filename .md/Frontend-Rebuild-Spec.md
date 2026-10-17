# GajiKu Frontend Rebuild Specification

Acuan: source repository pada 16 Oktober 2026 (Asia/Jakarta). Ini inventaris source, bukan jaminan bahwa fitur sama dengan deployment production; verifikasi source terbaru sebelum mulai.

## Tujuan dan batasan

Rebuild visual frontend dengan fitur, akses role, alur, validasi, dan hasil yang sama. Desain, struktur komponen, responsivitas, dan aksesibilitas boleh dimodernisasi; jangan menghapus fitur atau mengubah aturan bisnis.

- Source saat ini memakai Flask + Jinja (server-rendered), HTML/CSS/JS dalam templates dan static. Jangan mengasumsikan SPA atau API JSON; cek route dan response terlebih dahulu.
- Pertahankan URL, HTTP method, nama field, query/form parameter, CSRF, session/role, redirect, error/success state, dan unduhan yang sudah dipakai source.
- Backend adalah sumber kebenaran untuk limit, fee, PPN, cutoff/siklus, status, scope perusahaan, approval, validasi dan izin. UI bukan kontrol keamanan.
- Approval final mencatat status aplikasi; tidak otomatis mengirim uang ke bank. Jangan tampilkan klaim payout berhasil tanpa bukti eksternal.

## Role

1. Pegawai: dashboard personal, pengajuan REG/URG bila aktif, riwayat sendiri, kelola akun.
2. Admin perusahaan: dashboard dan data dalam scope perusahaan, riwayat, approval tahap pertama bila hak approval aktif, project hanya bila diizinkan.
3. Superadmin: akses lintas perusahaan, approval final, pengelolaan client/admin/pegawai, laporan, ekspor, dan pengaturan sistem.

Tampilkan menu sesuai role, tetapi backend tetap harus memverifikasi izin pada setiap route.

## Autentikasi umum

- Login: logo/brand, email, password, toggle visibility, submit, tautan lupa password dan daftar. Sediakan validasi, loading dan error aman; arahkan ke dashboard sesuai role backend.
- Registrasi pegawai: nama, email, password + konfirmasi (min. 6 karakter). Jelaskan email harus terdaftar di master pegawai dan perlu verifikasi. Tampilkan sukses/pending/error; status akun ditentukan server.
- Verifikasi registrasi: tampilkan email tujuan dan aksi token; tangani sukses, token salah atau kedaluwarsa.
- Lupa password: input email, kirim tautan, kembali ke login. Reset: password baru + konfirmasi, min. 6 karakter; source menyebut token 30 menit dan sekali pakai, ikuti backend aktual.

## Pegawai

### Dashboard
- Header nama, perusahaan, waktu/tanggal, avatar unggah/hapus.
- KPI: plafon bulanan/mingguan, limit harian, hari ke siklus, total tarikan sukses, saldo REG, sisa plafon URG bila aktif.
- Data pegawai/rekening: ID, data rekening tersedia, skema gaji atau cutoff mingguan.
- Aksi Tarik Gaji, menu riwayat dan ubah password, logout.
- Daftar transaksi terbaru dengan produk, nominal, biaya/status. Tampilkan batal + countdown untuk on-process hanya jika server mengizinkan; bedakan status sukses/dibatalkan dengan teks.

### Pengajuan tarik gaji
- Pilih produk REG/URG aktif; nominal; rekening tujuan dari opsi server; validasi dan preview fee. Tampilkan batas REG dan saldo/limit dari backend.
- Sebelum kirim, tampilkan invoice berisi produk, nominal, rekening, admin fee dan biaya lain/PPN bila berlaku; aksi kembali atau konfirmasi.
- Cegah submit ganda dan tampilkan loading. Validasi final tetap di server. Pertahankan field/parameter aktual pada template dan route.

### Riwayat dan akun
- Riwayat enam periode terakhir sesuai pilihan server: filter periode, tanggal, periode, nominal, admin fee, status, total; empty state dan kembali.
- Ganti password: password lama, baru, konfirmasi (min. 6 karakter), validasi dan feedback.

## Admin perusahaan

### Dashboard dan approval
- Header akun/avatar/waktu; menu Settings, Kelola Pegawai, Riwayat Transaksi; Kelola Projects hanya bila diizinkan; logout.
- KPI, grafik bulanan, cohort/rekap yang tersedia, jumlah transaksi, cutoff mingguan, transaksi terbaru.
- Antrean REG dan URG (bila aktif) dengan pegawai, perusahaan, produk, nominal, rekening, waktu, status dan tahap approval.
- Tampilkan approve/reject hanya untuk hak approval dan transaksi dalam scope. Minta konfirmasi. Approval admin adalah tahap pertama; transaksi dapat tetap menunggu superadmin dan belum berstatus sukses.

### Kelola pegawai/project
- Tabel ID, nama, email, telepon, jabatan, perusahaan, gaji, status, siklus/cutoff dan aksi sesuai backend; admin fee dapat ditampilkan ke superadmin.
- Cari ID/nama/email/rekening, filter perusahaan sesuai izin, total, empty state.
- Tambah/edit master: identitas/kontak, jabatan, gaji, perusahaan/project, rekening, status, skema A/B/C/D atau cutoff; field fee hanya bila diizinkan.
- Admin hanya untuk perusahaan sendiri; project harus milik PT itu. Hormati Allow/Disallow Projects dan data legacy kosong.
- Impor Excel .xlsx, template unduhan, unggah, hasil/error backend; validasi sama dengan input manual.
- Project (jika punya hak): daftar dan tambah/edit/hapus project milik perusahaan; field PIC/lokasi/keterangan bila ada. Jangan paksa hapus project yang masih dipakai pegawai.

### Riwayat/pengaturan admin
- Filter transaksi menurut tanggal/periode, status, produk, dan perusahaan dalam scope; perusahaan terkunci bila server membatasi.
- Tabel tanggal, periode, ID/nama pegawai, perusahaan, produk, nominal, status, rekening, keterangan; total sukses bila tersedia.
- Ubah password lama/baru/konfirmasi. Akun environment hanya menampilkan keterangan password dikelola deployment.
- Force time limit hanya bagi role yang memang berhak, dengan konfirmasi.

## Superadmin

### Dashboard/approval final
- Menu Settings, Kelola Client, Kelola Admin, Kelola Pegawai, Riwayat, Product, Export CSV, logout.
- Ringkasan lintas perusahaan: KPI/nominal/transaksi, antrean on-process, cutoff, grafik bulanan, cohort/repeat/rate, rekap admin fee, transaksi terbaru/sebelumnya sesuai source.
- Pertahankan filter waktu/perusahaan yang tersedia. Antrean final mencakup approval admin dan jalur bypass sesuai kebijakan backend.
- Approve/reject hanya pada state valid; konfirmasi wajib. Tegaskan approve final mengubah status aplikasi, bukan instruksi transfer bank.

### Client, project, admin
- Client: cari nama PT/PIC/email admin, filter status MoU; tampilkan perusahaan/PIC, admin, project/karyawan, status MoU, detail/edit.
- Detail client: data perusahaan, konfigurasi MoU Allow/Disallow Projects, PIC dan field source; daftar admin dengan status/hak approval.
- Project per client: tambah/edit/hapus, validasi hubungan client-project; lindungi project yang digunakan pegawai.
- Admin: cari/filter perusahaan; tabel identitas, kontak, status, hak approval, cutoff, produk dan aksi.
- Tambah/edit admin: nama/email, perusahaan dari client terdaftar, password saat dibuat, status aktif, hak approval, cutoff/hari, produk REG/URG sesuai server. Hapus/nonaktifkan dengan konfirmasi; jangan tampilkan password tersimpan. Tampilkan penolakan backend untuk aturan coverage/transaksi berjalan.

### Pegawai, riwayat, ekspor, settings
- Kelola pegawai lintas perusahaan dan impor; tampilkan kolom tambahan yang disediakan backend.
- Riwayat lintas perusahaan: cari ID/nama/email/jabatan/perusahaan; filter perusahaan, project, produk, status, rentang tanggal; detail dan total.
- Export CSV custom: tanggal mulai/akhir, perusahaan/semua, siklus A/B/C/D atau cutoff mingguan. Validasi dan unduh file server; pertahankan export lain bila tersedia.
- Product: toggle REG/URG dan PPN admin fee sesuai backend; setelah simpan tampilkan nilai server.
- Settings: ubah password non-environment, PPN, force time limit/opsi global hanya untuk akun berhak. Jelaskan kredensial environment dikelola deployment.

## Komponen bersama

- Format Rupiah dan tanggal Indonesia konsisten.
- Badge teks untuk status, produk, status pegawai, siklus dan cutoff; jangan hanya pakai warna.
- Semua form memiliki label, validasi/error, loading, success/empty state; cegah submit ganda pada aksi finansial.
- Modal: fokus terjaga, tombol close/Escape, konfirmasi approval/delete/perubahan global.
- Responsif desktop/tablet/ponsel; tabel lebar dapat di-scroll atau jadi kartu tanpa kehilangan informasi; modal tidak melebihi viewport.
- Semantic HTML, keyboard navigation, focus visible, label/aria untuk ikon, kontras dan reduced motion.
- Pertahankan identitas GajiKu serta informasi legal/confidential yang diwajibkan; styling boleh diperbarui.

## Alur wajib

1. Login menentukan dashboard berdasarkan role.
2. Registrasi dicocokkan dengan master pegawai dan email; status datang dari backend.
3. Pegawai melihat limit server, memilih produk/rekening, mengecek invoice, lalu mengonfirmasi.
4. Approval admin hanya bila kebijakan/hak meminta; setelah itu finalisasi superadmin atau bypass sesuai backend.
5. Approve/reject/cancel hanya tersedia pada state dan scope yang valid.
6. Approval final bukan payout otomatis.
7. Semua data, filter, ekspor, mutasi pegawai/admin/client/project dan settings memakai backend aktual.

## Acceptance checklist

- [ ] Tiga role mendapat halaman/menu sesuai izin; backend melindungi route.
- [ ] Login, registrasi/verifikasi, lupa/reset password beserta semua state tersedia.
- [ ] Pegawai: KPI/rekening, REG/URG, invoice, riwayat, cancel bersyarat, avatar/password.
- [ ] Admin: dashboard/grafik, approval bersyarat, pegawai/import, project bersyarat, riwayat/password.
- [ ] Superadmin: dashboard/final approval, client/project/MoU, admin/hak approval, pegawai, riwayat, product/PPN, export/settings.
- [ ] Scope, limit, fee, status, cutoff, siklus, produk dan hak akses berasal dari server.
- [ ] Field, route, method, CSRF, query, dan unduhan kompatibel dengan kontrak aktual.
- [ ] Responsif dan accessible; uji per role di staging/dummy data sebelum produksi.
- [ ] Tidak ada data dummy, payout palsu, atau sukses sebelum konfirmasi backend.

## Prompt siap pakai

> Baca .md/Frontend-Rebuild-Spec.md dan source frontend/backend aktual terlebih dahulu. Bangun ulang frontend GajiKu dengan mempertahankan seluruh fitur, role, alur, field, filter, validasi, state, route, metode HTTP, CSRF, dan kontrak backend. Jangan mengubah aturan bisnis atau mengarang API. Pertahankan autentikasi; dashboard pegawai/admin/superadmin; pengajuan REG/URG dengan invoice; riwayat; avatar; manajemen pegawai dan impor Excel; approval bertahap; client/project/admin; laporan dan export CSV; product/PPN dan settings. Semua limit, fee, status, cutoff, scope dan izin berasal dari backend. Approval final bukan transfer uang otomatis. Buat UI responsif/accessibility dengan loading/empty/error/success dan konfirmasi tindakan berisiko. Inventarisasi route/field sebelum implementasi, lalu laporkan fitur yang dipertahankan dan hasil uji per role. Jangan hapus atau sederhanakan fitur tanpa persetujuan.
