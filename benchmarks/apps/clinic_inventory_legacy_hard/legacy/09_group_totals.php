<?php
require_once __DIR__ . '/db.php';
// S09 aggregation + GROUP BY + HAVING
$sql = "SELECT doctor_id, COUNT(*) AS total, SUM(fee) AS fees FROM appointments GROUP BY doctor_id HAVING COUNT(*) >= 1";
$result = mysqli_query($conn, $sql);
