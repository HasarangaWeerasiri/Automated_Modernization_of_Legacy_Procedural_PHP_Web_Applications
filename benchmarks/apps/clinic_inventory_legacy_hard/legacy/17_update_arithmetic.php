<?php
require_once __DIR__ . '/db.php';
// S17 arithmetic SET expression: preserve semantics or abstain
$id = $_POST['id'] ?? '1';
$delta = $_POST['delta'] ?? '5.00';
$result = mysqli_query($conn, "UPDATE appointments SET fee = fee + $delta WHERE id = $id");
