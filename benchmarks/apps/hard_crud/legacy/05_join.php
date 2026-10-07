<?php

$userId = $_GET['user_id'];

$sql = "SELECT orders.id,
               orders.total,
               orders.status,
               users.name
        FROM orders
        JOIN users ON orders.user_id = users.id
        WHERE users.id = " . $userId;

$result = mysqli_query($conn, $sql);