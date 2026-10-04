# Planning Redesign UI/UX GajiKu — Mobile Banking Style

## Arah desain

Mengadaptasi pola visual dari referensi:

- Mobile-first.
- Layout berbasis rounded card.
- Satu fokus utama per layar.
- Informasi penting tampil ringkas dan bertingkat.
- CTA utama lebih menonjol.
- Bottom navigation untuk pegawai di mobile.
- Spacing lebih lega dan tampilan lebih bersih.
- Komponen terasa seperti aplikasi mobile banking modern.

Warna inti GajiKu tetap dipertahankan:

- Navy/dark blue sebagai surface utama.
- Gold sebagai accent dan highlight.
- Cyan/blue sebagai action dan informasi.
- Warna status existing tetap digunakan.
- Dark theme dan theme baru tetap tersedia, tetapi diseragamkan melalui satu design system.

## Perubahan utama

### 1. Fondasi visual

- Buat token warna, spacing, radius, shadow, dan typography.
- Pertahankan warna GajiKu, tetapi kurangi penggunaan gradient dan border dekoratif berlebihan.
- Gunakan radius besar pada card dan modal mengikuti referensi.
- Terapkan hierarchy angka yang kuat untuk saldo, limit, nominal, dan status.
- Standarisasi seluruh button, badge, input, table, alert, dan modal.

### 2. Navigasi pegawai

Pada mobile, gunakan bottom navigation tetap dengan struktur:

- Dashboard.
- Riwayat/transaksi.
- Tarik Gaji sebagai CTA utama.
- Profil/pengaturan.

Ketentuan:

- Bottom nav hanya untuk pegawai.
- Admin dan superadmin tetap menggunakan topbar/menu desktop.
- Navigasi tetap aman pada perangkat dengan safe-area inset.
- Halaman aktif memiliki indikator visual dan label yang jelas.

### 3. Dashboard pegawai

Susun ulang menjadi:

1. Greeting dan profil singkat.
2. Card saldo utama.
3. Card limit atau status pengajuan.
4. CTA besar `Tarik Gaji`.
5. Rekening utama.
6. Riwayat transaksi terbaru.

Card menggunakan pola seperti referensi:

- Satu informasi utama.
- Label kecil.
- Angka besar.
- Supporting text singkat.
- Action yang jelas.

### 4. Flow Tarik Gaji

Flow dibuat bertahap:

1. Pilih produk.
2. Pilih rekening tujuan.
3. Masukkan nominal.
4. Lihat estimasi fee.
5. Review invoice.
6. Konfirmasi pengajuan.

Perubahan UX:

- Pilihan rekening menggunakan card selection.
- Fee teknis disembunyikan dalam bagian detail expandable.
- Total nominal dan potongan dibuat lebih mudah dipahami.
- Error tampil dekat field terkait.
- Tombol submit memiliki loading state.
- Invoice mobile menggunakan layout label di atas value jika layar sempit.

### 5. Dashboard admin dan superadmin

Tetap mempertahankan data operasional, tetapi tampil lebih terstruktur:

- Summary KPI dalam card ringkas.
- Pending approval sebagai action queue utama.
- Status approval dalam workflow visual.
- Grafik dan laporan berada setelah action queue.
- Aksi reset dipindahkan ke area Danger Zone.
- Table desktop tetap dipertahankan, sementara mobile memakai card/list layout.

### 6. Form dan modal

- Form panjang dibagi menjadi beberapa section:
  - Identitas.
  - Pekerjaan.
  - Pembayaran.
  - Pengaturan payroll.
- Modal memakai rounded card, header jelas, dan footer action konsisten.
- Fokus otomatis masuk ke modal.
- Escape menutup modal.
- Fokus dikembalikan ke tombol pembuka.
- Tombol delete/reject/reset memakai style danger terpisah.

### 7. Dua theme

Dark theme dan theme baru tetap dipertahankan.

Keduanya akan menggunakan:

- Struktur komponen yang sama.
- Kontras yang tervalidasi.
- State button dan form yang sama.
- Warna status yang konsisten.
- Tidak ada komponen yang tiba-tiba memakai gaya berbeda antar-theme.

## Batas teknis

- Tetap menggunakan Flask, Jinja template, CSS, dan JavaScript existing.
- Tidak mengganti framework frontend.
- Tidak mengubah database, route, permission, atau aturan bisnis.
- Perubahan difokuskan pada template, stylesheet, komponen UI, dan interaction behavior.
- Asset referensi Google tidak disalin; yang digunakan hanya pola layout dan prinsip UX.

## Pengujian

- Mobile: 320px, 375px, 390px, 430px.
- Tablet: 768px.
- Desktop: 1024px dan 1440px.
- Uji seluruh role:
  - Pegawai.
  - Admin.
  - Superadmin.
- Uji seluruh flow payroll, approval, import, export, modal, dan pengaturan.
- Uji keyboard navigation, focus state, modal accessibility, kontras warna, dan reduced motion.
- Pastikan bottom navigation tidak menutupi konten atau tombol form.

## Kriteria penerimaan

- Tampilan memiliki nuansa mobile banking seperti referensi.
- Warna dan identitas GajiKu tetap terasa jelas.
- Dashboard lebih ringkas dan mudah dipindai.
- CTA utama mudah ditemukan.
- Navigasi pegawai mobile menggunakan bottom navigation.
- Semua fungsi existing tetap berjalan.
- Dark theme dan theme baru tetap tersedia.
- Tidak ada overflow atau layout rusak di mobile.
- UI konsisten antara halaman pegawai, admin, dan superadmin.
