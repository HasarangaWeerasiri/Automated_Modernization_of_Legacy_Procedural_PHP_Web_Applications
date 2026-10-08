<?php
require_once __DIR__ . '/db.php';
// S04 assignment then .=
$id = $_POST['id'] ?? '1';
$phone = $_POST['phone'] ?? '0700000000';
$sql = "UPDATE patients SET phone = '" . $phone . "'";
$sql .= " WHERE id = " . $id;
$result = mysqli_query($conn, $sql);
