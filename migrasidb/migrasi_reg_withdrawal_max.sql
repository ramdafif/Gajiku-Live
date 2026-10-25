SET @gajiku_reg_limit_exists := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'clients'
      AND column_name = 'reg_withdrawal_max'
);
SET @gajiku_reg_limit_ddl := IF(
    @gajiku_reg_limit_exists = 0,
    'ALTER TABLE clients ADD COLUMN reg_withdrawal_max INT NOT NULL DEFAULT 1000000',
    'SELECT 1'
);
PREPARE gajiku_stmt FROM @gajiku_reg_limit_ddl;
EXECUTE gajiku_stmt;
DEALLOCATE PREPARE gajiku_stmt;
