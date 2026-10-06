<?php

$id = $_GET['id'];

$sql = "SELECT id, title, price FROM products WHERE id = $id";

$result = mysqli_query($conn, $sql);