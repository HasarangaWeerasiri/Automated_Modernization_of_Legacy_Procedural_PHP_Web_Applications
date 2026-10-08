-- C3 hard benchmark: MySQL 8.x schema (schema only; no database create/use statement)
CREATE TABLE departments (
 id INT NOT NULL AUTO_INCREMENT,
 name VARCHAR(100) NOT NULL,
 code VARCHAR(20) NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_department_code (code)
) ENGINE=InnoDB;

CREATE TABLE patients (
 id INT NOT NULL AUTO_INCREMENT,
 department_id INT NOT NULL,
 full_name VARCHAR(120) NOT NULL,
 email VARCHAR(160) NULL,
 phone VARCHAR(25) NULL,
 created_at DATETIME NOT NULL,
 PRIMARY KEY (id),
 KEY idx_patients_department (department_id),
 CONSTRAINT fk_patients_department FOREIGN KEY (department_id) REFERENCES departments(id)
) ENGINE=InnoDB;

CREATE TABLE doctors (
 id INT NOT NULL AUTO_INCREMENT,
 department_id INT NOT NULL,
 full_name VARCHAR(120) NOT NULL,
 hourly_rate DECIMAL(10,2) NOT NULL,
 active TINYINT(1) NOT NULL DEFAULT 1,
 PRIMARY KEY (id),
 CONSTRAINT fk_doctors_department FOREIGN KEY (department_id) REFERENCES departments(id)
) ENGINE=InnoDB;

CREATE TABLE appointments (
 id INT NOT NULL AUTO_INCREMENT,
 patient_id INT NOT NULL,
 doctor_id INT NOT NULL,
 booked_at DATETIME NOT NULL,
 status VARCHAR(30) NOT NULL DEFAULT 'pending',
 fee DECIMAL(10,2) NOT NULL,
 PRIMARY KEY (id),
 KEY idx_appointments_patient (patient_id),
 CONSTRAINT fk_appointments_patient FOREIGN KEY (patient_id) REFERENCES patients(id),
 CONSTRAINT fk_appointments_doctor FOREIGN KEY (doctor_id) REFERENCES doctors(id)
) ENGINE=InnoDB;

CREATE TABLE payments (
 id INT NOT NULL AUTO_INCREMENT,
 appointment_id INT NOT NULL,
 amount DECIMAL(10,2) NOT NULL,
 paid_at DATETIME NULL,
 method VARCHAR(30) NOT NULL,
 PRIMARY KEY (id),
 CONSTRAINT fk_payments_appointment FOREIGN KEY (appointment_id) REFERENCES appointments(id)
) ENGINE=InnoDB;
