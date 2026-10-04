# Catatan Perubahan — Gajiku-Live

Nomor baris di bawah mengacu ke file **setelah** perubahan (isi file saat ini di repo ini). Semua file
tetap memakai line-ending aslinya (LF, kecuali `superadmin_dashboard.html` yang aslinya CRLF — tetap CRLF).

Nama file di permintaan awal vs nama file asli di project:
- "superadmin_karyawan.html" → sebenarnya **`templates/admin_pegawai.html`** (dipakai Admin & Superadmin).
- "tarikgaji.html" → sebenarnya **`templates/tarik_gaji.html`**.

Semua perubahan sudah diuji lewat skenario simulasi (Flask test client, DB SQLite sementara meniru skema
MySQL) — lihat bagian **Pengujian** di paling bawah.

---

## 1. UMUM — Fee admin diinput manual (bukan pilihan 15.000/17.000)

### `app/routes/web.py`
- **Baris 146–148**: `ADMIN_FEE_FLAT_OPTIONS` diberi komentar bahwa nilainya kini hanya info/default,
  ditambah `ADMIN_FEE_FLAT_MIN = 0` dan `ADMIN_FEE_FLAT_MAX = 1_000_000` sebagai batas validasi input manual.
- **Baris 155–168** (fungsi baru `parse_admin_fee_flat`, fungsi `normalize_admin_fee_flat` diubah):
  - `parse_admin_fee_flat(value)`: parse angka manual (terima `"15000"` atau `"15.000"`), return `None`
    kalau kosong / bukan angka / di luar rentang 0–1.000.000.
  - `normalize_admin_fee_flat` sekarang memanggil `parse_admin_fee_flat`, tidak lagi mengecek
    `fee in ADMIN_FEE_FLAT_OPTIONS`. **Ini bug lama**: sebelumnya fee custom apa pun otomatis
    dipaksa balik ke default 15.000 karena hanya 15000/17000 yang lolos validasi.
- **Sekitar baris 1926–1935 (route `admin_pegawai_add`)**: Superadmin sekarang wajib mengisi fee lewat
  `parse_admin_fee_flat(request.form.get("admin_fee_flat"))` (bukan `normalize_admin_fee_flat` dengan default
  diam-diam). Ditambah validasi: jika `admin_fee_flat is None` → flash error
  `"Admin fee wajib diisi manual (angka 0 - Rp1.000.000)."` dan redirect balik (form tidak disimpan).
- **Sekitar baris 2298–2311 (route `admin_pegawai_update`)**: logika diubah total —
  - `current_fee` diambil dulu dari DB (fee pegawai yang sedang di-edit).
  - Field dikosongkan → fee lama dipertahankan (tidak error, tidak reset ke 15.000).
  - Field diisi tapi tidak valid (di luar 0–1.000.000 atau bukan angka) → flash error
    `"Admin fee tidak valid (angka 0 - Rp1.000.000)."`, redirect, **tidak** menyimpan perubahan lain di form.
  - Field diisi & valid → dipakai sebagai fee baru.
  - Admin biasa (non-superadmin): fee tidak berubah (selalu `current_fee`), sama seperti sebelumnya.

### `templates/admin_pegawai.html`
- **Baris 104**: kolom tabel fee di daftar pegawai — `r['admin_fee_flat'] or 15000` diganti jadi
  `r['admin_fee_flat'] if r['admin_fee_flat'] is not none else 15000`, supaya fee `0` (kini nilai valid)
  tidak salah ditampilkan sebagai 15.000.
- **Baris 128**: payload JS data pegawai untuk modal edit — perbaikan yang sama (`is not none` bukan `or`).
- **Baris 187–188** (form **Tambah** pegawai, hanya tampil untuk Superadmin — dibungkus
  `{% if session.get('is_superadmin') %}`): radio `15.000 / 17.000` (`.cycle-toggle.fee-toggle`) diganti
  `<input id="add_admin_fee_flat" name="admin_fee_flat" type="text" inputmode="numeric" pattern="[0-9.]+"
  placeholder="isi manual, cth: 15000" autocomplete="off" required>`.
- **Baris 243–244** (form **Edit** pegawai, Superadmin): radio diganti input teks serupa,
  `id="m_admin_fee_flat"`.
  - Blok fallback untuk Admin biasa (radio disabled + hidden input default 15000) **tidak diubah** —
    tetap dalam komentar HTML seperti aslinya (baris 246–250).
- **Baris 287–291** (JS): fungsi `setEditAdminFeeSelection(value)` (mencentang radio) diganti
  `setEditAdminFeeValue(value)` yang mengisi `input#m_admin_fee_flat.value` (default `15000` bila
  data kosong/null); dipanggil di baris 312 saat modal edit dibuka.

**Perilaku baru:** Superadmin mengisi angka bebas (bukan cuma 15.000/17.000) saat tambah/edit karyawan;
kosong saat edit = fee lama dipertahankan; di luar 0–1.000.000 ditolak dengan pesan error jelas.

---

## 2. UMUM — Approval 2 layer: Admin → Superadmin (transfer)

Prinsip: kolom `status` transaksi **tidak** langsung jadi `sukses` saat Admin PT approve — itu masih
`on-proses`. Baru menjadi `sukses` setelah **Superadmin** approve (karena Superadmin yang transfer dana).
Dua kolom pencatat approval ditambahkan: `admin_approved_at/by` (layer 1) dan `final_approved_at/by` (layer 2).

### `app/db.py`
- **Baris 388–424 (baru)**: `APPROVAL_COLUMNS` (daftar kolom yang perlu ada) + fungsi
  `ensure_approval_columns(db)` — menambahkan 4 kolom (`admin_approved_at`, `admin_approved_by`,
  `final_approved_at`, `final_approved_by`) ke tabel `transactions` bila belum ada. Kompatibel SQLite
  (`PRAGMA table_info` + `ALTER TABLE ... ADD COLUMN`) maupun MySQL (`SHOW COLUMNS ... LIKE`).
  Idempotent (aman dipanggil berkali-kali) dan tidak menghentikan start aplikasi bila gagal (mis. user DB
  tanpa hak `ALTER`) — hanya `print` peringatan lalu lanjut; di kasus itu jalankan
  `migrasi_2layer_approval.sql` manual (lihat berkas terpisah).
- **Baris 444 & 449**: `ensure_approval_columns(...)` dipanggil di `ensure_db()`, baik jalur SQLite
  (`init_db()`) maupun MySQL, supaya kolom otomatis dibuat saat aplikasi start pertama kali.

### `app/utils/cache.py`
- **Baris 22–28 (baru)**: fungsi `clear_cache_prefix(prefix)` — menghapus semua entri cache in-memory
  yang key-nya diawali `prefix`. Dipakai setelah approve/reject supaya angka KPI di dashboard admin
  (`admin_kpi:...`, TTL 10 detik) tidak menampilkan data basi sesaat setelah aksi.

### `app/routes/web.py` — blok logic baru (baris 317–450, setelah `require_superadmin()`)
Ditambahkan komentar besar berjudul **"2-LAYER APPROVAL TARIK GAJI"** berisi:
- `LEGACY_GLOBAL_ADMIN_EMAIL = "admin@example.com"` — email admin lama di seed data yang dashboard-nya
  memang didesain melihat *semua* PT (lihat `struktur_gajiku.sql`/seed); tetap diberi akses penuh
  layer 1 **kecuali** untuk PT Windu (lihat bagian 3).
- `WINDU_KEYWORD = "windu"`, `is_windu_company(*names)` — deteksi PT Windu dari kata "windu" (case
  insensitive) pada nama perusahaan / perusahaan induk pegawai atau admin.
- `_is_parent_company(db, company)` — cek apakah `company` adalah perusahaan induk (dipakai untuk
  admin holding yang mengelola beberapa anak PT, konsisten dengan filter yang sudah ada di
  `admin_dashboard`/`admin_pegawai`).
- `load_admin_companies(db)` — daftar nama perusahaan (huruf kecil) yang punya akun Admin PT aktif.
- `company_has_admin(perusahaan, perusahaan_induk, admin_companies)` — cek PT tsb punya admin atau tidak.
- `get_tx_approval_row(db, txid)` — ambil 1 transaksi + perusahaan pegawainya + status approval.
- `admin_can_handle(db, perusahaan, perusahaan_induk, self_is_parent=None)` — **LAYER 1**: apakah Admin
  yang login berwenang atas transaksi ini (PT sendiri, atau anak PT bila admin holding; PT Windu
  hanya boleh Admin PT Windu sendiri; admin global lama boleh untuk PT non-Windu).
- `superadmin_final_gate(tx, admin_companies)` — **LAYER 2**: boolean + alasan, apakah Superadmin boleh
  approve final sekarang (butuh `admin_approved_at` terisi, **kecuali** PT tsb memang tidak punya akun
  admin sama sekali → boleh langsung supaya antrian tidak macet; PT Windu **selalu** wajib approval admin
  dulu, tanpa pengecualian).
- `decorate_pending_tx(db, rows)` — mengubah daftar transaksi `on-proses` (hasil query cache) menjadi
  list of dict dengan field tambahan untuk template: `stage` (`menunggu_admin` / `menunggu_superadmin`),
  `is_windu`, `admin_can_act`, `can_final`, `final_block_reason`. Status approval disegarkan langsung dari
  DB (bukan dari cache 10 detik) supaya tombol Approve tidak pernah menampilkan data basi; transaksi yang
  ternyata sudah diproses pengguna lain otomatis disaring keluar dari daftar.

### Route `admin_dashboard` (baris ±1410–1800)
- **Baris 1487**: cache key KPI dinaikkan dari `admin_kpi:v5:...` → `admin_kpi:v6:...` (memaksa cache lama
  yang belum punya kolom approval untuk dihitung ulang, hindari bug tampilan setelah deploy).
- 4 query SELECT antrian `on-proses` (REG & URG, sekitar baris 1611–1700-an) ditambah kolom
  `p.perusahaan_induk` dan `t.admin_approved_at, t.admin_approved_by`.
- **Baris 1783–1792**: sebelum `render_template`, panggil `decorate_pending_tx()` untuk `pending_reg` &
  `pending_urg`, hitung `pending_admin_count` (jumlah yang menunggu approve Admin ini), kirim ke template
  sebagai `pending_admin_count`.

### Route `superadmin_dashboard` (baris ±2730–3140)
- Query antrian REG & URG ditambah kolom `perusahaan_induk`, `admin_approved_at`, `admin_approved_by`
  (baris ±3057–3091, pola sama seperti di `admin_dashboard`).
- **Baris 3088–3090**: `decorate_pending_tx()` dipanggil untuk kedua daftar, `pending_admin_wait` dihitung
  (transaksi yang **belum** bisa di-final-approve karena masih menunggu Admin PT).
- **Baris 3130**: `pending_admin_wait` dikirim ke template.

### Route approve/reject (baris 2660–2723 untuk Admin, 3735–3785 untuk Superadmin) — **ditulis ulang total**
- `admin_tx_approve(txid)`:
  - Superadmin yang tidak sengaja memanggil endpoint ini → di-redirect ke dashboard Superadmin (tidak
    diizinkan "melompati" layer 1 lewat endpoint Admin).
  - Validasi: transaksi ada, `admin_can_handle()` (harus PT miliknya — **sebelumnya route lama sama sekali
    tidak mengecek kepemilikan PT**, admin PT mana pun bisa approve transaksi PT lain), status masih
    `on-proses`, belum pernah di-approve admin sebelumnya.
  - **Efek**: mengisi `admin_approved_at` + `admin_approved_by` (nama/email admin dari session).
    Status transaksi **tetap** `on-proses` (dulu langsung `sukses` — ini yang diperbaiki).
  - `clear_cache_prefix("admin_kpi:")` dipanggil supaya dashboard langsung update.
- `admin_tx_reject(txid)`:
  - Superadmin yang memanggil endpoint ini → diarahkan ke `superadmin_tx_reject` (boleh reject kapan saja).
  - Admin PT hanya boleh reject **sebelum** dia sendiri approve (setelah approve, keputusan tolak jadi
    wewenang Superadmin saja, supaya tidak ada status "sudah admin-approve lalu ditolak admin lagi").
  - Validasi kepemilikan PT sama seperti approve.
- `superadmin_tx_approve(txid)`:
  - **LAYER 2**: cek `superadmin_final_gate()` — kalau belum boleh (menunggu admin PT / khusus PT Windu
    wajib admin dulu), flash pesan alasan spesifik & batalkan.
  - Kalau boleh: `status='sukses'`, isi `final_approved_at/by`.
- `superadmin_tx_reject(txid)`: tetap boleh menolak transaksi `on-proses` kapan saja (tidak terikat layer),
  ditambah `clear_cache_prefix`.

### `templates/admin_dashboard.html`
Dua section "Permintaan Reg/Urg On-Proses" **sebelumnya di-comment total (tidak berfungsi)** — sekarang
diaktifkan dan ditulis ulang (baris ±178–263):
- **Baris 179–237**: macro Jinja `pending_table(rows)` — tabel antrian dengan kolom baru **"Tahap Approval"**
  (menampilkan siapa yang sudah approve / badge "Menunggu approve Anda" / "Di luar cakupan Anda") dan kolom
  Aksi (tombol Approve/Tolak, masing-masing `<form method="post">` dengan `csrf_token`, hanya tampil bila
  `t['admin_can_act']` true — Admin tidak lagi melihat tombol untuk transaksi PT yang bukan tanggung jawabnya).
- **Baris 239–250**: section "Permintaan Reg On-Proses" memanggil macro tsb dengan `pending_reg`, ditambah
  keterangan singkat 2-layer approval + badge jumlah `pending_admin_count`.
- **Baris 253–263**: section "Permintaan Urg On-Proses" (tetap dibungkus `{% if 'urg' in enabled_products %}`
  seperti bagian lain di file ini) memanggil macro yang sama dengan `pending_urg`.

### `templates/superadmin_dashboard.html`
- **Baris 165–177 (baru)**: macro Jinja `tahap(t)` — badge status approval per baris ("Approved Admin" +
  nama admin / "Tanpa Admin PT" bila PT tak punya admin / "Menunggu Admin" bila belum), plus label "PT Windu"
  bila relevan.
- **Baris 181–184**: keterangan singkat 2-layer approval + badge `pending_admin_wait` di atas tabel REG.
- **Baris 200 & 259**: header kolom baru **"Tahap Approval"** ditambahkan di tabel REG dan URG.
- **Baris 215–225 (REG) & 274–284 (URG)**: sel `{{ tahap(t) }}` ditambahkan; tombol **Approve** sekarang
  dibungkus `{% if t['can_final'] %}` — kalau belum boleh (menunggu Admin PT), tombol diganti
  `<button disabled title="{{ t['final_block_reason'] }}">Menunggu Admin</button>` (title berisi alasan).
  Untuk transaksi PT Windu, tombol approve berlabel **"Approve & Transfer"**.
- **Baris 311**: KPI ringkas "Pending" diberi sub-baris jumlah `pending_admin_wait` (yang masih menunggu
  Admin PT), bila > 0.

**Alur akhir (umum):**
`Pegawai ajukan` → `on-proses` → **Admin PT approve** (status tetap `on-proses`, tercatat
`admin_approved_at/by`) → **Superadmin approve** (status jadi `sukses`, tercatat `final_approved_at/by`,
Superadmin yang transfer). PT yang belum punya akun Admin: Superadmin boleh approve langsung (tanpa
menunggu layer 1) supaya antrian tidak macet.

---

## 3. KHUSUS PT WINDU

Diimplementasikan **di dalam logic umum di atas** (bukan file terpisah), lewat `is_windu_company()`.
Tidak ada perubahan struktur database — deteksi memakai kolom yang sudah ada: `admins.company` (untuk
admin) dan `pegawai.perusahaan` / `pegawai.perusahaan_induk` (untuk pegawai).

- **`app/routes/web.py`, baris 318**: `WINDU_COMPANY_NAME = "Windu Karya"`.
- **`is_windu_company()`** (±baris 323–327): **exact match** (case-insensitive, trim spasi) ke
  `"Windu Karya"` — **bukan** sekadar mengandung kata "windu". PT lain yang kebetulan memuat kata itu
  (mis. "PT Winduaji Sejahtera") **tidak** ikut tertangkap sebagai PT Windu; sudah diuji secara eksplisit
  (lihat bagian Pengujian).
- **Fungsi `admin_can_handle()`** (±baris 363–380): jika `perusahaan`/`perusahaan_induk` pegawai persis
  `"Windu Karya"`, **hanya** Admin yang `company`-nya juga persis `"Windu Karya"` yang boleh
  approve/tolak — termasuk admin holding & admin global lama (`admin@example.com`) **tidak** diberi akses,
  berbeda dari perlakuan PT lain.
- **Fungsi `superadmin_final_gate()`** (±baris 381–391): untuk PT Windu Karya, Superadmin **selalu** wajib
  menunggu `admin_approved_at` terisi — **tidak** ada pengecualian "PT belum punya admin" seperti PT lain
  (PT Windu Karya diasumsikan selalu punya Admin PT sendiri).
- **UI**: badge "PT Windu" di dashboard Superadmin (`superadmin_dashboard.html`, macro `tahap`), dan label
  tombol final approve menjadi **"Approve & Transfer"** (karena Superadmin yang melakukan transfer),
  serta keterangan "Setelah ini Superadmin approve & transfer" di dashboard Admin PT Windu Karya
  (`admin_dashboard.html`, macro `pending_table`).

**Alur PT Windu Karya:** `Karyawan Windu Karya ajukan` → `on-proses` → **wajib Admin PT Windu Karya sendiri
approve** → **wajib Superadmin approve & transfer** → `sukses`. Tidak ada jalur pintas apa pun.

## 4. UMUM — Invoice sebelum "Tarik Gaji" di `templates/tarik_gaji.html`

### Backend — `app/routes/web.py`, route `tarik_gaji()` (±baris 1028–1071)
- **Baris 1047**: query rekening pegawai ditambah kolom `id_pegawai, perusahaan` (sebelumnya hanya
  ambil kolom rekening) — dibutuhkan untuk ditampilkan di invoice.
- **Baris 1070**: `pegawai_info=pegawai_rekening` ditambahkan ke `render_template(...)`.

### Template — `templates/tarik_gaji.html`
- **Baris 12**: `{% set pegawai_info = pegawai_info if pegawai_info is defined else none %}` (default aman
  bila variabel belum dikirim dari suatu jalur render lain).
- **Baris 46**: setiap radio pilihan rekening tujuan (`input[name="rekening_tujuan_key"]`) diberi
  `data-label="{{ opt.label }}" data-value="{{ opt.value }}"` supaya JS bisa membaca teks rekening yang
  dipilih untuk ditulis di invoice.
- **Baris 92**: label tombol utama diubah dari **"Apply"** menjadi **"Lihat Invoice"**.
- **Baris 113–159 (markup baru)**: `<div id="invoiceModal">` — modal overlay berisi:
  judul, badge produk (REG/URG), baris-baris detail (tanggal, nama, ID pegawai, perusahaan, produk,
  rekening tujuan, nominal, rincian fee, PPN bila aktif, total fee, keterangan bila diisi), catatan kecil,
  dan 2 tombol: **"Kembali"** (`#invBack`) & **"Tarik Gaji"** (`#invConfirm`).
- **Baris ±132–158**: CSS modal invoice (`.invoice-overlay`, `.invoice-card`, `.invoice-row`, dst.), memakai
  variabel warna tema yang sudah ada (`--bg2`, `--text`, `--border`, `--muted`) supaya konsisten dengan
  tema gelap/terang situs.
- **Baris 184–207 (JS baru)**: `INV_INFO` (data pegawai untuk invoice, dikirim via `tojson` lalu ditulis
  ke DOM dengan `textContent` — aman dari XSS) + `computeFee(produk, nominal)`, fungsi murni hasil
  ekstraksi dari logic `recalcFee()` yang sudah ada (rumus **tidak diubah**, hanya dipisah supaya bisa
  dipakai ulang oleh invoice tanpa duplikasi rumus).
- **Baris ±221–222 & 244**: `recalcFee()` (preview fee live saat mengetik nominal) disesuaikan supaya
  memanggil `computeFee()` yang baru, bukan menghitung ulang inline — perilaku tampilan preview form
  **tidak berubah**, hanya sumber perhitungannya kini satu tempat.
- **Baris 259–311 (JS baru)**: `invRow()` (helper baris invoice, pakai `textContent`), `openInvoice(form)`
  (mengisi seluruh isi modal dari state form saat ini), `closeInvoice()`.
- **Baris 312–332**: `handleSubmitTarikGaji(form)` — validasi ringan (nominal > 0, batas REG, rekening
  dipilih) **tetap dijalankan seperti semula**; setelah lolos, alih-alih langsung `confirm()` lalu submit,
  sekarang: bila invoice belum dikonfirmasi (`invoiceConfirmed` masih `false`) → panggil `openInvoice()`
  dan **batalkan submit** (`return false`); baru setelah pengguna klik "Tarik Gaji" di modal, form benar-benar
  dikirim.
- **Baris ±337–349**: event listener — `#invBack` & klik area gelap & tombol `Esc` menutup modal
  (`closeInvoice`); `#invConfirm` men-set `invoiceConfirmed = true`, menonaktifkan tombol (`disabled`) untuk
  cegah klik ganda, lalu `form.requestSubmit()`.

**Perilaku baru:** klik tombol utama → invoice detail transaksi muncul dulu (bukan langsung `confirm()` browser
seperti sebelumnya) → pengguna review → klik "Tarik Gaji" di modal → form baru benar-benar terkirim ke server.
Validasi & endpoint backend (`POST /tarik-gaji`) **tidak diubah sama sekali** — perubahan murni di lapisan
konfirmasi sebelum submit, jadi nominal/fee final tetap dihitung ulang oleh server seperti biasa (dicatat
juga sebagai catatan di modal).

---

## 4b. KHUSUS PT WINDU KARYA — Dropdown project (bukan ketik manual) di form Tambah/Edit Pegawai

Field "Perusahaan" di `admin_pegawai.html` semula input teks bebas untuk semua admin. Sekarang khusus
untuk **Admin PT Windu Karya** (session `company` persis "Windu Karya") field itu jadi dropdown berisi
daftar project resmi — bukan lagi diketik manual — supaya tidak ada nama project dobel gara-gara typo.
Sesuai keputusanmu: daftar project **tetap** (bukan diambil otomatis dari data lama), dan kalau ada
project baru, **harus didaftarkan dulu** oleh Superadmin atau Admin PT Windu Karya sendiri lewat menu
baru "Kelola Project Windu Karya" — admin PT Windu Karya sama sekali tidak bisa mengetik nama bebas lagi.
Tidak ada perubahan pada tabel `admins`/`pegawai` yang sudah ada — hanya menambah **1 tabel baru**
(`windu_projects`) khusus untuk daftar project ini.

### Perbaikan fondasi wajib lebih dulu: `_is_parent_company()` (`app/routes/web.py`, ±baris 329–341)
Sebelum fitur ini bisa jalan, ada masalah "ayam-telur": kode lama mendeteksi apakah sebuah company
"induk/holding" (punya banyak anak perusahaan/project) **secara dinamis** — dengan mengecek apakah
sudah ADA pegawai di database dengan `perusahaan_induk` = company tsb. Untuk PT Windu Karya yang baru
mau mulai memakai struktur project, pengecekan ini selalu `False` di awal (belum ada data), jadi PT
Windu Karya tidak akan pernah dianggap induk — dropdown project tidak akan pernah berfungsi dengan benar
(project yang dipilih tidak akan otomatis tersimpan sebagai anak dari "Windu Karya").

**Perbaikan**: `_is_parent_company()` sekarang mengembalikan `True` untuk PT Windu Karya **selalu**,
tanpa syarat data historis (`is_windu_company(company)` dicek lebih dulu, sebelum query dinamis ke DB).
Fungsi ini dipakai lewat **satu tempat terpusat**, jadi saya sekalian membereskan **10 titik kode**
yang tadinya masing-masing menduplikasi query SQL yang sama persis untuk cek "is_parent" (di
`admin_dashboard`, `admin_riwayat`, `admin_pegawai` [listing + dropdown filter], `admin_pegawai_add`
[2x], `admin_pegawai_update` [2x], hapus-pegawai, dan `superadmin_riwayat`) — semuanya sekarang memanggil
`_is_parent_company(db, admin_company)` yang sama, bukan lagi 10 salinan query terpisah. Efeknya
otomatis konsisten ke semua halaman (dashboard, riwayat, daftar pegawai) begitu helper ini diperbaiki.

**Efek samping yang perlu diperhatikan**: karena PT Windu Karya sekarang selalu dianggap induk, query
yang menentukan pegawai mana saja yang termasuk cakupan Admin PT Windu Karya beralih dari
`perusahaan = 'Windu Karya'` menjadi `perusahaan_induk = 'Windu Karya'`. **Pegawai PT Windu Karya LAMA**
(kalau ada, di database production, dengan `perusahaan` diisi literal "Windu Karya" tanpa
`perusahaan_induk` terisi) akan **hilang dari tampilan dashboard Admin PT Windu Karya** sampai
`perusahaan_induk`-nya dirapikan. Transaksi mereka tetap tervalidasi benar sebagai PT Windu Karya di
alur approval (itu dicek terpisah lewat `is_windu_company`, tidak terpengaruh), tapi tidak akan tampil
di listing dashboard. **Migrasi data untuk kasus ini ada di `migrasi_2layer_approval.sql`** (bagian
paling bawah, UPDATE opsional yang sengaja dikomentari) — jalankan sekali setelah deploy kalau memang
ada data lama semacam itu.

### `app/db.py`
- **Fungsi baru `ensure_windu_projects_table(db)`**: membuat tabel `windu_projects` (id, nama_project,
  created_by, created_at) otomatis saat aplikasi start, kompatibel SQLite & MySQL, idempotent (aman
  dipanggil berkali-kali), tidak menghentikan start aplikasi bila gagal (mis. tanpa hak `CREATE TABLE`)
  — di kasus itu jalankan `migrasi_windu_projects.sql` manual.
- Dipanggil di `ensure_db()` (jalur SQLite maupun MySQL), sama seperti `ensure_approval_columns()`.

### `app/routes/web.py` — helper & route baru (sebelum `def decorate_pending_tx`)
- `can_manage_windu_projects()` — `True` untuk Superadmin **atau** Admin yang `company`-nya persis
  "Windu Karya".
- `get_windu_projects(db)` — daftar nama project (list string, urut A-Z) untuk dropdown.
- `get_windu_project_rows(db)` — daftar `{id, nama_project}` untuk panel kelola/hapus.
- `windu_project_exists(db, nama)` — cek project sudah terdaftar (case-insensitive, trim).
- **`POST /admin/windu-projects/add`** (`admin_windu_projects_add`): daftarkan project baru. Validasi:
  wajib login admin, harus `can_manage_windu_projects()`, nama tidak boleh kosong / >255 karakter,
  tidak boleh sama dengan "Windu Karya" (nama induk sendiri), dan tidak boleh duplikat
  (case-insensitive, trim — supaya "Site Cikarang" dan "site   cikarang" dianggap sama). Kalau lolos,
  simpan + flash sukses.
- **`POST /admin/windu-projects/<id>/delete`** (`admin_windu_projects_delete`): hapus project dari
  daftar — ditolak (flash error) kalau project itu **masih dipakai** oleh pegawai manapun (dicek ke
  tabel `pegawai`), supaya tidak ada pegawai yang tiba-tiba kehilangan referensi project-nya.

### Route `admin_pegawai` (GET, listing) — ±baris akhir fungsi sebelum `render_template`
- Variabel baru dikirim ke template: `is_windu_admin` (`True` hanya utk Admin non-super yang
  `company`-nya persis "Windu Karya"), `windu_projects` (list nama, untuk dropdown — hanya diisi kalau
  `is_windu_admin` atau Superadmin), `windu_project_rows` (untuk panel kelola), dan
  `can_manage_windu_projects` (kontrol tampil-tidaknya tombol "Kelola Project Windu Karya").

### Route `admin_pegawai_add` & `admin_pegawai_update` — validasi server-side (bukan cuma UI)
Di kedua route, setelah nilai `perusahaan` ditentukan (lewat logic `_is_parent_company` yang sudah
diperbaiki), ditambah pengecekan: **kalau admin yang login adalah Admin PT Windu Karya (bukan
Superadmin)**, nilai `perusahaan` yang dikirim **wajib** ada di tabel `windu_projects` — kalau tidak,
ditolak dengan flash error dan redirect balik (data tidak disimpan). Ini penting supaya proteksi tidak
bisa dilewati dengan mengirim request POST manual langsung ke server (di luar dropdown HTML).

### `templates/admin_pegawai.html`
- **Tombol baru** "Kelola Project Windu Karya" di toolbar atas (sebelah "+ Pegawai"), hanya tampil bila
  `can_manage_windu_projects` — membuka modal baru `#winduProjectsModal`.
- **Modal baru `#winduProjectsModal`**: form tambah project (`POST /admin/windu-projects/add`) + tabel
  daftar project dengan tombol Hapus per baris (`POST /admin/windu-projects/<id>/delete`, dengan
  `confirm()` sebelum submit).
- **Field "Perusahaan" di modal Tambah Pegawai**: kalau `is_windu_admin` → `<select required>` berisi
  opsi dari `windu_projects` (+ placeholder "Pilih project..." yang tidak bisa dipilih) — tidak ada
  fallback ketik manual. Kalau bukan admin Windu Karya → tetap `<input type="text">` seperti sebelumnya
  (tidak berubah untuk admin lain/Superadmin).
- **Field "Perusahaan" di modal Edit Pegawai**: sama, `<select>` untuk Admin PT Windu Karya.
- **JS `openEditModal(data)`**: diberi pengaman — kalau pegawai yang sedang diedit punya nilai
  `perusahaan` yang **sudah tidak ada** lagi di daftar `windu_projects` (mis. project-nya baru saja
  dihapus dari daftar oleh orang lain), nilai itu tetap ditampilkan apa adanya di dropdown (ditambahkan
  sebagai opsi ekstra berlabel "(tidak terdaftar)") supaya data pegawai **tidak diam-diam berubah**
  kalau form disimpan ulang tanpa sengaja mengganti pilihan project.

### File baru: `migrasi_windu_projects.sql`
Migrasi manual/cadangan `CREATE TABLE IF NOT EXISTS windu_projects` untuk MySQL, dipakai hanya kalau
user database aplikasi tidak punya hak `CREATE TABLE` (tabel sudah otomatis dibuat saat app start).
Berisi juga contoh `INSERT` opsional (dikomentari) untuk seed daftar project awal lewat SQL langsung.

### `migrasi_2layer_approval.sql` — tambahan di bagian akhir file
Catatan + `UPDATE` opsional (dikomentari) untuk merapikan `perusahaan_induk` pegawai PT Windu Karya
lama, seperti dijelaskan di bagian "Efek samping" di atas.

**Alur pemakaian:** Superadmin atau Admin PT Windu Karya membuka "Kelola Project Windu Karya" → daftarkan
nama project (ditolak kalau kosong/duplikat/sama dengan "Windu Karya") → project langsung muncul di
dropdown "Perusahaan" saat Tambah/Edit Pegawai (khusus Admin PT Windu Karya) → pegawai baru otomatis
tersimpan dengan `perusahaan` = project yang dipilih dan `perusahaan_induk` = "Windu Karya" (otomatis di
belakang layar, sama seperti pola admin holding lain seperti GMI).

## 5. File baru

- **`migrasi_2layer_approval.sql`**: migrasi manual/cadangan untuk 4 kolom approval di tabel
  `transactions`, memakai `information_schema` check (aman dijalankan berulang, kompatibel MySQL 5.7 &
  8.0). Idealnya **tidak perlu dijalankan manual** karena `app/db.py` sudah melakukannya otomatis saat
  aplikasi start (lihat bagian 2) — sediakan hanya untuk kasus user DB tanpa hak `ALTER TABLE`.
- **`PerubahanFiles.md`**: file ini.
- **`migrasi_windu_projects.sql`**: migrasi manual/cadangan `CREATE TABLE windu_projects` (lihat
  bagian 4b) — juga tidak perlu dijalankan manual kecuali user DB tanpa hak `CREATE TABLE`.

---

## 6. Hal yang PERLU diputuskan / ditindaklanjuti (belum dikerjakan)

1. **Transaksi `on-proses` lama** (sebelum migrasi ini di-deploy) akan otomatis dianggap "menunggu Admin
   PT" begitu kolom baru dibuat (`admin_approved_at` = NULL untuk semua baris lama). Ini konsisten dengan
   aturan baru (harus admin approve dulu, kecuali PT tanpa admin), tapi bila ada antrian lama yang mau
   di-skip langsung ke Superadmin, jalankan opsi UPDATE manual di `migrasi_2layer_approval.sql` (bagian
   paling bawah, sengaja dikomentari, harus dipilih & disesuaikan sendiri).
2. Invoice belum menampilkan "nominal diterima setelah dipotong/ditambah fee" karena skema `nominal` vs
   `admin_fee` di tabel `transactions` disimpan terpisah (fee tidak memotong `nominal`) — bila polanya
   berbeda dari asumsi ini, beri tahu supaya baris invoice disesuaikan.
3. Definisi "khusus PT Windu" vs "umum" yang saya pakai: PT lain boleh Superadmin approve langsung *hanya*
   kalau PT itu memang tak punya akun Admin; PT Windu Karya **tidak pernah** boleh dilompati siapa pun
   selain Admin PT Windu Karya sendiri. Nama perusahaan dicocokkan **persis** `"Windu Karya"`
   (case-insensitive, trim spasi) lewat kolom `admins.company` / `pegawai.perusahaan` /
   `pegawai.perusahaan_induk` yang sudah ada — tidak ada perubahan struktur DB.
4. **Daftar project PT Windu Karya masih kosong** — `migrasi_windu_projects.sql` sengaja tidak diisi
   seed data karena daftarnya belum diberikan. Superadmin atau Admin PT Windu Karya perlu mendaftarkan
   project pertama lewat tombol "Kelola Project Windu Karya" setelah deploy (atau isi lewat `INSERT`
   manual di file SQL tsb).
5. Kalau di database production PT Windu Karya sudah punya pegawai lama dengan `perusahaan` diisi
   literal "Windu Karya" (bukan nama project), jalankan UPDATE opsional di
   `migrasi_2layer_approval.sql` (bagian paling akhir) supaya mereka tidak hilang dari dashboard Admin
   PT Windu Karya — lihat penjelasan "Efek samping" di bagian 4b.

---

## 7. Pengujian yang sudah dilakukan

Karena tidak ada akses jaringan/DB MySQL sungguhan di sandbox ini, pengujian dilakukan dengan:
- **Migrasi otomatis kolom** on-the-fly di database uji (SQLite meniru skema `transactions`/`pegawai`/dst
  dari `gajiku_db.sql`) — dikonfirmasi ke-4 kolom baru muncul saat aplikasi Flask di-start.
- **Flask test client** (tanpa server sungguhan) mensimulasikan login Superadmin, 4 Admin PT berbeda
  (GMI, PT Windu, Springhill, admin lama `admin@example.com`), dan 1 pegawai, dengan skenario:
  - Superadmin approve transaksi PT **sebelum** admin approve → **ditolak** untuk PT yang punya admin
    (GMI/Windu/Springhill), **diizinkan** untuk PT tanpa admin.
  - Admin hanya bisa approve/tolak transaksi PT dalam cakupannya (dicoba approve PT lain → ditolak, tidak
    tercatat).
  - Admin approve → status tetap `on-proses`, tercatat `admin_approved_by`; admin tidak bisa reject
    setelah approve sendiri.
  - Superadmin approve setelah admin approve → status jadi `sukses`, tercatat `final_approved_by`.
  - Admin global lama (`admin@example.com`) **tidak bisa** approve transaksi PT Windu Karya (khusus).
  - Admin PT Windu Karya bisa approve transaksi Windu Karya; tombol di dashboard Superadmin berubah jadi
    "Approve & Transfer"; setelah admin Windu Karya approve, Superadmin approve → sukses.
  - **Exact match, bukan substring**: Admin PT dengan nama mirip tapi berbeda (uji pakai
    "PT Winduaji Sejahtera") **tidak bisa** approve transaksi PT Windu Karya, dan sebaliknya Admin PT
    Windu Karya **tidak bisa** approve transaksi PT bernama mirip tsb — keduanya diperlakukan sebagai PT
    biasa (bukan PT Windu Karya), sesuai exact-match `WINDU_COMPANY_NAME = "Windu Karya"`.
  - Superadmin bisa reject kapan saja (tanpa terikat layer); Superadmin memanggil endpoint `/admin/tx/.../approve`
    langsung → ditolak (tidak boleh melompati layer 1 lewat endpoint admin).
  - Fee admin: input manual `"12.500"` tersimpan sebagai `12500`; kosong ditolak (saat tambah); nilai
    di atas batas (`99999999`) ditolak; nilai `0` diterima; update dengan fee manual baru tersimpan benar
    (`17.500` → `17500`).
  - Halaman `admin_pegawai.html` render 200, memuat `<input id="add_admin_fee_flat">` dan **tidak lagi**
    memuat radio `admin_fee_flat` aktif di form tambah.
  - Halaman `tarik_gaji.html` render 200 untuk pegawai dengan fee custom (`12.500`) → dikonfirmasi
    `ADMIN_FEE_REG` di JS terisi `12500` (bukan default 15000); modal invoice (`#invoiceModal`,
    `#invConfirm`, teks "Lihat Invoice") ada di HTML; data `INV_INFO` (ID pegawai & perusahaan) terisi
    benar dari DB.
  - **Fitur dropdown project PT Windu Karya** (skenario terpisah, total 19 pemeriksaan, semua lolos):
  halaman Kelola Pegawai utk Admin PT Windu Karya menampilkan dropdown (bukan free text) + tombol
  "Kelola Project"; Admin PT lain (GMI) tidak melihat tombol itu & field-nya tetap free text; Admin PT
  lain ditolak saat mencoba daftar project; tambah pegawai ditolak selama project belum terdaftar;
  project berhasil didaftarkan oleh Admin Windu Karya maupun Superadmin; duplikat (beda kapitalisasi/
  spasi) ditolak; nama project yang sama dengan "Windu Karya" ditolak; setelah project terdaftar,
  tambah pegawai berhasil dengan `perusahaan_induk` otomatis terisi "Windu Karya"; mengirim nama
  project fiktif langsung lewat POST (melewati dropdown) tetap ditolak di server; transaksi pegawai
  dengan project tsb tetap dikenali sebagai PT Windu Karya di alur approval 2 layer (badge muncul, Admin
  GMI tidak bisa approve, Admin Windu Karya bisa); project yang masih dipakai pegawai gagal dihapus;
  project yang tidak dipakai berhasil dihapus lalu hilang dari dropdown.
- Seluruh skenario approval 2-layer & fee manual dari pengujian sebelumnya **dijalankan ulang** setelah
  perubahan `_is_parent_company()` — 35 pemeriksaan, semua lolos, memastikan konsolidasi 10 titik kode
  duplikat tidak mengubah perilaku PT lain (GMI, Springhill, admin global lama, dst).
- Sintaks JavaScript hasil ekstraksi `<script>` di `tarik_gaji.html` diverifikasi valid (`node --check`),
    dan `computeFee()` diuji terpisah di Node — hasil untuk kasus REG & URG sesuai ekspektasi (fee flat +
    PPN 11%, split REG/URG saldo harian).
- Seluruh `app/routes/web.py` diverifikasi lolos `ast.parse` (tidak ada error sintaks Python).

**Belum diuji** (di luar jangkauan sandbox ini): jalur MySQL sungguhan (hanya diverifikasi lewat pembacaan
skema `gajiku_db.sql` + SQL migrasi ditulis kompatibel MySQL 5.7/8.0), tampilan visual di browser asli
(hanya dicek lewat HTML yang dirender, bukan screenshot), dan job background (`app/tasks`) yang mengubah
status transaksi otomatis (mis. auto-cancel) — perlu dicek apakah ada job lain yang mengasumsikan status
`on-proses` langsung berarti "belum diapprove siapa pun"; dengan skema baru, `on-proses` bisa juga berarti
"sudah admin-approve, menunggu superadmin", jadi job semacam itu sebaiknya diperiksa ulang bila ada.

---

## 8. UMUM — Import Pegawai Massal lewat Excel (.xlsx)

Fitur baru: admin (semua level) bisa menambahkan banyak pegawai sekaligus lewat 1 file Excel, alih-alih
satu-satu lewat form Tambah Pegawai. Aturan bisnis yang sudah ada (fee manual, PT Windu Karya, holding
company) tetap berlaku sama persis untuk import massal ini — tidak ada jalur pintas.

### Dependency baru
`requirements.txt` — ditambah `openpyxl` (baca/tulis file .xlsx). Sudah tersedia & teruji di sandbox ini
(v3.1.5); pastikan `pip install -r requirements.txt` dijalankan ulang saat deploy.

### `app/routes/web.py`
- Import baru di baris atas: `from openpyxl import Workbook, load_workbook`,
  `from openpyxl.worksheet.datavalidation import DataValidation`,
  `from openpyxl.styles import Font, PatternFill, Alignment`.
- **`GET /admin/pegawai/import/template`** (`admin_pegawai_import_template`): membuat & mengunduh file
  Excel template on-the-fly (tidak disimpan permanen di server, dibuat ulang tiap diminta) berisi:
  - Sheet "Import Pegawai": baris header (13 kolom, urutan lihat di bawah) + 1 baris contoh data.
  - Dropdown data-validation di kolom Status Aktif (Aktif/Nonaktif) dan Siklus Gaji (A/B/C/D).
  - Khusus Admin PT Windu Karya: kolom Perusahaan/Project dapat dropdown otomatis berisi daftar
    project resmi yang sudah terdaftar (sumbernya sheet tersembunyi `_daftar_project`).
  - Sheet "Petunjuk": penjelasan cara isi tiap kolom, termasuk catatan khusus untuk Admin PT Windu Karya.
- **`POST /admin/pegawai/import`** (`admin_pegawai_import`): terima upload file, validasi:
  - Ekstensi harus `.xlsx`, ukuran maksimal 5 MB, maksimal `IMPORT_MAX_ROWS = 500` baris data.
  - Baris kosong total (sering muncul di akhir file Excel) otomatis dilewati, bukan dianggap error.
  - Setiap baris divalidasi dengan **fungsi yang sama** dengan form Tambah Pegawai manual
    (`employee_id_is_valid`, `ewallet_is_valid`, `parse_admin_fee_flat`, `normalize_siklus`,
    `is_windu_company`/`windu_project_exists`, `_is_parent_company`) — supaya aturan bisnis konsisten
    antara input satu-satu dan import massal:
    - ID Pegawai/Nama/Email wajib diisi, ID maksimal 16 karakter alfanumerik.
    - Cek duplikat ID & email — baik terhadap data yang **sudah ada di database**, maupun duplikat
      **antar baris di dalam file yang sama**.
    - Admin PT biasa (bukan holding/Windu Karya): kolom Perusahaan di file **diabaikan**, otomatis
      dipaksa sesuai PT admin yang login (sama seperti form manual).
    - Admin holding (GMI) / Admin PT Windu Karya: kolom Perusahaan **wajib diisi**; khusus Windu Karya,
      wajib salah satu dari daftar resmi `windu_projects` (baris ditolak kalau belum terdaftar).
    - Admin biasa: kolom Admin Fee di file **selalu diabaikan**, dipaksa default 15.000.
    - Superadmin: kolom Admin Fee dipakai per baris (parse manual, 0–1.000.000); kosong → default
      15.000, terisi tapi tidak valid → baris ditolak dengan pesan jelas.
  - **Baris valid tetap diimport** walau ada baris lain yang gagal (bukan all-or-nothing) — insert
    dilakukan sekali lewat `executemany` untuk semua baris valid, baru di-commit.
  - Hasil ditampilkan lewat flash message: ringkasan (berapa berhasil/gagal dari total), lalu detail
    per baris gagal (nomor baris + alasan), dibatasi maksimal 20 detail ditampilkan (sisanya diringkas
    jadi satu baris "...dan N baris lain juga gagal").

### `templates/admin_pegawai.html`
- Tombol baru **"Import Excel"** di toolbar atas (sebelah "+ Pegawai"), membuka modal `#importModal`.
- Modal berisi: link **"Download Template Excel"** (ke route template di atas) + form upload
  (`<input type="file" accept=".xlsx">`) yang POST ke `/admin/pegawai/import`.
- JS: `openImportModal()` + modal ini didaftarkan ke wiring close-on-click-luar & tombol Escape yang
  sudah ada (bersama `addModal`, `editModal`, `winduProjectsModal`).

### Struktur kolom template (baris 1 = header, data mulai baris 2)

| Kolom | Nama | Wajib? | Keterangan |
|---|---|---|---|
| A | ID Pegawai* | Ya | Alfanumerik, maks 16 karakter, harus unik |
| B | Nama* | Ya | |
| C | Email* | Ya | Harus mengandung `@`, harus unik |
| D | Jabatan | Tidak | |
| E | Perusahaan/Project | Kondisional | Wajib untuk admin holding (GMI) / Admin PT Windu Karya (harus dari daftar resmi); diabaikan untuk admin PT biasa |
| F | No Rekening Bank Utama | Tidak | |
| G | No Rekening Bank Lain | Tidak | |
| H | Rekening E-Wallet | Tidak | Hanya huruf, angka, spasi |
| I | No Telp | Tidak | |
| J | Gaji Pokok | Tidak | Angka, default 0 |
| K | Status Aktif | Tidak | "Aktif"/"Nonaktif" (dropdown), kosong = Aktif |
| L | Siklus Gaji | Tidak | A/B/C/D (dropdown), kosong = B |
| M | Admin Fee | Kondisional | Hanya dipakai kalau yang upload Superadmin; diabaikan untuk admin biasa |

### Pengujian yang dilakukan (15 skenario, semua lolos)
Download template untuk 3 peran (Admin PT biasa, Admin PT Windu Karya, Superadmin) — semua 200 &
ukuran file wajar; import Admin GMI 2 baris (kolom Perusahaan & Admin Fee di file sengaja diisi salah
untuk memastikan tetap dipaksa/diabaikan sesuai aturan, status Aktif/Nonaktif & siklus terbaca benar);
batch berisi campuran baris valid & tidak (ID duplikat dengan DB, email duplikat antar baris, ID kosong,
e-wallet format salah) — dipastikan hanya baris valid yang masuk, sisanya ditolak dengan alasan spesifik;
Admin PT Windu Karya mencoba import dengan project yang belum terdaftar → ditolak, setelah didaftarkan
lewat "Kelola Project Windu Karya" → baris berhasil masuk dengan `perusahaan_induk` otomatis "Windu
Karya"; Superadmin import dengan fee manual per baris → tersimpan sesuai isian; file bukan `.xlsx`
ditolak; file kosong (cuma header) ditangani tanpa error/crash. Seluruh regresi sebelumnya (fee manual,
approval 2 layer, dropdown project Windu Karya — total 55 skenario) dijalankan ulang, semua tetap lolos.

### Yang perlu diperhatikan ke depan
- Batas 500 baris & 5 MB per file bersifat pengaman default — sesuaikan `IMPORT_MAX_ROWS` /
  `IMPORT_MAX_FILE_BYTES` di `web.py` kalau volume data pegawai memang jauh lebih besar dari itu.
- File template dibuat sementara di `/tmp/gajiku_import/` lalu dikirim via `send_file` — tidak
  dibersihkan otomatis (file menumpuk seiring waktu kalau fitur ini sering dipakai). Kalau server
  jarang direstart, sebaiknya tambahkan pembersihan berkala folder ini.
- Import ini murni menambah baris baru ke tabel `pegawai` (sama seperti form manual) — **tidak**
  menyentuh tabel `users`/`user_accounts` (akun login pegawai tetap dibuat sendiri lewat halaman
  Register), konsisten dengan alur yang sudah ada sebelumnya.

---

## 9. UMUM — Hak Approval per-Admin (toggle) + Modul "Menunggu Transfer" di Dashboard Superadmin

Fitur baru: Superadmin sekarang menentukan **admin mana saja** (per orang, bukan per PT) yang boleh
approve/tolak pengajuan tarik gaji. Dashboard Superadmin juga dipecah jadi 2 tahap visual: "REG/URG
On-Proses" (masih perlu admin approve, atau memang PT tanpa admin ber-hak approval) dan "Menunggu
Transfer" (admin sudah approve, tinggal Superadmin transfer manual lalu klik **Done**).

### Kebijakan default (disengaja, bukan bug)
**Semua admin — lama maupun baru — defaultnya `hak_approval = OFF`.** Efeknya: begitu fitur ini
di-deploy, SEMUA PT (kecuali PT Windu Karya) otomatis kembali ke perilaku "bypass langsung ke
Superadmin" — persis seperti sebelum ada layer approval admin. Sistem **tidak berubah** sampai
Superadmin secara sadar menyalakan toggle untuk admin tertentu. **Kecuali PT Windu Karya**, yang tidak
pernah bypass apa pun kondisinya — jadi begitu fitur ini aktif, transaksi PT Windu Karya **akan
langsung macet** sampai Superadmin menyalakan `hak_approval` untuk minimal 1 admin PT Windu Karya.
Ini bukan bug, tapi **wajib jadi langkah manual pertama setelah deploy**.

### `app/db.py`
- Fungsi baru `ensure_hak_approval_column(db)`: menambah kolom `admins.hak_approval` (INTEGER/TINYINT,
  default 0) otomatis saat aplikasi start, kompatibel SQLite & MySQL, idempotent. Dipanggil di
  `ensure_db()` bersama migrasi kolom lain.

### `app/routes/web.py`
- **`load_admin_companies(db)`** (±baris 347): query diubah, sekarang HANYA menghitung perusahaan yang
  punya admin dengan `hak_approval = 1` (bukan lagi "punya akun admin" apa pun). Inilah yang membuat
  seluruh alur "PT tanpa admin -> bypass" otomatis ikut berlaku untuk "PT tanpa admin BER-HAK
  APPROVAL" tanpa perlu ubah logic di tempat lain (`superadmin_final_gate` dan `decorate_pending_tx`
  memakai fungsi ini apa adanya).
- **`admin_can_handle()`** (±baris 373): ditambah gerbang paling awal —
  `if not session.get("hak_approval"): return False`. Berlaku universal, termasuk untuk admin global
  lama (`admin@example.com`): tanpa toggle ON, admin manapun **tidak pernah** bisa approve/tolak,
  apa pun PT-nya.
- **Login (`login()`, ±baris 690 & 745)**: kedua jalur login admin (dari tabel `admins`, dan dari ENV
  `ADMIN_USERNAME`/`ADMIN_PASSWORD`) sekarang menyimpan `session["hak_approval"]` — untuk jalur ENV,
  dicoba dicocokkan dulu ke tabel `admins` berdasarkan email (kalau ada baris yang cocok, hak
  approval-nya ikut baris itu; kalau tidak ada, default OFF).
- **`admin_tx_approve`/`admin_tx_reject`** (±baris 3043 & 3078): ditambah pengecekan eksplisit di awal
  — kalau `session["hak_approval"]` falsy, langsung flash pesan jelas ("Anda tidak memiliki hak
  approval tarik gaji. Hubungi Superadmin...") alih-alih pesan generik "bukan milik perusahaan Anda"
  yang jadi menyesatkan sejak ada fitur ini.
- **`superadmin_dashboard()`** (±baris 3474): setelah `decorate_pending_tx`, daftar `pending_reg` dan
  `pending_urg` **dipecah**: item dengan `stage == 'menunggu_superadmin'` (admin sudah approve)
  dipindah ke list baru `pending_transfer` (gabungan REG+URG, ditandai `product_label`); sisanya
  (`stage == 'menunggu_admin'`, mencakup baik yang benar-benar menunggu admin MAUPUN yang bypass)
  tetap di `pending_reg`/`pending_urg` seperti biasa. `pending_transfer` dikirim ke template.
- **`superadmin_admins()`** (list Kelola Admin): SELECT ditambah kolom `hak_approval`.
- **`superadmin_edit_admin()`**: menangkap checkbox `hak_approval` dari form; kalau admin yang diedit
  adalah admin PT Windu Karya **satu-satunya** yang `hak_approval`-nya masih ON dan mau dimatikan,
  ditampilkan **flash warning** tegas (bukan diblokir — tetap disimpan sesuai keputusan bisnis).
  Sekalian diperbaiki: pesan error exception mentah (`{e}`) yang dulu bocor ke UI diganti log server +
  pesan generik (temuan Audit #16).
- **`superadmin_admins_add()`**: menangkap checkbox `hak_approval` (default 0 kalau tidak dicentang).
  Sekalian diperbaiki: `NOW()` (MySQL-only, pecah di SQLite - temuan Audit #11) diganti
  `datetime.now().isoformat(...)` supaya konsisten dengan pola di tempat lain & bisa diuji di SQLite.

### `templates/admin_dashboard.html`
- Teks fallback saat tombol Approve/Tolak tidak muncul dipecah jadi 2 kondisi: **"Anda tidak punya hak
  approval"** (kalau memang PT-nya sendiri tapi toggle OFF) vs **"Di luar cakupan Anda"** (kalau
  memang bukan PT yang dia kelola) — sebelumnya kedua kasus ini salah kaprah ditampilkan sama.

### `templates/superadmin_dashboard.html`
- Tombol Approve di tabel REG/URG On-Proses disederhanakan jadi selalu **"Approve"** (varian label
  "Approve & Transfer" khusus Windu dihapus — sudah tidak relevan karena Windu tidak pernah muncul di
  sini dengan tombol aktif; begitu admin approve, PASTI pindah ke Menunggu Transfer). Ditambah
  `confirm()` yang menjelaskan bahwa klik Approve di sini **langsung** menandai sukses (karena PT-nya
  memang tidak punya admin ber-hak approval — Superadmin berperan admin+transfer sekaligus).
- **Section baru "Menunggu Transfer"**: kolom tabelnya **persis sama** dengan REG/URG On-Proses (Waktu,
  ID Pegawai, Nama, Project, Nominal, Admin, Rekening Tujuan, No Telp, Tahap Approval, Aksi) — memakai
  ulang macro `tahap()` yang sama persis (otomatis menampilkan badge "Approved Admin" + nama admin yang
  approve + label "PT Windu" kalau relevan, karena semua baris di sini memang berstatus
  `menunggu_superadmin`). Satu-satunya beda ada di kolom Aksi: tombol **"Done"** (bukan "Approve")
  memanggil endpoint `superadmin_tx_approve` yang sama — tidak ada route baru, logikanya memang
  identik (set `sukses` + catat `final_approved_at/by`) — dan **"Tolak"** (endpoint
  `superadmin_tx_reject` yang sama, tetap bisa dipakai kapan saja).

### `templates/superadmin_admins.html`
- Kolom baru **"Hak Approval"** di tabel Kelola Admin (badge ON/OFF).
- Checkbox **"Beri hak approve/tolak tarik gaji"** di form Tambah Admin (default tidak dicentang) dan
  form Edit Admin (otomatis tercentang sesuai data saat modal dibuka).

### Pengujian (22 skenario baru, semua lolos)
Kolom `hak_approval` otomatis termigrasi; admin default OFF tidak melihat tombol approve & mendapat
pesan jelas; mencoba approve manual tetap ditolak di server; PT tanpa admin ber-hak-approval (bypass)
tetap bisa langsung di-approve Superadmin dan langsung sukses (skip Menunggu Transfer); PT Windu Karya
tetap macet (tidak bypass) walau semua adminnya OFF; Superadmin menyalakan toggle untuk 1 admin di PT
yang punya 2 admin → hanya admin yang di-ON-kan yang bisa approve, satunya tetap ditolak; setelah
admin approve, transaksi hilang dari REG/URG dan muncul di Menunggu Transfer dengan tombol Done;
klik Done → baru sukses; skenario sama untuk PT Windu Karya (approve admin → Menunggu Transfer →
Done); mematikan hak_approval admin PT Windu Karya terakhir memicu warning tapi tetap tersimpan;
tambah admin baru lewat form dengan/tanpa checklist tersimpan sesuai; halaman Kelola Admin menampilkan
kolom baru. Seluruh regresi sebelumnya (fee manual, 2-layer approval lama, dropdown project Windu
Karya, import Excel — total 71 skenario) dijalankan ulang dengan fixture disesuaikan (admin lama diberi
`hak_approval=1` secara eksplisit di data uji, merepresentasikan kondisi "Superadmin sudah menyalakan
toggle" — bukan perubahan logic), semua tetap lolos.

### Yang perlu diperhatikan ke depan
- **Wajib dilakukan segera setelah deploy**: nyalakan `hak_approval` untuk minimal 1 admin PT Windu
  Karya lewat Kelola Admin, kalau tidak transaksi PT Windu Karya akan macet total (lihat "Kebijakan
  default" di atas).
- Perubahan toggle **baru berlaku setelah admin yang bersangkutan login ulang** (nilai disimpan di
  session saat login, bukan dicek ulang ke DB tiap request) — konsisten dengan pola session lain yang
  sudah ada di aplikasi ini (mis. `company`), bukan hal baru yang saya perkenalkan.
- Warning "admin Windu Karya terakhir" murni peringatan, tidak memblokir — sesuai keputusan bisnis yang
  diminta.
