<?php
if ($bad) { ?>
<form method="post"><input name="u"><input type="submit" value="Go"></form>
<?php } else { header('Location: index.php'); }
