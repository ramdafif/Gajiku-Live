-- Izin produk per company admin. Default 1 menjaga perilaku lama (REG dan URG aktif).
ALTER TABLE admins
  ADD COLUMN IF NOT EXISTS produk_reg_aktif TINYINT(1) NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS produk_urg_aktif TINYINT(1) NOT NULL DEFAULT 1;
