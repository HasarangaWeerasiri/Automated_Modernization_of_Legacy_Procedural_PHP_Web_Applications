<?php
function menu($active)
{
  ?><ul class="menu"><li class="<?php if ($active == "home") { echo 'on'; } ?>"><a href="index.php">Home</a></li></ul><?php
}

function top()
{
  ?><html><body><div class="wrap"><?php
}

function bottom()
{
  ?></div></body></html><?php
}
