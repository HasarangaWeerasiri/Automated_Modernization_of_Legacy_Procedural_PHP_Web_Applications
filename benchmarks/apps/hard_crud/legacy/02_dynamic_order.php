<?php

$column = $_GET['sort'];

$sql = "SELECT id, name, email
        FROM users
        ORDER BY " . $column;

$result = mysqli_query($conn, $sql);