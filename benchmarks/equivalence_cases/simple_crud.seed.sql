-- E1 seed for simple_crud (hand-written test data, not derived from C3 output).
INSERT INTO users (id, name, email, status) VALUES
  (1, 'Alice Perera', 'alice@example.com', 'active'),
  (2, 'Bimal Silva', 'bimal@example.com', 'inactive'),
  (3, 'Chamari O''Neil', 'chamari@example.com', 'active');
INSERT INTO appointments (id, user_id, appointment_date, status) VALUES
  (1, 1, '2026-10-20', 'confirmed'),
  (2, 3, '2026-10-21', NULL);
