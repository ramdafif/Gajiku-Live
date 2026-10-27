# Panduan Testing: Limit REG Perusahaan dan Override Karyawan

## 1. Ruang lingkup dan before/after

Urutan pemeriksaan yang disarankan: migrasi mingguan, batas perusahaan, override,
izin/stale form, cutoff atomik, lalu regresi pengaturan/ekspor/UI.

- Sebelum: cutoff/produk perusahaan dibaca dari akun admin. Sesudah: dibaca dari
  `clients`; tambah/edit/pindah/hapus admin tidak mengubah aturan perusahaan.
- Sebelum: semua pegawai hanya memakai batas REG PT. Sesudah: default tetap PT,
  dengan `pegawai.reg_withdrawal_max_override=NULL`; batas khusus hanya dari
  Kelola Pegawai Superadmin. Override bukan plafon total dan boleh melebihi PT.
- Sebelum: editor profil dan batas REG bercampur. Sesudah: penyimpanan profil,
  batas REG, cutoff, dan produk PT terpisah; versi lama ditolak HTTP 409.
- Sebelum: produk/PPN berada di beberapa halaman. Sesudah: Pengaturan Sistem
  khusus Superadmin, simpan produk dan PPN terpisah. Pengaturan Akun untuk
  profil/keamanan. Force time limit dipensiunkan, bukan diaktifkan diam-diam.
- Sebelum: ekspor bisa berbeda dari tabel. Sesudah: Ekspor hasil filter memakai
  query/cakupan yang sama dengan riwayat; Ekspor kustom tetap formulir tersendiri.
  URL CSV/Excel lama tetap tersedia.
- ID transaksi riwayat pegawai sekarang terisi. Total nominal/biaya diberi label
  hanya transaksi selesai; jumlah transaksi tetap mencakup semua status.
- KPI siklus bulanan efektif tidak menghitung pegawai mingguan dua kali. Siklus
  bulanan setiap pegawai tetap tersimpan sebagai fallback, bukan ditulis ulang.
- Dialog yang gagal validasi menampilkan kembali input dan pesan error di dialog.
  Draft hanya ada dalam respons HTML/memori, bukan localStorage/cookie.
- Status pegawai tetap Diproses/Menunggu persetujuan. Approval, URG, rumus biaya,
  PPN 11%, dan transaksi yang sudah diajukan tidak diubah retroaktif.

## 2. Persiapan wajib

Gunakan data sintetis lokal/staging. Jangan melakukan percobaan penarikan atau
migrasi pada data produksi melalui panduan ini tanpa cutover yang disetujui.

Siapkan Superadmin, Admin PT A/B, dan minimal empat pegawai PT A:
A mengikuti PT, B untuk override Rp500.000, C override Rp1.500.000 lintas project,
D nonaktif dengan override. Tambahkan pegawai PT B untuk uji isolasi tenant.
Gunakan gaji/saldo sintetis cukup besar; contoh gaji Rp16 juta menghasilkan
plafon mingguan Rp2 juta, tetapi saldo harian tetap bergantung tanggal dan pemakaian.

Catat tanggal pengujian dalam WIB. Jangan mengubah tanggal server produksi.
Pengujian batas kalender otomatis memakai tanggal tetap, termasuk tahun kabisat.

Sebelum menjalankan versi baru di clone staging:

1. Hentikan web/worker/scheduler versi lama dan blokir pengajuan sementara.
2. Backup schema, `clients`, `admins`, `pegawai`, `users`, `transactions`, dan
   `app_settings`. Catat jumlah transaksi, SUM nominal/biaya per status serta
   snapshot seluruh baris transaksi agar perubahan historis dapat dideteksi.
3. Catat per pegawai mingguan: cutoff, siklus bulanan tersimpan, kunci periode,
   plafon, akumulasi harian, pemakaian sukses+pending, saldo, sisa plafon.
4. Inventaris seluruh admin PT, termasuk yang nonaktif dan konfigurasi berbeda.
   Migrasi meniru runtime lama: cutoff dari admin ber-ID terbesar yang cutoff-nya
   aktif, produk dari admin ber-ID terbesar. Ini dua resolver legacy yang berbeda,
   bukan memilih satu admin sembarang. Hari invalid mengikuti fallback lama Rabu.
5. Pastikan tabel MySQL transaksional/InnoDB. Jalankan bootstrap pertama hanya
   melalui satu proses maintenance; DDL MySQL tidak menjamin rollback penuh.

## 3. Migrasi dan gate kesetaraan mingguan

Bootstrap aplikasi menjalankan migrasi additive sekali dengan marker
`company_policy_v1`. Kolom legacy admin dipertahankan untuk recovery tetapi bukan
sumber kebijakan aktif. Audit `company_policy_events` menyimpan konfigurasi legacy,
batas PT awal, hasil resolusi, actor, dan perubahan berikutnya.

Tidak ada UPDATE/INSERT terhadap transaksi historis dalam migrasi. Override
existing mulai NULL ketika kolom baru ditambahkan. Bootstrap berikutnya tidak
menyalin ulang aturan admin atau mereset override.

Set konfigurasi proses ke **clone staging yang benar**, bukan database default
produksi. Verifier menggunakan konfigurasi Flask yang sama dan bootstrap dapat
menjalankan migrasi; periksa `DB_TYPE`, `DB_PATH` atau seluruh `MYSQL_*` dahulu.
Matikan worker/scheduler untuk proses maintenance:

```powershell
$env:START_INLINE_WORKER = '0'
$env:EXPORT_CSV_ENABLED = '0'
python -B -m flask --app app:create_app verify-company-policy --at-date 2026-10-26 --days 10
python -B -m flask --app app:create_app verify-company-policy --at-date 2026-12-28 --days 14
```

Ekspektasi: exit code 0, JSON `passed=true`, `checks` lebih dari nol,
`mismatches=[]`. Verifier membandingkan seluruh hasil `compute_limits` dengan
resolver cutoff legacy dan resolver perusahaan pada tanggal yang sama, termasuk
pegawai bulanan sebagai kontrol. Jalankan **sebelum** kebijakan cutoff diedit atau
admin dipindah/dihapus, saat kolom legacy masih menggambarkan baseline cutover.
Verifier tidak mengubah transaksi. Akun tanpa pasangan `users` tidak memiliki
perhitungan keuangan untuk dibandingkan; jumlah akun diperlihatkan dalam laporan.

Sesudah migrasi, bandingkan juga snapshot transaksi dan angka yang dicatat di
langkah persiapan. Jika ada selisih cutoff/periode/angka, STOP: jangan buka layanan.
Tidak boleh memperbaiki selisih dengan menghitung ulang atau menulis ulang transaksi.

## 4. UI batas default perusahaan

1. Login Superadmin > Kelola perusahaan > detail PT A.
2. Pada Kebijakan perusahaan, set Maksimal penarikan reguler per transaksi menjadi
   1000000; klik **Simpan batas REG**. Awalnya tertulis semua karyawan mengikuti PT.
3. Login pegawai A > Dashboard dan Tarik gaji. Maksimal sekali pengajuan adalah
   minimum saldo REG tersedia dan Rp1 juta; tombol Maksimal/preset/preview sama.
4. Ubah PT ke 750000. Pegawai A langsung mengikuti Rp750.000, tanpa edit pegawai.
5. Bandingkan cutoff, periode, akumulasi, pemakaian, saldo, serta sisa plafon sebelum
   dan sesudah. Semuanya tetap; hanya cap sekali pengajuan REG berubah.
6. Edit profil PT/PIC/MoU, simpan. Batas REG/cutoff/produk tidak ikut berubah.
7. Coba 0, negatif, desimal, pemisah ribuan, huruf, dan angka >2147483647.
   Server menolak; tidak ada perubahan tersimpan. Input memakai rupiah bulat positif.

Catatan: simpan tiap bagian terpisah. Mengisi bagian lain tetapi hanya menekan
Simpan batas REG tidak akan menyimpan bagian lain tersebut.

## 5. UI override dan indikator tanda !

Menu tetap bernama **Kelola pegawai**; route lama `/admin/pegawai` dipakai bersama
oleh Admin dan Superadmin, tetapi kontrol override hanya muncul bagi Superadmin.

1. Superadmin > Kelola pegawai > Tambah/Edit pegawai B. Pilih perusahaan PT A.
   Default **Ikuti perusahaan**, batas PT read-only. Ganti ke **Batas khusus karyawan**,
   isi 500000; simpan. Daftar/form menampilkan sumber dan batas efektif.
2. Set pegawai C 1500000. Override boleh lebih besar dari PT, saldo tetap membatasi.
3. Buka PT A: dekat input REG muncul **! 2 karyawan menggunakan batas khusus**.
   Klik/Enter/Space untuk membuka panel di form yang sama. Periksa nama, ID, project,
   nominal, dan status. Panel tidak mempunyai editor override kedua.
4. Ubah default PT ke 750000. A mengikuti; B tetap 500000, C tetap 1500000.
5. Set batas khusus B 750000 sama dengan default. B **tetap dihitung override**.
6. Ubah B ke Ikuti perusahaan; simpan. Jumlah turun satu. Ubah PT lagi, B mengikuti.
7. Tambahkan override D nonaktif di project berbeda. D tetap dihitung, statusnya
   terlihat. Pegawai PT B tidak boleh muncul. Uji >10 override agar hitungan tidak
   tergantung halaman tabel/pagination. Jumlah berdasarkan nilai non-NULL tersimpan.
8. Klik tautan panel: masuk Kelola pegawai dengan filter PT A (`client=...`),
   bukan filter project (`company=...`). Edit override hanya di halaman ini.
9. Tambah pegawai tanpa override; pastikan langsung mengikuti PT. POST/form lama
   yang tidak membawa kedua field limit mempertahankan override existing saat edit.
   Pemilihan Ikuti perusahaan adalah reset yang eksplisit.

## 6. Cutoff dan transaksi paralel

1. Dengan pengajuan `on-proses` milik PT A, termasuk pegawai nonaktif dan pending
   tanggal lama, coba ubah bulanan/mingguan atau hari cutoff aktif. Harus ditolak.
2. Selesaikan/bersihkan pending sintetis. Dengan transaksi sukses di salah satu
   rentang periode lama **atau** baru, perubahan tetap ditolak. Uji REG dan URG.
3. Transaksi PT B tidak boleh menghalangi perubahan PT A.
4. Bila tidak ada transaksi terdampak, perubahan cutoff boleh tersimpan. Siklus
   bulanan setiap pegawai masih sama. Hari berbeda saat kedua skema bulanan tidak
   memicu guard karena belum mengubah cutoff efektif.
5. Pending/sukses tidak menghalangi edit cap REG atau profil PT. Mengirim ulang
   konfigurasi cutoff yang efektifnya sama tidak membuat periode baru.
6. Uji dua koneksi/session pada MySQL staging: kirim pengajuan bersamaan dengan
   simpan cutoff. Jika pengajuan lebih dulu, cutoff harus ditolak; jika cutoff
   lebih dulu, pengajuan harus memakai kebijakan/periode baru. Tidak boleh ada
   transaksi di sela pemeriksaan dan penyimpanan cutoff.
7. Coba dua pengajuan paralel dengan saldo terbatas. Total pemakaian tidak boleh
   melebihi saldo/plafon; retry token sama tidak membuat transaksi ganda.

Implementasi memakai mutex kebijakan bersama (row lock MySQL / BEGIN IMMEDIATE
SQLite), kemudian lock pegawai saat penarikan. Lock lebih luas sengaja dipakai
untuk menjaga race saat pegawai dipindah perusahaan atau override diperbarui.
Ukur latensi pengajuan paralel di staging; jangan menjalankan kode lama bersamaan
karena kode lama tidak mengambil mutex ini.

## 7. Izin, stale form, dan batas terbaru

- Admin PT tidak melihat kontrol override, tidak bisa mengedit batas lewat POST
  buatan/impor, tidak bisa mengubah policy PT/global, dan tidak melihat tenant lain.
- Pegawai tidak dapat mengakses editor perusahaan/karyawan/pengaturan global.
- Buka detail PT pada dua tab. Simpan tab pertama; simpan tab kedua harus ditolak
  HTTP 409 dan meminta muat ulang, bukan menimpa konfigurasi terbaru.
- Ulangi stale form pada Pengaturan Sistem. Simpan produk tidak mengubah PPN dan
  sebaliknya. URL form produk/PPN lama tidak menyimpan setting secara diam-diam.
- Buka Tarik gaji, lalu turunkan PT/override dari sesi Superadmin. Submit nominal
  dari form lama di atas batas terbaru: ditolak dan preview dimuat ulang.
- Coba saldo Rp300 ribu dengan batas Rp1,5 juta: maksimal tetap Rp300 ribu.
  Pengurangan limit tidak merevisi nominal/biaya transaksi yang telah diajukan.

## 8. Impor

Unduh template dari Kelola pegawai. Urutan 14 kolom pertama tetap; kolom O opsional
**Batas REG khusus**. Angka positif berarti override, `IKUTI_PT` berarti mengikuti PT.
Kosong pada pegawai baru berarti mengikuti PT. Template 14 kolom yang sebelumnya
valid tetap diterima; pembatasan perusahaan pada template lama tetap berlaku.
Impor existing bersifat tambah, bukan upsert: ID/email existing ditolak, bukan
menghapus override existing. Admin PT tidak boleh mengisi kolom limit, termasuk
penanda reset. Uji beberapa baris valid/tidak valid dan laporan hasil per baris.

## 9. Regresi UI dan ekspor

1. Admin dan Superadmin > Riwayat: terapkan rentang tanggal, pencarian, PT/project,
   produk, status. Unduh Ekspor hasil filter sebagai Excel dan CSV; ID/jumlah baris
   harus tepat sama dengan semua hasil tabel, tidak hanya halaman pagination aktif.
2. Ekspor kustom adalah formulir terpisah dengan pilihan cakupannya sendiri.
   Uji URL legacy: `/admin/export` dan `/superadmin/export_range`, bulan, all,
   siklus bulanan dan cutoff mingguan; isolasi tenant tetap enforced server.
3. Periksa rekening awalan nol, tanggal/angka Excel, karakter Indonesia, teks
   menyerupai formula, hasil kosong, dan nominal/biaya. Total selesai hanya dari
   status sukses; jumlah transaksi mencakup seluruh status hasil filter.
4. Pegawai > Riwayat: ID terisi dan benar. Status tetap generik, tidak membocorkan
   pembedaan tahap admin/superadmin. Uji approve/reject, cancel, Windu, dan URG.
5. Kelola admin: tambah/edit/pindah/hapus akun tidak mengubah cutoff/produk PT.
   Perlindungan admin terakhir dan kewajiban approval Windu tetap berlaku.
6. Gagalkan validasi dialog pegawai/admin/PT/project, contoh email/ID/nama project
   duplikat. Dialog dibuka kembali dengan pesan error terlihat serta input aman
   dipertahankan. Password harus diisi ulang; file impor harus dipilih ulang.
7. Desktop dan mobile 390px: panel !, scroll tabel, semua form, keyboard/Tab,
   Enter/Space, fokus dialog, mode gelap/terang. Jangan bergantung hover/warna saja.
   Pastikan rekening/gaji tidak ditulis ke localStorage/cookie. Preferensi tema
   yang memang sudah memakai penyimpanan browser bukan draft data pegawai.

## 10. Pengujian otomatis lokal

```powershell
$testTemp = Join-Path $env:TEMP ('gajiku-policy-' + [guid]::NewGuid().ToString('N'))
python -B -m pytest -q -p no:cacheprovider --basetemp "$testTemp" --tb=short tests
node --test tests/withdrawal.test.cjs tests/theme.test.cjs tests/notifications.test.cjs
```

Suite memakai DB sementara, bukan `.env` produksi. Tes tambahan mencakup paritas
migrasi, semua hari cutoff, akhir bulan/tahun/kabisat, nullable inheritance, izin,
indikator override termasuk nominal sama/nonaktif, impor lama/baru, stale form,
cap tanpa perubahan cutoff, guard, ekspor bersama, dan pengajuan/cutoff paralel.

## 11. MySQL staging: gate terpisah yang wajib dijalankan

Tes InnoDB opt-in belum bisa dibuktikan hanya dengan suite SQLite. Pakai server
MySQL test khusus dan akun yang boleh CREATE/DROP database sintetis. Jangan beri
kredensial produksi. Fixture membuat nama acak `gajiku_policy_test_<uuid>` dan
menghapus hanya database yang dibuatnya sendiri, tidak menerima nama database PT.

```powershell
$env:GAJIKU_POLICY_MYSQL_TEST = '1'
$env:GAJIKU_MYSQL_TEST_HOST = '127.0.0.1'
$env:GAJIKU_MYSQL_TEST_PORT = '3306'
$env:GAJIKU_MYSQL_TEST_USER = 'dedicated_policy_test_user'
# Isi GAJIKU_MYSQL_TEST_PASSWORD lewat secret environment, bukan commit/file repo.
python -B -m pytest -q -p no:cacheprovider tests/test_company_policy_mysql.py
```

Tanpa opt-in, dua tes ini skipped; itu **bukan lulus gate MySQL**. Tes meliputi DDL
migrasi, paritas keuangan dan history, inheritance, serta row lock antar koneksi.
Setelah itu tetap lakukan smoke UI/race pada clone staging skema aplikasi lengkap,
verifier pada pegawai existing, dan perbandingan snapshot sebelum/sesudah.

## 12. Checklist cutover dan recovery

Rilis hanya setelah paritas pegawai mingguan existing pada clone staging lulus,
tes MySQL/race lulus, indikator benar, dan hasil UI ditandatangani penanggung jawab.

- Simpan backup, JSON verifier, snapshot policy/history, dan hasil tes sebagai bukti.
- Stop versi lama, lakukan bootstrap additive satu proses, cek marker/events,
  jalankan verifier, baru start seluruh web/worker/scheduler versi baru.
- Jangan menghapus marker untuk mengulang migrasi setelah ada perubahan kebijakan.
  Jangan drop kolom legacy sampai periode observasi/recovery selesai.
- Jika gagal sebelum ada write operasional baru: hentikan versi baru, periksa log,
  pulihkan backup staging/cutover sesuai prosedur. Jangan mengubah history agar
  angka terlihat cocok dan jangan membuka layanan dengan hasil verifier berbeda.
- Jika sudah ada write versi baru: rollback aplikasi saja tidak aman; legacy admin
  sudah tidak disinkronkan. Stop layanan, rekonsiliasi event policy dan transaksi,
  lakukan forward-fix atau prosedur recovery tervalidasi. Backup lama tidak boleh
  direstore begitu saja karena akan menghilangkan transaksi baru.

Status bukti lokal dan gate staging harus dilaporkan terpisah. Keberhasilan tes
sintetis bukan klaim bahwa database produksi atau clone MySQL existing sudah diuji.


## 13. Regresi filter Perusahaan dan Project

Label yang terlihat di semua UI adalah **Perusahaan**. Nama kolom/form internal
`perusahaan_induk` tetap dipertahankan untuk kompatibilitas database dan impor.

1. Login Superadmin, buka Kelola Pegawai tanpa filter: pastikan semua PT muncul.
   Pilih PT A, klik Terapkan: tabel dan jumlah hasil hanya berisi pegawai PT A,
   termasuk nonaktif. Ulangi PT B, lalu Reset untuk kembali ke semua pegawai.
2. Gabungkan Perusahaan, Project, dan pencarian nama/ID/rekening. Semua kondisi
   berlaku bersamaan. Tautan Kelola Pegawai pada panel override perusahaan
   harus membuka tabel yang benar-benar terfilter ke PT tersebut.
3. Pilih Project A lalu ganti Perusahaan ke PT B: pilihan Project langsung
   kembali ke Semua project dan opsinya hanya project milik PT B. Tabel baru
   berubah setelah Terapkan. Uji dengan klik, sentuhan, dan keyboard.
4. Buka URL dengan kombinasi PT A dan project PT B: hasil harus kosong, bukan
   seluruh perusahaan. Pilihan project yang tidak sesuai tetap ditampilkan
   dengan keterangan di luar pilihan perusahaan. Nama perusahaan URL dengan
   spasi/huruf besar-kecil berbeda tetap dikenali. PT yang tidak dikenal tidak
   boleh menampilkan seluruh pegawai atau ringkasan global.
5. Ulangi alur pergantian Perusahaan/Project di Riwayat Transaksi, termasuk
   Ekspor hasil filter CSV/Excel. Filter riwayat dan ekspor harus tetap identik.
6. Di Dashboard Superadmin, pilih PT A lalu PT B dan Semua perusahaan dengan
   cepat untuk menguji isolasi cache. Master pegawai, akun, nominal, biaya,
   grafik, siklus bulanan/mingguan, dan rekap biaya harus sesuai PT terpilih.
   Bulan rekap yang sudah dipilih tetap tersimpan saat berganti perusahaan.
   Antrean approval dan aktivitas terbaru sengaja tetap lintas perusahaan;
   informasinya dijelaskan di UI. Aturan approval tidak berubah.
7. Kelola Admin: filter Perusahaan dan pencarian bekerja bersamaan. Konfigurasi
   kebijakan hanya ada di Kelola Perusahaan, tidak mengikuti akun admin.
8. Login Admin PT A lalu ubah URL secara manual agar memuat PT B. Tabel,
   riwayat, ekspor, dan opsi project tidak boleh membocorkan data di luar
   cakupan akun. Uji juga admin legacy dengan cakupan project serta admin
   tanpa perusahaan. Pegawai tidak memperoleh hak filter lintas tenant.
9. Periksa desktop dan mobile: label Perusahaan konsisten pada filter dan
   dialog tambah/edit; tidak ada istilah lama yang membedakan perusahaan
   seolah merupakan editor kebijakan lain.


## 14. Bukti validasi lokal (27 Oktober 2026)

- Seluruh suite Python: **268 passed, 2 skipped, 54 subtests passed**.
  Ada 727 warning deprecation converter tanggal SQLite existing.
- Suite JavaScript withdrawal/theme/notifications: **47 passed**.
- Sintaks `static/app.js` dan pemeriksaan whitespace diff lulus.
- Chrome headless desktop 1440 px dan mobile 390 px: filter Perusahaan benar-benar
  mengubah tabel, opsi Project mengikuti PT, project lama direset saat ganti PT,
  URL filter yang tidak cocok tetap kosong dan terlihat jelas, serta filter
  riwayat/ringkasan dashboard lulus. Tidak ditemukan overflow halaman.
- Smoke awal diulang pada kode final: panel override dapat dibuka lewat Enter,
  mode inherit/override dan nominal benar, label mingguan tampil, dialog gagal
  validasi terbuka dengan input serta error tersimpan dalam respons, dan halaman
  Pengaturan Sistem tampil. Tidak ada exception JavaScript dalam pengujian ini.
- Regresi otomatis mencakup paritas perhitungan mingguan pada tanggal yang sama,
  perlindungan transaksi existing, pewarisan/override, tenant scope, isolasi cache,
  cohort/tren per PT, project pegawai nonaktif/legacy, dan tautan panel override.

Semua bukti ini menggunakan database sintetis terisolasi, **bukan produksi**.
Dua tes yang skipped adalah integrasi MySQL opt-in; validasi clone MySQL existing,
paritas seluruh pegawai aktual, dan race pada staging tetap **belum dijalankan**.
Gate rilis pada bagian 11-12 masih wajib dipenuhi sebelum deployment produksi.
