-- E1 disposable databases (run once in MySQL Workbench as an admin account).
-- Explicit names only; c3_tester gets rights on these 25 databases (16 for E1, 9 for E3) and nothing else.
-- The c3_tester account already exists (created for the Clinic tests).

CREATE DATABASE IF NOT EXISTS c3_e1_simple_crud_legacy CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e1_simple_crud_migrated CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e1_mixed_crud_legacy CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e1_mixed_crud_migrated CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e1_hard_crud_legacy CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e1_hard_crud_migrated CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e1_helpdesk_legacy CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e1_helpdesk_migrated CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e1_hms_kishan0725_legacy CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e1_hms_kishan0725_migrated CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e1_news_buzz_legacy CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e1_news_buzz_migrated CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e1_fee_rainbow_legacy CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e1_fee_rainbow_migrated CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e1_dignity_dev_hms_legacy CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e1_dignity_dev_hms_migrated CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

GRANT ALL PRIVILEGES ON c3_e1_simple_crud_legacy.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e1_simple_crud_migrated.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e1_mixed_crud_legacy.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e1_mixed_crud_migrated.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e1_hard_crud_legacy.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e1_hard_crud_migrated.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e1_helpdesk_legacy.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e1_helpdesk_migrated.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e1_hms_kishan0725_legacy.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e1_hms_kishan0725_migrated.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e1_news_buzz_legacy.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e1_news_buzz_migrated.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e1_fee_rainbow_legacy.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e1_fee_rainbow_migrated.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e1_dignity_dev_hms_legacy.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e1_dignity_dev_hms_migrated.* TO 'c3_tester'@'localhost';

-- E3 (Alembic upgrade/downgrade round trip): one EMPTY database per schema.
CREATE DATABASE IF NOT EXISTS c3_e3_simple_crud CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e3_mixed_crud CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e3_hard_crud CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e3_clinic CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e3_helpdesk CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e3_hms_kishan0725 CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e3_news_buzz CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e3_fee_rainbow CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS c3_e3_dignity_dev_hms CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

GRANT ALL PRIVILEGES ON c3_e3_simple_crud.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e3_mixed_crud.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e3_hard_crud.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e3_clinic.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e3_helpdesk.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e3_hms_kishan0725.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e3_news_buzz.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e3_fee_rainbow.* TO 'c3_tester'@'localhost';
GRANT ALL PRIVILEGES ON c3_e3_dignity_dev_hms.* TO 'c3_tester'@'localhost';

-- E2 (schema vs live database reflection) reuses the c3_e1_*_legacy databases and c3_clinic_legacy_test.

-- Verify:
SHOW GRANTS FOR 'c3_tester'@'localhost';
