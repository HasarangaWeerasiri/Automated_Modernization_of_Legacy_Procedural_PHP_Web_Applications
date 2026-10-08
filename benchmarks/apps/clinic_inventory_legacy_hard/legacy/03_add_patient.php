<?php
require_once __DIR__ . '/db.php';
// S03 concatenation of values, deterministic query structure
$name = $_POST['name'] ?? 'Dina';
$email = $_POST['email'] ?? 'dina@example.test';
$department = $_POST['department_id'] ?? '1';
$sql = "INSERT INTO patients (department_id, full_name, email, created_at) VALUES (" . $department . ", '" . $name . "', '" . $email . "', '2026-02-10 09:00:00')";
$result = mysqli_query($conn, $sql);
