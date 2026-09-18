<?php
// Dump PHP lexer tokens for the source read from stdin as JSON.
// Each token: [name, text, line]. Text is emitted base64 so "\r\n" etc.
// survive the trip byte-exact.
$src = stream_get_contents(STDIN);
$out = [];
$line = 1;
foreach (token_get_all($src) as $t) {
    if (is_array($t)) {
        $out[] = [token_name($t[0]), base64_encode($t[1]), $t[2]];
        $line = $t[2] + substr_count($t[1], "\n");
    } else {
        $out[] = ["CHAR", base64_encode($t), $line];
    }
}
echo json_encode($out);
