-- Konfigurasi cutoff mingguan per company pada tabel admins.
-- Jalankan sekali pada database MariaDB production.
ALTER TABLE admins
  ADD COLUMN IF NOT EXISTS cutoff_mingguan_aktif TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS cutoff_hari TINYINT NOT NULL DEFAULT 2;

-- Nilai cutoff_hari: 0 Senin, 1 Selasa, 2 Rabu, 3 Kamis,
-- 4 Jumat, 5 Sabtu, 6 Minggu.
