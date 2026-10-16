<?php
include 'config.php';
include 'lib.php';
session_start();
$r = mysqli_query($c, 'select name, price from items');
$r2 = mysqli_query($c, 'select name from users');
$r3 = mysqli_query($c, 'select name from items');
top();
?>
<table class="items">
<?php while ($row = mysqli_fetch_assoc($r)) { ?>
<tr><td><?php echo $row['name']; ?></td>
<td><?php echo $row['price']; ?></td>
<td><?php echo $row['name']; ?></td></tr>
<?php } ?>
</table>
<select name="o"><?php foreach ($opts as $o) { ?><option><?php echo $o; ?></option><?php } ?></select>
<?php if ($r2) { ?><ul class="users"><?php while ($u = mysqli_fetch_assoc($r2)) { ?><li><b><?php echo $u['name']; ?></b></li><?php } ?></ul><?php } else { ?><p>None</p><?php } ?>
<div class="cards"><?php while ($d = mysqli_fetch_assoc($r3)) { ?><div class="card"><h3><?php echo $d['name']; ?></h3></div><?php } ?></div>
<?php try { foreach ($xs as $x2) { echo "<li>$x2</li>"; } } catch (Exception $e) { } ?>
<?php menu("home"); ?>
<?php if ($_SESSION['role'] == 'admin') { ?><p class="admin">Admin</p><?php } ?>
<?php if (isset($_POST['save'])) { echo "<p>Saved</p>"; } ?>
<input type="checkbox" name="c" <?php if ($flag == 1) { echo 'checked'; } ?>>
<?php if ($n > 1) { ?><b>many</b><?php } ?>
<hr>
<?php if ($m > 1) { ?><i>more</i><?php } ?>
<?php if ($big) { ?><div class="big"><h2>A</h2><p>B</p><p>C</p></div><?php } ?>
<?php if ($wide) { ?><div class="wide"><h2>W</h2><p>X</p><p>Y</p></div><?php } ?>
<?php if ($tall) { ?><div class="tall"><h2>T</h2><p>U</p><p>V</p></div><?php } ?>
<?php if ($k) { ?><em>k</em><?php } ?>
<p class="status"><?php if ($s == 1) { echo "One"; } if ($s == 2) { echo "Two"; } ?></p>
<p><?php echo $_SESSION['user']; ?> <?php echo $_GET['q']; ?> <?php echo $o; ?></p>
<?php for ($i = 0; $i < 3; $i++) { $t = $i; } ?>
<?php if ($x) { echo "<script>alert(1)</script>"; } ?>
<?php $obj->draw(); ?>
<?php if ($y) { echo "<script>go()</script>"; } ?>
<?php bottom(); ?>
