<?php
session_start();
$conn = mysqli_connect("localhost", "root", "", "demo");
$result = mysqli_query($conn, "SELECT * FROM patients WHERE id = " . intval($_GET['id']));
?>
<html>
<body>
<table>
<?php foreach (mysqli_fetch_all($result, MYSQLI_BOTH) as $row) { ?>
  <tr><td><?php echo $row['name']; ?></td></tr>
<?php } ?>
</table>
</body>
</html>
