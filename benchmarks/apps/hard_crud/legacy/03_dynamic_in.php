<?php

$ids = $_GET['ids'];

$sql = "SELECT id, title, price
        FROM products
        WHERE id IN (" . $ids . ")";

$result = mysqli_query($conn, $sql);