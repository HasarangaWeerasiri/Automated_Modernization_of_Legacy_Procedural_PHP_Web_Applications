<?php

$status = $_GET['status'];

$sql = "SELECT id, name, email, status FROM users";

if ($status !== '') {
    $sql .= " WHERE status = '" . $status . "'";
}

$result = mysqli_query($conn, $sql);