-- Migrasi: set perusahaan_induk untuk pegawai PT Guixu
-- Aman dijalankan berulang kali.

START TRANSACTION;

UPDATE pegawai
SET perusahaan_induk = 'Guixu'
WHERE LOWER(TRIM(perusahaan)) = 'guixu';

COMMIT;

-- Verifikasi hasil migrasi
SELECT
    COUNT(*) AS total_pegawai_guixu,
    SUM(
        CASE
            WHEN perusahaan_induk = 'Guixu' THEN 1
            ELSE 0
        END
    ) AS sudah_memiliki_perusahaan_induk
FROM pegawai
WHERE LOWER(TRIM(perusahaan)) = 'guixu';
