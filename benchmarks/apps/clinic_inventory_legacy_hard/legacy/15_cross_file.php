<?php
require_once __DIR__ . '/db.php';
require_once __DIR__ . '/query_factory.php';
// S15 constructed in another file through a function
$sql = makeDoctorQuery($_GET['department_id'] ?? '1');
$result = mysqli_query($conn, $sql);
