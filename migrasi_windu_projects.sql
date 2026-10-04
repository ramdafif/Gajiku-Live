-- =============================================================================
-- Migrasi: tabel `windu_projects` (dropdown "Perusahaan" khusus PT Windu Karya)
-- =============================================================================
-- CATATAN: app/db.py sudah otomatis membuat tabel ini saat aplikasi start (lihat fungsi
-- ensure_windu_projects_table()), JADI file ini opsional / cadangan - sama seperti
-- migrasi_2layer_approval.sql. Jalankan manual HANYA bila user database aplikasi TIDAK
-- punya hak CREATE TABLE.
--
-- Tabel ini menyimpan daftar resmi nama project/anak-perusahaan di bawah PT Windu Karya.
-- Admin PT Windu Karya (session `company` = persis "Windu Karya") dan Superadmin bisa
-- mendaftarkan nama baru lewat menu "Kelola Project Windu Karya" di halaman Kelola Pegawai;
-- nama project WAJIB dipilih dari daftar ini (bukan diketik bebas) saat menambah/mengubah
-- pegawai PT Windu Karya, supaya nama project tidak dobel gara-gara typo/salah tulis.
-- =============================================================================

CREATE TABLE IF NOT EXISTS `windu_projects` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `nama_project` VARCHAR(255) NOT NULL,
  `created_by` VARCHAR(255) DEFAULT '',
  `created_at` DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- -----------------------------------------------------------------------------
-- OPSIONAL: seed daftar project awal. Sengaja DIKOSONGKAN karena daftar resminya belum
-- diberikan. Isi & jalankan baris di bawah ini (boleh diulang, tinggal tambah baris VALUES)
-- untuk mendaftarkan project awal secara massal lewat SQL, alih-alih satu-satu lewat UI:
--
-- INSERT INTO `windu_projects` (`nama_project`, `created_by`, `created_at`) VALUES
--   ('Nama Project 1', 'migrasi-awal', NOW()),
--   ('Nama Project 2', 'migrasi-awal', NOW());
--
-- Kalau tidak dijalankan, tidak masalah - Superadmin atau Admin PT Windu Karya tinggal
-- mendaftarkan project pertama lewat tombol "Kelola Project Windu Karya" di halaman
-- Kelola Pegawai setelah deploy.
-- =============================================================================
