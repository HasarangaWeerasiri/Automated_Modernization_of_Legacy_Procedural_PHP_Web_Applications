<?php

$id = $_POST['id'];
$name = $_POST['name'];

$sql = "UPDATE users SET name = '$name' WHERE id = $id";

mysqli_query($conn, $sql);