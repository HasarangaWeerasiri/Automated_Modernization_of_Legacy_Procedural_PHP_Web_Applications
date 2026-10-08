<?php
require_once __DIR__ . '/db.php';
// S08 multitable JOIN: useful abstention test
$patient = $_GET['patient_id'] ?? '1';
$sql = "SELECT a.id, p.full_name, d.full_name AS doctor_name FROM appointments a JOIN patients p ON p.id = a.patient_id JOIN doctors d ON d.id = a.doctor_id WHERE p.id = $patient";
$result = mysqli_query($conn, $sql);
