-- =============================================================================
-- Migrasi: kolom `admins.hak_approval` (fitur toggle hak approve/tolak per admin)
-- =============================================================================
-- CATATAN: app/db.py sudah otomatis menambahkan kolom ini saat aplikasi start (lihat fungsi
-- ensure_hak_approval_column()), JADI file ini opsional/cadangan - sama seperti 2 file migrasi
-- lain di folder ini. Jalankan manual HANYA bila user database aplikasi TIDAK punya hak
-- ALTER TABLE (auto-migrasi di atas akan gagal diam-diam dan dicatat di log aplikasi saja).
--
-- Default 0 (OFF) untuk SEMUA admin, termasuk admin yang sudah ada sebelumnya - supaya begitu
-- migrasi ini dijalankan, perilaku sistem TIDAK berubah (semua PT tetap bypass langsung ke
-- Superadmin seperti sebelum fitur approval-admin ada), KECUALI PT Windu Karya yang memang
-- tidak pernah bypass apa pun kondisinya.
-- =============================================================================

DELIMITER $$

CREATE PROCEDURE gajiku_add_hak_approval_column()
BEGIN
  IF NOT EXISTS (
      SELECT 1 FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'admins'
        AND COLUMN_NAME = 'hak_approval'
  ) THEN
    ALTER TABLE `admins` ADD COLUMN `hak_approval` TINYINT(1) NOT NULL DEFAULT 0
      COMMENT 'Hak approve/tolak tarik gaji (layer 1). 1=boleh, 0=tidak (default).';
  END IF;
END$$

DELIMITER ;

CALL gajiku_add_hak_approval_column();
DROP PROCEDURE gajiku_add_hak_approval_column;

-- =============================================================================
-- WAJIB DILAKUKAN SETELAH MIGRASI INI (lewat UI "Kelola Admin", bukan SQL):
-- Nyalakan hak_approval untuk MINIMAL 1 admin PT Windu Karya. Tanpa ini, begitu fitur ini aktif,
-- SEMUA transaksi PT Windu Karya macet total (tidak ada yang bisa approve) - lihat penjelasan
-- lengkap di PerubahanFiles.md bagian 9 dan Audit.md.
--
-- Kalau mau langsung lewat SQL (darurat, sebelum sempat buka UI), contoh:
-- UPDATE admins SET hak_approval = 1 WHERE LOWER(TRIM(email)) = LOWER('email_admin_windu@contoh.com');
-- =============================================================================
