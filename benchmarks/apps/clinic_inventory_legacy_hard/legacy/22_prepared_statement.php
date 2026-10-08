<?php
require_once __DIR__ . '/db.php';
// S22 mysqli prepare + bind_param; analyzer should detect execution semantics
$id = $_GET['id'] ?? 1;
$stmt = mysqli_prepare($conn, "SELECT id, full_name FROM patients WHERE id = ?");
mysqli_stmt_bind_param($stmt, 'i', $id);
mysqli_stmt_execute($stmt);
