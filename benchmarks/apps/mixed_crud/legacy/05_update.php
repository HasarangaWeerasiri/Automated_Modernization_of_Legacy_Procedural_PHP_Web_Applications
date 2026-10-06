<?php

$id = $_POST['id'];
$title = $_POST['title'];
$price = $_POST['price'];

$sql = "UPDATE products
        SET title = '$title', price = $price
        WHERE id = $id";

mysqli_query($conn, $sql);