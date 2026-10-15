-- E1 seed for hard_crud (hand-written test data, not derived from C3 output).
INSERT INTO users (id, name, email, status, role, created_at) VALUES
  (1, 'Alice Perera', 'alice@example.com', 'active', 'admin', '2026-01-05 09:00:00'),
  (2, 'Bimal Silva', 'bimal@example.com', 'inactive', 'customer', '2026-02-10 14:30:00'),
  (3, 'Chamari O''Neil', 'chamari@example.com', 'active', 'customer', '2026-03-15 08:15:00');
INSERT INTO categories (id, name) VALUES (1, 'Medicine'), (2, 'Equipment');
INSERT INTO products (id, category_id, title, description, price, stock, status) VALUES
  (1, 1, 'Paracetamol 500mg', 'Pain relief', 2.50, 100, 'active'),
  (2, 2, 'Thermometer', NULL, 15.00, 0, 'inactive'),
  (3, 1, 'Vitamin C 100% pure', 'Contains % and _ characters', 4.75, 20, 'active');
INSERT INTO orders (id, user_id, total, status, created_at) VALUES
  (1, 1, 7.50, 'paid', '2026-10-01 10:00:00'),
  (2, 3, 15.00, 'pending', '2026-10-02 11:00:00');
INSERT INTO order_items (id, order_id, product_id, quantity, unit_price) VALUES
  (1, 1, 1, 3, 2.50),
  (2, 2, 2, 1, 15.00);
