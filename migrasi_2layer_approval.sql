-- =============================================================================
-- Migrasi: kolom 2-layer approval pada tabel `transactions`
-- =============================================================================
-- CATATAN: app/db.py sudah otomatis menambahkan kolom ini saat aplikasi start
-- (lihat fungsi ensure_approval_columns()), JADI file ini opsional / cadangan.
-- Jalankan manual HANYA bila:
--   - user database yang dipakai aplikasi TIDAK punya hak ALTER TABLE, atau
--   - ingin memverifikasi struktur kolom secara eksplisit sebelum deploy.
--
-- Aman dijalankan berulang: setiap ALTER dibungkus cek "kolom belum ada"
-- lewat information_schema (kompatibel MySQL 5.7 & 8.0, tidak memakai
-- `ADD COLUMN IF NOT EXISTS` yang baru didukung MySQL 8.0.29+).
-- =============================================================================

DELIMITER $$

CREATE PROCEDURE gajiku_add_approval_columns()
BEGIN
  IF NOT EXISTS (
      SELECT 1 FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'transactions'
        AND COLUMN_NAME = 'admin_approved_at'
  ) THEN
    ALTER TABLE `transactions` ADD COLUMN `admin_approved_at` DATETIME NULL
      COMMENT 'Waktu Admin PT approve (layer 1). NULL = belum di-approve admin.';
  END IF;

  IF NOT EXISTS (
      SELECT 1 FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'transactions'
        AND COLUMN_NAME = 'admin_approved_by'
  ) THEN
    ALTER TABLE `transactions` ADD COLUMN `admin_approved_by` VARCHAR(255) NULL
      COMMENT 'Nama/email Admin PT yang approve layer 1.';
  END IF;

  IF NOT EXISTS (
      SELECT 1 FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'transactions'
        AND COLUMN_NAME = 'final_approved_at'
  ) THEN
    ALTER TABLE `transactions` ADD COLUMN `final_approved_at` DATETIME NULL
      COMMENT 'Waktu Superadmin approve final / transfer (layer 2).';
  END IF;

  IF NOT EXISTS (
      SELECT 1 FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'transactions'
        AND COLUMN_NAME = 'final_approved_by'
  ) THEN
    ALTER TABLE `transactions` ADD COLUMN `final_approved_by` VARCHAR(255) NULL
      COMMENT 'Nama/email Superadmin yang approve final layer 2.';
  END IF;
END$$

DELIMITER ;

CALL gajiku_add_approval_columns();
DROP PROCEDURE gajiku_add_approval_columns;

-- -----------------------------------------------------------------------------
-- OPSIONAL: data lama.
-- Transaksi berstatus 'on-proses' yang SUDAH ADA sebelum migrasi ini akan
-- otomatis dianggap "menunggu Admin PT" (admin_approved_at masih NULL), KECUALI
-- untuk PT yang belum punya akun admin (sistem izinkan Superadmin approve
-- langsung). Tidak perlu UPDATE manual, tapi jika ingin men-skip layer 1
-- untuk transaksi lama secara massal (mis. migrasi mendadak saat sudah ada
-- antrian lama), jalankan salah satu opsi di bawah dan SESUAIKAN dulu sebelum
-- dieksekusi -- JANGAN dijalankan begitu saja secara default:
--
-- -- Opsi A: anggap semua transaksi lama sudah "di-approve admin" oleh sistem,
-- --         supaya Superadmin bisa langsung approve final tanpa menunggu admin:
-- -- UPDATE `transactions`
-- --   SET admin_approved_at = NOW(), admin_approved_by = 'migrasi-otomatis'
-- --   WHERE status = 'on-proses' AND admin_approved_at IS NULL;
-- =============================================================================

-- =============================================================================
-- WAJIB DICEK bila mengaktifkan fitur "PT Windu Karya selalu induk/holding"
-- (lihat migrasi_windu_projects.sql untuk tabel & fitur dropdown project-nya)
-- =============================================================================
-- Sejak PT Windu Karya diperlakukan SELALU sebagai perusahaan induk (bukan lagi dideteksi
-- dinamis dari data), semua query scoping Admin (dashboard/riwayat/daftar pegawai) beralih
-- memfilter berdasarkan kolom `perusahaan_induk = 'Windu Karya'` (bukan lagi `perusahaan =
-- 'Windu Karya'`). Kalau di database PRODUCTION sudah ada pegawai PT Windu Karya LAMA yang
-- kolom `perusahaan`-nya diisi literal "Windu Karya" TANPA `perusahaan_induk` terisi, mereka
-- akan HILANG dari dashboard Admin PT Windu Karya (walau transaksinya tetap tervalidasi benar
-- sebagai PT Windu Karya di alur approval, lewat pengecekan is_windu_company).
--
-- Jalankan UPDATE berikut SEKALI setelah deploy, untuk merapikan data lama tsb supaya kembali
-- muncul normal di dashboard (perusahaan_induk diisi "Windu Karya", nilai `perusahaan` yang
-- sudah ada TIDAK diubah/dihapus):
--
-- UPDATE `pegawai`
--   SET `perusahaan_induk` = 'Windu Karya'
--   WHERE LOWER(TRIM(`perusahaan`)) = 'windu karya'
--     AND (`perusahaan_induk` IS NULL OR TRIM(`perusahaan_induk`) = '');
--
-- Setelah itu, pertimbangkan mengubah nilai `perusahaan` pegawai tsb dari literal "Windu Karya"
-- menjadi nama project yang sebenarnya (didaftarkan dulu lewat menu "Kelola Project Windu
-- Karya"), supaya konsisten dengan pegawai baru yang ditambahkan lewat dropdown project.
-- =============================================================================
