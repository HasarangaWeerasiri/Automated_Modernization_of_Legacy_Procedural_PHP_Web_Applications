<?php

$id = $_GET['id'];

$sql = "DELETE FROM products ";
$sql .= "WHERE id = " . $id;

mysqli_query($conn, $sql);