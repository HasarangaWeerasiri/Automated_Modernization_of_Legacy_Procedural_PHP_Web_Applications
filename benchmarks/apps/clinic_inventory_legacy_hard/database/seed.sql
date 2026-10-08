-- Import into an isolated test database after schema.sql.
INSERT INTO departments (id,name,code) VALUES (1,'Cardiology','CARD'),(2,'General','GEN');
INSERT INTO patients (id,department_id,full_name,email,phone,created_at) VALUES
(1,1,'Alice Silva','alice@example.test','0711111111','2026-01-03 09:00:00'),
(2,2,'Bob Perera','bob@example.test',NULL,'2026-01-04 09:00:00'),
(3,1,'Charlie Fernando','charlie@example.test','0771111111','2026-01-05 09:00:00');
INSERT INTO doctors (id,department_id,full_name,hourly_rate,active) VALUES
(1,1,'Dr A',125.50,1),(2,2,'Dr B',99.00,1),(3,1,'Dr C',200.00,0);
INSERT INTO appointments (id,patient_id,doctor_id,booked_at,status,fee) VALUES
(1,1,1,'2026-02-01 10:30:00','confirmed',125.50),
(2,2,2,'2026-02-02 15:00:00','pending',99.00),
(3,3,1,'2026-02-03 08:30:00','cancelled',125.50);
INSERT INTO payments (id,appointment_id,amount,paid_at,method) VALUES
(1,1,125.50,'2026-02-01 10:45:00','cash'),
(2,2,50.00,NULL,'card');
