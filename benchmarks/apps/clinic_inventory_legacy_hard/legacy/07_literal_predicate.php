<?php
require_once __DIR__ . '/db.php';
// S07 static SQL value must remain literal
$result = mysqli_query($conn, "SELECT id, booked_at FROM appointments WHERE status = 'confirmed'");
