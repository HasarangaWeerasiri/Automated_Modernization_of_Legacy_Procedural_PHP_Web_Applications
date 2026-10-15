CREATE DATABASE IF NOT EXISTS c3_helpdesk_holdout CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE c3_helpdesk_holdout;

CREATE TABLE teams (
  id INT AUTO_INCREMENT PRIMARY KEY,
  team_name VARCHAR(80) NOT NULL UNIQUE
) ENGINE=InnoDB;

CREATE TABLE agents (
  id INT AUTO_INCREMENT PRIMARY KEY,
  team_id INT NOT NULL,
  full_name VARCHAR(120) NOT NULL,
  CONSTRAINT fk_agents_team FOREIGN KEY (team_id) REFERENCES teams(id)
) ENGINE=InnoDB;

CREATE TABLE tickets (
  id INT AUTO_INCREMENT PRIMARY KEY,
  agent_id INT NOT NULL,
  subject VARCHAR(160) NOT NULL,
  description TEXT NOT NULL,
  priority VARCHAR(12) NOT NULL DEFAULT 'normal',
  status VARCHAR(16) NOT NULL DEFAULT 'open',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_tickets_agent FOREIGN KEY (agent_id) REFERENCES agents(id)
) ENGINE=InnoDB;

INSERT INTO teams (team_name) VALUES ('IT Support'), ('Facilities'), ('Customer Care');
INSERT INTO agents (team_id, full_name) VALUES
(1,'Amara Silva'),(1,'Nimal Perera'),(2,'Kavindi Fernando'),(3,'Ruwan Jayasekara');
INSERT INTO tickets (agent_id,subject,description,priority,status) VALUES
(1,'Laptop cannot connect to Wi-Fi','Connection drops in the library','high','open'),
(2,'Email password reset','Lost access to account','normal','closed'),
(3,'Air conditioner leaking','Room B12 requires inspection','high','open'),
(4,'Update customer profile','Correct contact information','low','in_progress'),
(1,'Install office software','New employee device setup','normal','open');
