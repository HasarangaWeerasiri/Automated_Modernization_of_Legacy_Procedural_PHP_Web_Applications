<?php

$table = $_GET['table'];
$condition = $_GET['condition'];

$sql = buildQuery($table, $condition);

mysqli_query($conn, $sql);