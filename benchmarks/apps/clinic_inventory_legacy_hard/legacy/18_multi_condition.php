<?php
require_once __DIR__ . '/db.php';
// S18 AND / comparison literals
$minimum = $_GET['minimum'] ?? '100';
$result = mysqli_query($conn, "SELECT id, full_name, hourly_rate FROM doctors WHERE active = 1 AND hourly_rate >= $minimum");
