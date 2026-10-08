<?php
require_once __DIR__ . '/db.php';
// S10 nested select
$sql = "SELECT id, full_name FROM patients WHERE id IN (SELECT patient_id FROM appointments WHERE status = 'confirmed')";
$result = mysqli_query($conn, $sql);
