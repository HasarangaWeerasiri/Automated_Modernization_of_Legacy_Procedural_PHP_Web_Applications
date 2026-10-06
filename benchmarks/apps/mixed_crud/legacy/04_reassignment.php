<?php

$sql = "SELECT id FROM old_products";

$sql = "SELECT id, title, price FROM products";

mysqli_query($conn, $sql);