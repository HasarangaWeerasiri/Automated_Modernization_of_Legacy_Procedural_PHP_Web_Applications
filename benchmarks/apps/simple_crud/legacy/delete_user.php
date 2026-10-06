<?php

$id = $_GET['id'];

$sql = "DELETE FROM users ";
$sql .= "WHERE id = " . $id;

mysqli_query($conn, $sql);