# Audit Menyeluruh — Gajiku-Live

Audit ini mencakup seluruh `app/routes/web.py` (3864 baris), `app/__init__.py`, `app/config.py`,
`app/db.py`, seluruh template, `app/services/email_service.py`, script maintenance di `app/`, dan
struktur project secara umum. Tujuannya: jadi referensi tunggal untuk perbaikan ke depan, dari sisi
developer (keamanan, kualitas kode, kestabilan) maupun sisi bisnis (integritas finansial, kepatuhan,
operasional).

**Belum ditelusuri detail** (di luar cakupan audit ini): `app/tasks/export_scheduler.py` dan
`app/migrasi_local.py` — keduanya bukan jalur yang dipakai user/admin secara langsung, jadi
prioritasnya lebih rendah, tapi sebaiknya di-review juga di putaran berikutnya.

**Catatan asal-usul**: hampir semua temuan di sini **sudah ada sebelum sesi perbaikan Gajiku-Live yang
terakhir** (fee manual, approval 2 layer, PT Windu Karya, invoice tarik gaji, dropdown project) — ditandai
`[PRE-EXISTING]`. Yang berkaitan langsung dengan pekerjaan sesi terakhir ditandai `[SESI TERAKHIR]`.

---

## Ringkasan Prioritas

| # | Temuan | Severity | Area |
|---|--------|----------|------|
| 1 | Tanggal simulasi dikontrol client → limit tarik gaji harian bisa dilewati | 🔴 Kritis | Bisnis/Finansial |
| 2 | Lupa password: reset tanpa verifikasi + pecah di SQLite | 🔴 Kritis | Keamanan |
| 3 | Kredensial default & akun admin lintas-PT tertanam di kode | 🔴 Kritis | Keamanan |
| 4 | Race condition (TOCTOU) saat membuat transaksi tarik gaji | 🔴 Kritis | Bisnis/Finansial |
| 5 | Admin "Non-Aktif" tetap bisa login penuh | 🔴 Kritis | Keamanan/Operasional |
| 6 | Hapus/nonaktifkan admin PT Windu Karya bisa mengunci approval selamanya | 🟠 Tinggi | Operasional |
| 7 | Tidak ada rate limiting di mana pun | 🟠 Tinggi | Keamanan |
| 8 | IDOR ringan di `/api/tx_status` | 🟠 Tinggi | Keamanan |
| 9 | Session tidak di-hardening (Secure/SameSite/timeout) | 🟠 Tinggi | Keamanan |
| 10 | `requirements.txt` tanpa pin versi | 🟠 Tinggi | Developer/Ops |
| 11 | `NOW()` MySQL-only membuat 2 alur pecah total di SQLite | 🟡 Sedang | Developer |
| 12 | Tidak ada batas ukuran upload avatar | 🟡 Sedang | Keamanan |
| 13 | Kode sisa testing nyangkut di jalur produksi | 🟡 Sedang | Developer |
| 14 | Nol automated test walau `pytest` jadi dependency | 🟡 Sedang | Developer |
| 15 | `web.py` monolit 3.864 baris | 🟡 Sedang | Developer/Bisnis |
| 16 | Pesan error mentah bocor ke UI Superadmin | 🟡 Sedang | Keamanan (rendah) |
| 17 | Kolom `urg_lock_until` tidak pernah dipakai (fitur belum selesai) | 🟢 Rendah | Bisnis |
| 18 | Script maintenance CLI hardcode ke SQLite, tak jalan di production | 🟢 Rendah | Ops |
| — | Gap pengujian & catatan pekerjaan sesi terakhir | — | Developer |

---

## 🔴 KRITIS

### 1. Tanggal simulasi dikontrol client → limit tarik gaji harian bisa dilewati `[PRE-EXISTING]`
**Lokasi:** `app/routes/web.py`, fungsi `current_sim_date()` (±baris 522), dipakai di `compute_limits()`
(±baris 140–160) dan route `tarik_gaji` POST (±baris 1160–1171).

```python
t = request.args.get("tanggal") or session.get("sim_date") or date.today().isoformat()
```

Nilai ini dipakai langsung sebagai "hari ini" untuk menghitung `hari_ke` (hari keberapa dalam siklus
gaji), yang menentukan saldo REG yang bisa ditarik:
`saldo = limit_harian * hari_ke - total_sukses`.

**Tidak ada validasi** bahwa `tanggal` yang dikirim tidak boleh melebihi tanggal server sungguhan
(`date.today()`). Pegawai yang login cukup membuka `/dashboard?tanggal=<tanggal-akhir-siklus>` untuk
membuat sistem mengira sudah di hari terakhir siklus gaji, sehingga limit harian terkali penuh dan bisa
mengajukan tarik gaji sebesar limit **satu bulan penuh** di hari pertama siklus.

Komentar di kode (baris ±1171) secara eksplisit menyebut "ambil acuan bulan/tahun dari AT_DATE simulasi,
**bukan** kalender real server" — kelihatan seperti fitur simulasi/demo yang kebawa ke jalur produksi.

**Mitigasi yang sudah ada (parsial):** dana baru benar-benar cair setelah 2 admin approve (bukan
otomatis), jadi bukan langsung hilang — tapi tidak ada indikator apa pun di layar approval yang memberi
tahu admin bahwa nominal ini janggal dibanding limit harian pegawai yang sebenarnya, jadi risikonya
bergantung penuh pada kejelian manual admin.

**Rekomendasi:**
- Hapus kemampuan override tanggal dari `request.args`/`request.form` di jalur produksi, atau
- Kalau memang dibutuhkan untuk keperluan testing/demo, kunci di belakang flag env eksplisit
  (`ALLOW_DATE_OVERRIDE=1`) yang **default mati**, dan validasi `at_date <= date.today()` selalu,
  bahkan saat flag itu aktif.
- Tambahkan indikator anomali di layar approval admin (mis. badge merah kalau nominal > limit harian
  wajar berdasarkan tanggal kalender asli).

### 2. Lupa Password: reset tanpa verifikasi + pecah di SQLite `[PRE-EXISTING]`
**Lokasi:** `app/routes/web.py`, fungsi `forgot_password()` (±baris 782).

- Cukup kirim `email` lewat POST → password akun **langsung diganti** dengan password acak dan
  dikirim via email — tidak ada link/token reset, tidak ada verifikasi kepemilikan email, tidak ada
  rate limit. Siapa pun yang tahu email seorang pegawai bisa memicu reset ini **berulang kali kapan
  saja**, secara efektif mengunci pemilik akun asli dari akunnya sendiri (DoS berulang), meski
  penyerang sendiri tidak bisa membaca password baru itu (karena dikirim ke email pemilik).
- Route ini juga membangun query dengan placeholder gaya MySQL (`%s`) secara langsung, padahal semua
  query lain di codebase pakai `?` yang di-translate otomatis oleh `MySQLConnectionWrapper.execute()`
  (`app/db.py` baris 23: `sql.replace("?", "%s")`). **Sudah diverifikasi langsung**: route ini
  menghasilkan `sqlite3.OperationalError: near "%": syntax error` di mode SQLite — artinya fitur ini
  kemungkinan besar tidak pernah bisa diuji/berjalan di lingkungan dev lokal, hanya di MySQL.
- Password baru dikirim **dalam bentuk plaintext** lewat email — praktik yang secara umum dihindari
  (kalau inbox email pegawai ter-kompromi, password baru langsung terbaca).

**Rekomendasi:**
- Ganti ke alur token-based: kirim link berisi token sekali-pakai + kedaluwarsa (mis. 15 menit), user
  klik link baru diminta set password baru sendiri.
- Tambahkan rate limit per-email & per-IP (mis. maksimal 3x per jam).
- Jangan ungkapkan info "email tidak terdaftar" vs "terdaftar" secara eksplisit — beri pesan generik
  ("Kalau email terdaftar, instruksi reset sudah dikirim") untuk hindari account enumeration.
- Perbaiki placeholder SQL jadi `?` supaya konsisten & bisa diuji di SQLite.

### 3. Kredensial default & akun admin lintas-PT tertanam di kode `[PRE-EXISTING]`
**Lokasi:** `app/config.py`.

- `APP_SECRET` default `"dev-secret-change-me"` — kalau env var lupa di-set saat deploy, seluruh
  session & CSRF token bisa dipalsukan siapa saja yang membaca source code ini (termasuk lewat repo
  git kalau pernah ter-commit).
- `ADMIN_PASSWORD` default `"admin123"` untuk akun `admin@example.com` (`LEGACY_GLOBAL_ADMIN_EMAIL`
  di `web.py`). Akun ini punya akses admin ke **semua PT** tanpa scoping perusahaan (kecuali PT Windu
  Karya, yang sudah dikunci khusus di sesi terakhir). Kombinasi email+password default yang publik di
  source code + akses lintas-PT adalah risiko besar kalau tidak pernah dirotasi di production.
- Email pribadi (`dramadhani881@gmail.com`) ter-hardcode sebagai default `EMAIL_ADMIN_LIST` — bocor
  data pribadi di source code, dan berisiko notifikasi penting salah kirim kalau env var lupa di-override.

**Rekomendasi:**
- Hapus semua default value untuk secret/password — kalau env var tidak diset, aplikasi **gagal start**
  dengan pesan error jelas (fail-fast), jangan diam-diam pakai default yang lemah.
- Evaluasi apakah akun `admin@example.com` (legacy global admin) masih perlu ada di production. Kalau
  perlu, ganti ke akun bernama jelas dengan password kuat & scoping company yang benar, bukan bypass
  universal.
- Hapus email pribadi dari default config; kalau perlu, load dari env var wajib tanpa fallback.

### 4. Race condition (TOCTOU) saat membuat transaksi tarik gaji `[PRE-EXISTING]`
**Lokasi:** `app/routes/web.py`, route `tarik_gaji` POST (±baris 1218–1280).

Urutan operasinya: baca `total_sukses` → hitung `saldo`/`limit_urg_max` → validasi `nominal <= saldo`
→ `INSERT` transaksi baru. Tidak ada lock (`SELECT ... FOR UPDATE`), tidak ada constraint DB, tidak ada
idempotency key/token anti-double-submit di antara langkah-langkah itu. Membuka 2 tab browser atau
double-klik cepat pada tombol submit bisa membuat **dua transaksi lolos validasi limit yang sama**
secara bersamaan, karena keduanya membaca `total_sukses` sebelum salah satunya sempat ter-commit.

Ini berlaku independen dari temuan #1 — bahkan kalau bug tanggal simulasi diperbaiki, race condition ini
tetap ada dan bisa membuat pegawai menarik lebih dari limit harian yang sah.

**Rekomendasi:**
- Bungkus baca-lalu-tulis ini dalam satu transaksi DB dengan row lock (`SELECT ... FOR UPDATE` di
  MySQL), atau
- Tambahkan constraint/agregat check di level DB (trigger, atau re-validasi ulang nominal di dalam
  transaksi yang sama tepat sebelum commit), atau minimal
- Tambahkan idempotency token per submit di sisi client (disable tombol setelah klik — sudah ada
  sebagian di JS invoice, tapi ini tidak melindungi dari 2 tab berbeda).

### 5. Admin "Non-Aktif" tetap bisa login penuh `[PRE-EXISTING]`
**Lokasi:** `app/routes/web.py`, route `login` (±baris 677) vs kolom `admins.status_aktif` yang dipakai
di `superadmin_admins`/`superadmin_edit_admin` (±baris 3325–3381).

Kolom `status_aktif` disimpan dan ditampilkan di daftar admin (bisa diset "Non-Aktif" oleh Superadmin),
tapi **tidak pernah dicek saat proses login** — query login hanya mencocokkan email/nama + password
hash, tidak memfilter `status_aktif=1`. Artinya menonaktifkan seorang admin di UI **tidak benar-benar
mencabut aksesnya** — mereka tetap bisa login dan bertindak normal. Alur offboarding admin secara
fungsional tidak bekerja.

**Rekomendasi:** tambahkan pengecekan `status_aktif` (dan idealnya invalidasi session yang sedang
aktif) di jalur login utama `admins`, bukan cuma jalur khusus (kalau ada).

---

## 🟠 TINGGI

### 6. Hapus/nonaktifkan admin PT Windu Karya bisa mengunci approval selamanya `[interaksi dengan desain sesi terakhir]`
**Lokasi:** `superadmin_delete_admin` (±baris 3390) + desain 2-layer approval khusus PT Windu Karya
(`superadmin_final_gate()`).

`superadmin_delete_admin` melakukan hard-delete tanpa cek dependensi apa pun. Karena desain PT Windu
Karya **tidak pernah** mengizinkan Superadmin melompati layer 1 (beda dari PT lain yang boleh dilewati
kalau memang tidak punya akun admin), kalau satu-satunya akun Admin PT Windu Karya terhapus — atau
bahkan cuma dinonaktifkan (yang, karena temuan #5, sebenarnya tidak benar-benar memblokir apa pun) —
**semua transaksi PT Windu Karya yang sedang `on-proses` menjadi mustahil di-approve** sampai ada admin
Windu Karya baru dibuat.

**Rekomendasi:** tambahkan pengaman di `superadmin_delete_admin` (dan idealnya di alur nonaktifkan admin
juga, begitu temuan #5 diperbaiki): tolak/beri peringatan keras kalau ini admin terakhir untuk sebuah PT
yang masih punya transaksi `on-proses` menunggu approval — terutama untuk PT Windu Karya yang tidak
punya jalur bypass sama sekali.

### 7. Tidak ada rate limiting di mana pun `[PRE-EXISTING]`
Login (pegawai, admin, superadmin), lupa password, tambah project Windu Karya, dan semua endpoint POST
lain tidak punya pembatas percobaan. Bisa di-brute-force atau displintir tanpa hambatan.

**Rekomendasi:** tambahkan rate limiting per-IP/per-akun (mis. Flask-Limiter) minimal untuk `login` dan
`forgot_password`.

### 8. IDOR ringan di `/api/tx_status` `[PRE-EXISTING]`
**Lokasi:** `app/routes/web.py` ±baris 1361.

Endpoint hanya mengecek `"user_id" in session` (siapa saja yang login sebagai pegawai), **tidak**
memfilter `WHERE user_id = session['user_id']`. Pegawai bisa menebak ID transaksi orang lain dan melihat
status + sisa waktu cancel-nya. Dampak sedang (nominal/fee tidak ikut terekspos di response JSON).

**Rekomendasi:** tambahkan filter `user_id` di query.

### 9. Session tidak di-hardening `[PRE-EXISTING]`
Tidak ada `SESSION_COOKIE_SECURE`, `SESSION_COOKIE_SAMESITE`, atau `PERMANENT_SESSION_LIFETIME` yang
diset eksplisit di mana pun. Cookie session bisa terkirim lewat HTTP biasa (kalau TLS tidak dipaksa di
level infra), dan tidak ada auto-logout karena idle (session bertahan selama browser tidak ditutup).

**Rekomendasi:** set `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_HTTPONLY=True` (default Flask sudah
True, pastikan tidak ter-override), `SESSION_COOKIE_SAMESITE="Lax"` eksplisit, dan pertimbangkan
`PERMANENT_SESSION_LIFETIME` + `session.permanent = True` dengan durasi wajar (mis. 8 jam) khusus untuk
sesi admin/superadmin.

### 10. `requirements.txt` tanpa pin versi `[PRE-EXISTING]`
Semua dependency (`flask`, `gunicorn`, `redis`, `rq`, `mysql-connector-python`, `python-dotenv`, `pytest`)
tidak punya nomor versi. Build tidak reproducible; `pip install` di masa depan bisa menarik versi yang
rusak/berisiko kapan saja. `pytest` juga terdaftar sebagai dependency utama (ikut ter-install di
production), bukan dev-only.

**Rekomendasi:** pin versi eksplisit (`flask==3.x.x`, dst), pisahkan `requirements-dev.txt` untuk
`pytest`.

---

## 🟡 SEDANG

### 11. `NOW()` (fungsi MySQL) hardcode di 2 tempat `[PRE-EXISTING]`
**Lokasi:** `admin_pegawai_delete` (±baris 2667, insert ke `pegawai_archive`) dan `superadmin_admins_add`
(±baris 3433, insert admin baru).

Sudah diverifikasi langsung: `sqlite3.OperationalError: no such function: NOW`. Artinya **hapus
pegawai** dan **tambah akun admin baru** (termasuk cara resmi membuat akun "Admin PT Windu Karya" yang
jadi prasyarat fitur yang baru dibuat!) kemungkinan besar hanya bisa diuji/berjalan di MySQL, tidak
pernah bisa dites di SQLite lokal.

**Rekomendasi:** ganti `NOW()` → parameter Python (`datetime.now().isoformat(...)`) seperti pola yang
sudah dipakai konsisten di tempat lain di codebase ini.

### 12. Tidak ada batas ukuran upload avatar `[PRE-EXISTING]`
`MAX_CONTENT_LENGTH` tidak diset di config manapun. Upload avatar bisa dikirim file berukuran sangat
besar, potensi DoS disk/memory pada server.

**Rekomendasi:** set `app.config["MAX_CONTENT_LENGTH"]` (mis. 5 MB) di `app/__init__.py`.

### 13. Kode sisa testing nyangkut di jalur produksi `[PRE-EXISTING]`
- `print("=== DEBUG: REQUEST POST MASUK KE BACKEND ===")` di jalur tarik gaji (±baris 1171).
- Endpoint `/txn/test` di `app/__init__.py` (±baris 83) bebas diakses tanpa login, bisa insert baris
  tak terbatas ke DB — kandidat kuat untuk dihapus atau dikunci di belakang env flag debug-only.
- Email lupa-password dikirim **sinkron**, bukan lewat antrean (comment: "Panggil langsung tanpa
  antrean untuk test") — bisa membuat request nge-hang kalau SMTP lambat/down.

**Rekomendasi:** hapus/nonaktifkan `/txn/test` di production, pindahkan pengiriman email di
`forgot_password` ke `enqueue_email()` seperti jalur lain, bersihkan `print()` debug.

### 14. Nol automated test walau `pytest` jadi dependency `[PRE-EXISTING]`
Tidak ada folder `tests/` sama sekali di repo. Seluruh pengujian yang dilakukan selama sesi perbaikan
kemarin murni ad-hoc (script sementara, dibuang setelah dipakai), tidak built-in ke repo untuk dijalankan
ulang tim dev atau CI.

**Rekomendasi:** bangun minimal test suite untuk alur kritis (login, hitung limit tarik gaji, approval
2-layer, fee manual) — bisa dimulai dari mengadaptasi skenario yang sudah divalidasi manual di
`PerubahanFiles.md` sesi terakhir menjadi test file resmi di `tests/`.

### 15. `web.py` monolit 3.864 baris `[PRE-EXISTING]`
Semua route dan business logic dalam satu file. Risiko "bus factor" tinggi, developer baru butuh waktu
lama untuk paham konteks, rawan merge conflict kalau lebih dari satu developer jalan bersamaan, dan
perubahan kecil butuh scan manual berkali-kali untuk memastikan tidak ada logic terduplikasi tersebar
(seperti 10 titik pengecekan `is_parent` yang ditemukan & dirapikan di sesi terakhir).

**Rekomendasi:** pecah bertahap per domain (`routes/auth.py`, `routes/pegawai.py`,
`routes/approval.py`, `routes/tarik_gaji.py`, dst) — tidak perlu sekaligus, cukup setiap kali menyentuh
satu area, pindahkan ke modul sendiri.

### 16. Pesan error mentah bocor ke UI Superadmin `[PRE-EXISTING]`
**Lokasi:** `superadmin_edit_admin` (±baris 3382): `flash(f"Gagal mengupdate admin: {e}")` menampilkan
exception Python asli (berpotensi berisi detail internal DB) ke layar. Risiko rendah (cuma terlihat
Superadmin yang memang sudah punya akses tinggi), tapi bukan praktik baik.

**Rekomendasi:** log exception ke server log, tampilkan pesan generik ke user.

---

## 🟢 RENDAH / Catatan Bisnis

### 17. Kolom `urg_lock_until` tidak pernah dipakai `[PRE-EXISTING]`
Selalu diisi `NULL` saat insert transaksi (±baris 1205 & 1294), tidak pernah dibaca di mana pun.
Sepertinya fitur "jeda/cooldown URG" yang direncanakan tapi tidak pernah diselesaikan.

**Rekomendasi:** konfirmasi ke tim/product owner apakah fitur ini memang perlu diselesaikan, atau kolom
ini aman dihapus untuk mengurangi kebingungan developer berikutnya.

### 18. Script maintenance CLI hardcode ke SQLite `[PRE-EXISTING]`
`app/ubah_role.py`, `app/add_superadmin.py`, `app/cek_db.py`, `app/ubah_pw.py` semuanya connect
langsung ke file SQLite lokal (`sqlite3.connect(DB_PATH)`), bukan lewat `app/db.py` yang mendukung
MySQL. Tidak akan berfungsi untuk kelola akun di database production (MySQL).

**Rekomendasi:** kalau tim ops memang butuh alat CLI resmi untuk production, tulis ulang lewat
`app/db.py` (`ensure_db()` + koneksi yang sama dipakai aplikasi), atau dokumentasikan jelas bahwa
perubahan role/akun di production harus lewat UI Superadmin, bukan script ini.

---

## Catatan Pengujian & Transparansi (pekerjaan sesi terakhir) `[SESI TERAKHIR]`

- **Race condition kecil** di pendaftaran project Windu Karya (`windu_projects`): pengecekan duplikat
  dilakukan di level aplikasi (SELECT dulu, baru INSERT), bukan `UNIQUE constraint` di database. Dua
  request bersamaan (klik dobel/dua tab) secara teori masih bisa lolos jadi 2 baris duplikat. Risiko
  rendah, tapi bisa ditambah `UNIQUE` index (case-insensitive) kalau mau lebih aman.
- **Belum diuji otomatis secara khusus** setelah refactor `_is_parent_company()`: route `/admin/riwayat`,
  `/superadmin/riwayat`, dan `/admin/pegawai/<id>/delete`. Kodenya sudah dibaca ulang dan diyakini benar
  (semua 10 titik duplikat dipetakan & diganti helper yang sama), tapi belum dijalankan test end-to-end
  khusus untuk 3 route ini seperti untuk dashboard/pegawai/approval. **Sebaiknya dicoba manual sebelum
  deploy ke production.**
- Fee admin `0` diterima sebagai nilai valid (sengaja, sesuai keputusan bisnis yang diminta) — pastikan
  ini memang dimaksudkan (fee 0 = gratis untuk pegawai tsb), bukan celah yang tidak disadari.
- Daftar project PT Windu Karya masih **kosong** setelah deploy — perlu didaftarkan manual lewat menu
  "Kelola Project Windu Karya" atau lewat `migrasi_windu_projects.sql`.

---

## Urutan Perbaikan yang Disarankan

1. **#1 Tanggal simulasi** dan **#4 Race condition transaksi** — ini yang paling langsung berdampak ke
   uang keluar, prioritas tertinggi.
2. **#5 Admin non-aktif tetap bisa login** dan **#3 Kredensial default** — pintu masuk akses tak sah,
   harus ditutup sebelum yang lain.
3. **#2 Lupa password** — perbaiki jadi token-based sekaligus benerin bug SQLite-nya.
4. **#6 Pengaman hapus admin terakhir PT Windu Karya** — cepat dikerjakan, mencegah deadlock operasional.
5. Sisanya (#7–#18) bisa dikerjakan bertahap sesuai kapasitas tim — tidak mendesak secara langsung
   tapi penting untuk kesehatan jangka panjang project.

Belum ada satu pun perbaikan dari daftar ini yang dieksekusi — dokumen ini murni untuk referensi
prioritas ke depan. Konfirmasi dulu item mana yang mau dikerjakan sebelum saya mulai ubah kode, terutama
untuk #1 (tanggal simulasi) karena ada kemungkinan itu memang sengaja dipakai untuk keperluan
testing/demo dan saya tidak mau menghapusnya tanpa persetujuan.
