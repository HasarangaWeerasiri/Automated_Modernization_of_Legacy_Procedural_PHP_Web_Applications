CREATE TABLE teams (
  id INT UNSIGNED NOT NULL AUTO_INCREMENT,
  name VARCHAR(80) NOT NULL,
  parent_id INT UNSIGNED NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_team_name (name),
  CONSTRAINT fk_team_parent FOREIGN KEY (parent_id) REFERENCES teams(id) ON DELETE SET NULL
) ENGINE=InnoDB;
CREATE TABLE users (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  email VARCHAR(255) NOT NULL UNIQUE,
  status ENUM('active','blocked') NOT NULL DEFAULT 'active',
  flags SET('a','b') NULL,
  age TINYINT UNSIGNED NULL,
  balance DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  is_admin TINYINT(1) NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  bio TEXT,
  team_id INT UNSIGNED NULL,
  INDEX idx_users_name (name),
  CONSTRAINT chk_age CHECK (age >= 0),
  CONSTRAINT fk_users_team FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB;
CREATE TABLE memberships (
  user_id INT NOT NULL,
  team_id INT UNSIGNED NOT NULL,
  role VARCHAR(20) NOT NULL DEFAULT 'member',
  PRIMARY KEY (user_id, team_id),
  FOREIGN KEY (user_id) REFERENCES users(id),
  FOREIGN KEY (team_id) REFERENCES teams(id)
) ENGINE=InnoDB;
