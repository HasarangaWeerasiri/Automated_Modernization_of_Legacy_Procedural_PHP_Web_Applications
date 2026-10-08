<?php
require_once __DIR__ . '/db.php';
// S20 UPDATE with JOIN: expected unsupported by single-table converter
$id = $_POST['id'] ?? '1';
$sql = "UPDATE appointments a JOIN doctors d ON d.id = a.doctor_id SET a.fee = d.hourly_rate WHERE a.id = $id";
$result = mysqli_query($conn, $sql);
