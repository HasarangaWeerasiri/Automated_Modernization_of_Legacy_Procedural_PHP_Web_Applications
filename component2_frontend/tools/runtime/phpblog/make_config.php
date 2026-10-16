<?php
// Phase 4e runtime check: the two files phpBlog's installer writes, made exactly as install/done.php makes them,
// from the installer's inputs (tools/runtime/phpblog/README.md). Not part of phpBlog; run by prepare.py with the
// PHP of the web container. Arguments: <phpBlog tree> <output folder> <inputs JSON>.
[$script, $tree, $out, $inputs] = $argv;
$in = json_decode(file_get_contents($inputs), true);

// install/done.php 3: include "../config_settings.php";  (the settings the repository ships)
include $tree . '/config_settings.php';

// install/done.php 14-22 (the request the installer page received, as recorded in the inputs)
$_SERVER['HTTP_HOST']   = $in['http_host'];
$_SERVER['REQUEST_URI'] = $in['request_uri'];
if (isset($_SERVER['HTTPS'])) {
    $htp = 'https';
} else {
    $htp = 'http';
}
$fullpath             = "$htp://$_SERVER[HTTP_HOST]$_SERVER[REQUEST_URI]";
$settings['site_url'] = substr($fullpath, 0, strpos($fullpath, '/install'));
$settings['email']    = $in['email'];
file_put_contents($out . '/config_settings.php', '<?php $settings = ' . var_export($settings, true) . '; ?>');

// install/done.php 49-53 (CONFIG_FILE_TEMPLATE = "config.tpl", install/core.php 18), then 59-60: written as is
$config_file = file_get_contents($tree . '/install/config.tpl');
$config_file = str_replace("<DB_HOST>", $in['database_host'], $config_file);
$config_file = str_replace("<DB_NAME>", $in['database_name'], $config_file);
$config_file = str_replace("<DB_USER>", $in['database_username'], $config_file);
$config_file = str_replace("<DB_PASSWORD>", $in['database_password'], $config_file);
file_put_contents($out . '/config.php', $config_file);
