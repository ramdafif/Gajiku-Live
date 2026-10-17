# Template Gajiku

Template dikelompokkan berdasarkan layout, role, dan komponen bersama.
Perubahan struktur ini tidak mengubah route, form, endpoint, atau backend.

## Struktur utama

```text
templates/
  layouts/
    base.html
    auth.html
  auth/
    login.html
    register.html
    forgot.html
    reset_password.html
    verify_registration.html
  employee/
    dashboard.html
    tarik_gaji.html
    riwayat.html
    settings.html
  admin/
    dashboard.html
    pegawai.html
    riwayat.html
    settings.html
  superadmin/
    dashboard.html
    admins.html
    clients.html
    riwayat.html
    settings.html
  shared/
    management_dashboard.html
    transaction_history.html
    account_settings.html
    client_detail.html
  components/
    ...
  README.md
```

## Tanggung jawab folder

- `layouts/`: kerangka aplikasi, navigasi, dan layout autentikasi.
- `auth/`: masuk, daftar, pemulihan password, dan verifikasi email.
- `employee/`: halaman pegawai.
- `admin/`: halaman admin. `pegawai.html` juga digunakan Superadmin melalui endpoint yang sama.
- `superadmin/`: halaman khusus Superadmin.
- `shared/`: isi halaman yang digunakan beberapa role, tanpa menduplikasi markup.
- `components/`: macro, ikon, notifikasi, form, antrean persetujuan, dan dialog yang digunakan ulang.

## Alias kompatibilitas di root

File HTML di root hanya berisi satu perintah `extends`. File tersebut mempertahankan
nama template yang masih dipanggil backend, sehingga backend tidak perlu diubah.

Contoh: `admin_dashboard.html` meneruskan ke `admin/dashboard.html`, lalu menggunakan
isi bersama dari `shared/management_dashboard.html`.

Edit isi halaman di subfolder, bukan di alias root. Gunakan path lengkap dari root
template untuk `extends`, `include`, dan import macro, misalnya `layouts/base.html`
atau `components/macros.html`.

Alias root dapat dihapus setelah pemanggilan `render_template(...)` di backend
resmi dipindahkan ke path baru. Itu bukan bagian dari perubahan ini.

## Validasi

Jalankan suite regresi dari root project dengan `python -m pytest -q`.
Pemeriksaan template harus rekursif dengan `rglob('*.html')`, bukan hanya
`glob('*.html')`, agar layout dan halaman di subfolder ikut diperiksa.
