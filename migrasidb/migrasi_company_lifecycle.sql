CREATE TABLE IF NOT EXISTS client_billing (
    client_id INT PRIMARY KEY,
    due_day INT NOT NULL,
    first_due DATE NOT NULL,
    blocked_at DATETIME NULL,
    activated_at DATETIME NULL,
    updated_by VARCHAR(191) NOT NULL,
    updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS client_bills (
    id INT AUTO_INCREMENT PRIMARY KEY,
    client_id INT NOT NULL,
    period VARCHAR(7) NOT NULL,
    due_on DATE NOT NULL,
    paid_at DATETIME NULL,
    paid_by VARCHAR(191) NULL,
    created_at DATETIME NOT NULL,
    UNIQUE KEY uq_client_bills_period (client_id, period)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS client_mou_actions (
    client_id INT NOT NULL,
    period_key VARCHAR(64) NOT NULL,
    action VARCHAR(16) NOT NULL,
    acted_by VARCHAR(191) NOT NULL,
    acted_at DATETIME NOT NULL,
    PRIMARY KEY (client_id, period_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS notifications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_key VARCHAR(191) NOT NULL,
    client_id INT NOT NULL,
    kind VARCHAR(32) NOT NULL,
    audience VARCHAR(16) NOT NULL,
    period_key VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    resolved_at DATETIME NULL,
    UNIQUE KEY uq_notifications_event (event_key),
    KEY ix_notifications_client (client_id, audience, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS notification_reads (
    notification_id INT NOT NULL,
    principal VARCHAR(191) NOT NULL,
    read_at DATETIME NOT NULL,
    PRIMARY KEY (notification_id, principal)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
