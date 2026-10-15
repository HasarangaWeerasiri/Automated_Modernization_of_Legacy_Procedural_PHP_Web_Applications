<?php

declare(strict_types=1);

/*
 * E1 legacy side: build the SQL string with PHP itself, from the verbatim
 * application source, and (optionally) execute it with mysqli.
 *
 * This file does not use C3's recovery. It parses the PHP file with
 * nikic/php-parser, finds the execution call at FILE:LINE and takes a
 * backward slice of the straight-line statements in the same scope that
 * build the SQL argument:
 *
 *   - `$v = expr;` / `$v .= expr;` (and other compound assignments) whose
 *     right-hand side uses only string-building constructs are kept verbatim;
 *   - a needed variable assigned from anything else (an app function, a
 *     query result, a constant, an object property ...) or assigned inside a
 *     preceding control-flow statement becomes a CASE INPUT: its value is
 *     supplied by the case file, never guessed;
 *   - the slice stops at the enclosing function boundary; variables still
 *     needed there (parameters, globals, includes) are case inputs too.
 *
 * Usage:
 *   php legacy_runner.php slice <php_file> <line> <call>
 *   php legacy_runner.php run <spec.json>
 *
 * run spec: {"file", "line", "call", "mode": "legacy"|"raw",
 *            "inputs": {"$_GET['id']": "3", "$name": "x"},
 *            "execute": bool, "database": "...", "host": "127.0.0.1",
 *            "user": "c3_tester"}
 * mode "raw" makes the SQL escaping functions the identity, so the variable
 * values are what a migrated caller passes to bind parameters. The password
 * is read from the environment variable C3_DB_PASSWORD only.
 */

require __DIR__ . '/../../components/c3_data_layer/vendor/autoload.php';

use PhpParser\Node;
use PhpParser\NodeFinder;
use PhpParser\NodeTraverser;
use PhpParser\NodeVisitor\ParentConnectingVisitor;
use PhpParser\ParserFactory;

const EXECUTOR_FUNCTIONS = [
    // name => position of the SQL argument
    'mysqli_query' => 1,
    'mysqli_real_query' => 1,
    'mysqli_multi_query' => 1,
    'mysqli_prepare' => 1,
    'mysqli_stmt_prepare' => 1,
    'mysql_query' => 0,
    'mysql_unbuffered_query' => 0,
    'mysql_db_query' => 1,
];
const EXECUTOR_METHODS = ['query', 'real_query', 'multi_query', 'prepare', 'exec'];

// Pure string-building functions allowed in the slice.
const PURE_FUNCTIONS = [
    'intval', 'floatval', 'strval', 'boolval', 'trim', 'ltrim', 'rtrim',
    'htmlspecialchars', 'htmlentities', 'strip_tags', 'stripslashes',
    'strtolower', 'strtoupper', 'ucfirst', 'ucwords', 'md5', 'sha1',
    'sprintf', 'str_replace', 'substr', 'number_format', 'abs', 'round',
];
// SQL escaping functions: real in "legacy" mode, identity in "raw" mode.
const ESCAPE_FUNCTIONS = ['mysqli_real_escape_string' => 1, 'mysql_real_escape_string' => 0, 'addslashes' => 0];
const ESCAPE_METHODS = ['real_escape_string', 'escape_string'];
const SUPERGLOBALS = ['_GET', '_POST', '_REQUEST', '_COOKIE', '_SESSION', '_SERVER'];

function fail(string $message): void
{
    fwrite(STDERR, $message . PHP_EOL);
    exit(2);
}

function call_name(Node $node): ?string
{
    if ($node instanceof Node\Expr\FuncCall && $node->name instanceof Node\Name) {
        return strtolower($node->name->toString());
    }
    if (($node instanceof Node\Expr\MethodCall || $node instanceof Node\Expr\NullsafeMethodCall)
        && $node->name instanceof Node\Identifier) {
        return '->' . strtolower($node->name->toString());
    }
    return null;
}

function call_line(Node $node): int
{
    // The line of the function/method name token (what a grep reports).
    return $node->name->getStartLine();
}

function find_call(array $ast, int $line, string $call): array
{
    $finder = new NodeFinder();
    $wanted = strtolower($call);
    $candidates = $finder->find($ast, function (Node $node) use ($line, $wanted) {
        $name = call_name($node);
        if ($name === null) {
            return false;
        }
        $isExecutor = isset(EXECUTOR_FUNCTIONS[$name]) || in_array(ltrim($name, '->'), EXECUTOR_METHODS, true) && str_starts_with($name, '->');
        return $isExecutor && call_line($node) === $line && $name === $wanted;
    });
    return $candidates;
}

function sql_argument(Node $call): ?Node\Expr
{
    $name = call_name($call);
    $position = str_starts_with($name, '->') ? 0 : EXECUTOR_FUNCTIONS[$name];
    foreach ($call->args as $arg) {
        if ($arg instanceof Node\Arg && $arg->name !== null && strtolower($arg->name->toString()) === 'query') {
            return $arg->value;
        }
    }
    $arg = $call->args[$position] ?? null;
    return $arg instanceof Node\Arg ? $arg->value : null;
}

function connection_variables(Node $call): array
{
    $names = [];
    $name = call_name($call);
    if (str_starts_with($name, '->') && $call->var instanceof Node\Expr\Variable && is_string($call->var->name)) {
        $names[] = $call->var->name;
    } elseif (!str_starts_with($name, '->') && EXECUTOR_FUNCTIONS[$name] === 1) {
        $link = $call->args[0] ?? null;
        if ($link instanceof Node\Arg && $link->value instanceof Node\Expr\Variable && is_string($link->value->name)) {
            $names[] = $link->value->name;
        }
    }
    return $names;
}

/** Why an expression is not pure string building, or null when it is. */
function disallowed(Node $expr, array &$connections): ?string
{
    $finder = new NodeFinder();
    foreach ($finder->find($expr, fn (Node $n) => true) as $node) {
        if ($node instanceof Node\Expr\FuncCall) {
            $name = call_name($node);
            if ($name === null) {
                return 'dynamic function call';
            }
            if (isset(ESCAPE_FUNCTIONS[$name])) {
                if (ESCAPE_FUNCTIONS[$name] === 1) {
                    $link = $node->args[0] ?? null;
                    if ($link instanceof Node\Arg && $link->value instanceof Node\Expr\Variable && is_string($link->value->name)) {
                        $connections[] = $link->value->name;
                    }
                }
                continue;
            }
            if (!in_array($name, PURE_FUNCTIONS, true)) {
                return "call to $name()";
            }
        } elseif ($node instanceof Node\Expr\MethodCall) {
            $name = call_name($node);
            if ($name !== null && in_array(ltrim($name, '->'), ESCAPE_METHODS, true)
                && $node->var instanceof Node\Expr\Variable && is_string($node->var->name)) {
                $connections[] = $node->var->name;
                continue;
            }
            return 'method call ' . ($name ?? 'dynamic');
        } elseif ($node instanceof Node\Expr\ConstFetch) {
            if (!in_array(strtolower($node->name->toString()), ['true', 'false', 'null'], true)) {
                return 'constant ' . $node->name->toString();
            }
        } elseif ($node instanceof Node\Expr\Variable && !is_string($node->name)) {
            return 'variable variable';
        } elseif ($node instanceof Node\Expr\CallLike
            || $node instanceof Node\Expr\Include_
            || $node instanceof Node\Expr\Eval_
            || $node instanceof Node\Expr\ShellExec
            || $node instanceof Node\Expr\Exit_
            || $node instanceof Node\Expr\Print_
            || $node instanceof Node\Expr\Closure
            || $node instanceof Node\Expr\ArrowFunction
            || $node instanceof Node\Expr\Assign
            || $node instanceof Node\Expr\AssignOp
            || $node instanceof Node\Expr\AssignRef
            || $node instanceof Node\Expr\PropertyFetch
            || $node instanceof Node\Expr\NullsafePropertyFetch
            || $node instanceof Node\Expr\StaticPropertyFetch
            || $node instanceof Node\Expr\ClassConstFetch
            || $node instanceof Node\Expr\PreInc || $node instanceof Node\Expr\PostInc
            || $node instanceof Node\Expr\PreDec || $node instanceof Node\Expr\PostDec) {
            return 'construct ' . $node->getType();
        }
    }
    return null;
}

function used_variables(Node $expr): array
{
    $names = [];
    foreach ((new NodeFinder())->findInstanceOf($expr, Node\Expr\Variable::class) as $var) {
        if (is_string($var->name) && !in_array($var->name, SUPERGLOBALS, true) && $var->name !== 'this') {
            $names[$var->name] = true;
        }
    }
    return array_keys($names);
}

function superglobal_inputs(Node $expr): array
{
    $inputs = [];
    foreach ((new NodeFinder())->findInstanceOf($expr, Node\Expr\ArrayDimFetch::class) as $fetch) {
        if ($fetch->var instanceof Node\Expr\Variable && is_string($fetch->var->name)
            && in_array($fetch->var->name, SUPERGLOBALS, true)) {
            if ($fetch->dim instanceof Node\Scalar\String_ || $fetch->dim instanceof Node\Scalar\Int_) {
                $inputs[] = '$' . $fetch->var->name . "['" . $fetch->dim->value . "']";
            } else {
                $inputs[] = '$' . $fetch->var->name . '[<dynamic key>]';
            }
        }
    }
    return $inputs;
}

/** Variables assigned anywhere inside a statement (control flow). */
function assigned_inside(Node $stmt): array
{
    $names = [];
    $finder = new NodeFinder();
    $targets = [];
    foreach ($finder->find($stmt, fn (Node $n) => $n instanceof Node\Expr\Assign
        || $n instanceof Node\Expr\AssignOp || $n instanceof Node\Expr\AssignRef) as $assign) {
        $targets[] = $assign->var;
    }
    foreach ($finder->findInstanceOf($stmt, Node\Stmt\Foreach_::class) as $loop) {
        $targets[] = $loop->valueVar;
        if ($loop->keyVar !== null) {
            $targets[] = $loop->keyVar;
        }
    }
    foreach ($finder->find($stmt, fn (Node $n) => $n instanceof Node\Stmt\Global_ || $n instanceof Node\Stmt\Static_) as $decl) {
        foreach ($decl->vars as $var) {
            $targets[] = $var instanceof Node\Stmt\StaticVar ? $var->var : $var;
        }
    }
    foreach ($targets as $target) {
        foreach ((new NodeFinder())->findInstanceOf($target, Node\Expr\Variable::class) as $var) {
            if (is_string($var->name)) {
                $names[$var->name] = true;
            }
        }
        if ($target instanceof Node\Expr\Variable && is_string($target->name)) {
            $names[$target->name] = true;
        }
    }
    return array_keys($names);
}

function source_text(string $code, Node $node): string
{
    return substr($code, $node->getStartFilePos(), $node->getEndFilePos() - $node->getStartFilePos() + 1);
}

/** The statement list that holds $stmt, and its index there. */
function containing_list(Node $stmt, array $ast): ?array
{
    $parent = $stmt->getAttribute('parent');
    $lists = [];
    if ($parent === null) {
        $lists[] = $ast;
    } else {
        foreach ($parent->getSubNodeNames() as $sub) {
            if (is_array($parent->$sub)) {
                $lists[] = $parent->$sub;
            }
        }
    }
    foreach ($lists as $list) {
        foreach ($list as $index => $item) {
            if ($item === $stmt) {
                return [$list, $index];
            }
        }
    }
    return null;
}

function slice(string $file, int $line, string $call): array
{
    $code = file_get_contents($file);
    $parser = (new ParserFactory())->createForNewestSupportedVersion();
    $ast = $parser->parse($code);
    $traverser = new NodeTraverser(new ParentConnectingVisitor());
    $ast = $traverser->traverse($ast);

    $result = ['file' => $file, 'line' => $line, 'call' => $call];
    $calls = find_call($ast, $line, $call);
    if (count($calls) === 0) {
        return $result + ['status' => 'call_not_found'];
    }
    if (count($calls) > 1) {
        return $result + ['status' => 'ambiguous_call_on_line'];
    }
    $callNode = $calls[0];
    $argument = sql_argument($callNode);
    if ($argument === null) {
        return $result + ['status' => 'missing_sql_argument'];
    }

    $connections = connection_variables($callNode);
    $reason = disallowed($argument, $connections);
    if ($reason !== null) {
        return $result + ['status' => 'sql_expression_not_pure', 'reason' => $reason];
    }

    $needed = array_fill_keys(used_variables($argument), true);
    $inputs = [];           // name => note
    $statements = [];       // verbatim, in execution order
    $sliceNodes = [$argument];
    $superglobals = superglobal_inputs($argument);
    $scope = 'file';

    // The statement that contains the call.
    $node = $callNode;
    while ($node !== null && !($node instanceof Node\Stmt && containing_list($node, $ast) !== null)) {
        $node = $node->getAttribute('parent');
    }

    while ($node !== null) {
        [$list, $index] = containing_list($node, $ast);
        for ($i = $index - 1; $i >= 0 && $needed; $i--) {
            $stmt = $list[$i];
            $expr = $stmt instanceof Node\Stmt\Expression ? $stmt->expr : null;
            $isAssign = $expr instanceof Node\Expr\Assign || $expr instanceof Node\Expr\AssignOp;
            if ($isAssign && $expr->var instanceof Node\Expr\Variable && is_string($expr->var->name)
                && isset($needed[$expr->var->name])) {
                $name = $expr->var->name;
                $why = disallowed($expr->expr, $connections);
                if ($why !== null) {
                    $inputs[$name] = "assigned from $why at line {$stmt->getStartLine()}; value supplied by the case";
                    unset($needed[$name]);
                    continue;
                }
                array_unshift($statements, ['line' => $stmt->getStartLine(), 'code' => source_text($code, $stmt)]);
                $sliceNodes[] = $stmt;
                $superglobals = array_merge($superglobals, superglobal_inputs($expr->expr));
                if ($expr instanceof Node\Expr\Assign) {
                    unset($needed[$name]);
                }
                foreach (used_variables($expr->expr) as $used) {
                    if (!isset($inputs[$used])) {
                        $needed[$used] = true;
                    }
                }
                continue;
            }
            if ($stmt instanceof Node\Stmt\Function_ || $stmt instanceof Node\Stmt\Class_) {
                continue;
            }
            foreach (assigned_inside($stmt) as $name) {
                if (isset($needed[$name])) {
                    $inputs[$name] = "assigned inside {$stmt->getType()} at line {$stmt->getStartLine()} (control flow); value supplied by the case";
                    unset($needed[$name]);
                }
            }
        }
        if (!$needed) {
            break;
        }
        // Move to the enclosing statement, stopping at a function boundary.
        $parent = $node->getAttribute('parent');
        while ($parent !== null && !($parent instanceof Node\Stmt && containing_list($parent, $ast) !== null)) {
            if ($parent instanceof Node\FunctionLike) {
                break;
            }
            $parent = $parent->getAttribute('parent');
        }
        if ($parent instanceof Node\FunctionLike) {
            $scope = $parent instanceof Node\Stmt\Function_ || $parent instanceof Node\Stmt\ClassMethod
                ? 'function ' . $parent->name->toString() : 'closure';
            break;
        }
        if ($parent instanceof Node\Stmt\Function_ || $parent instanceof Node\Stmt\ClassMethod) {
            $scope = 'function ' . $parent->name->toString();
            break;
        }
        $node = $parent;
    }

    foreach (array_keys($needed) as $name) {
        $inputs[$name] = $scope === 'file'
            ? 'not assigned before the call in this file (include, global or earlier request); value supplied by the case'
            : "not assigned in $scope before the call (parameter or global); value supplied by the case";
    }
    $connections = array_values(array_unique($connections));
    $caseInputs = [];
    foreach (array_values(array_unique($superglobals)) as $name) {
        $caseInputs[] = ['name' => $name, 'kind' => 'superglobal', 'note' => ''];
    }
    foreach ($inputs as $name => $note) {
        if (!in_array($name, $connections, true)) {
            // Keys read from the variable ($row['id']): the case value must be an array.
            $keys = [];
            foreach ((new NodeFinder())->find($sliceNodes, fn (Node $n) => $n instanceof Node\Expr\ArrayDimFetch
                && $n->var instanceof Node\Expr\Variable && $n->var->name === $name) as $fetch) {
                if ($fetch->dim instanceof Node\Scalar\String_ || $fetch->dim instanceof Node\Scalar\Int_) {
                    $keys[(string) $fetch->dim->value] = true;
                }
            }
            $caseInputs[] = ['name' => '$' . $name, 'kind' => $keys ? 'array' : 'variable', 'keys' => array_keys($keys), 'note' => $note];
        }
    }

    $status = 'ok';
    foreach ($caseInputs as $input) {
        if (str_contains($input['name'], '<dynamic key>')) {
            $status = 'dynamic_superglobal_key';
        }
    }

    return $result + [
        'status' => $status,
        'scope' => $scope,
        'statements' => $statements,
        'sql_expression' => source_text($code, $argument),
        'inputs' => $caseInputs,
        'connection_variables' => $connections,
    ];
}

/** Assign one case input: "$_GET['id']" or "$name". */
function input_assignment(string $name, $value): string
{
    if (preg_match('/^\$(_[A-Z]+)\[\'(.*)\'\]$/s', $name, $m)) {
        return '$' . $m[1] . '[' . var_export($m[2], true) . '] = ' . var_export($value, true) . ';';
    }
    if (preg_match('/^\$([A-Za-z_][A-Za-z0-9_]*)$/', $name, $m)) {
        return '$' . $m[1] . ' = ' . var_export($value, true) . ';';
    }
    fail("bad input name: $name");
}

function connect(array $spec): mysqli
{
    mysqli_report(MYSQLI_REPORT_OFF);
    $password = getenv('C3_DB_PASSWORD');
    if ($password === false) {
        fail('C3_DB_PASSWORD is not set');
    }
    $conn = mysqli_init();
    // FOUND_ROWS: affected rows = matched rows, the convention of SQLAlchemy's MySQL dialect.
    if (!mysqli_real_connect($conn, $spec['host'] ?? '127.0.0.1', $spec['user'] ?? 'c3_tester', $password,
        $spec['database'], 3306, null, MYSQLI_CLIENT_FOUND_ROWS)) {
        fail('connect failed: ' . mysqli_connect_error());
    }
    mysqli_set_charset($conn, 'utf8mb4');
    return $conn;
}

function run(array $spec): array
{
    $sliced = slice($spec['file'], (int) $spec['line'], $spec['call']);
    if ($sliced['status'] !== 'ok') {
        return ['slice' => $sliced, 'status' => 'slice_' . $sliced['status']];
    }
    foreach ($sliced['inputs'] as $input) {
        if (!array_key_exists($input['name'], $spec['inputs'] ?? [])) {
            return ['slice' => $sliced, 'status' => 'missing_input', 'missing' => $input['name']];
        }
    }

    $raw = ($spec['mode'] ?? 'legacy') === 'raw';
    $conn = !$raw && !empty($spec['execute']) ? connect($spec) : null;

    $namespace = $raw ? 'C3E1Raw' : 'C3E1Legacy';
    $prelude = $raw
        ? 'function mysqli_real_escape_string($c, $v) { return $v; }
           function mysql_real_escape_string($v, $c = null) { return $v; }
           function addslashes($v) { return $v; }
           final class Connection { public function real_escape_string($v) { return $v; } public function escape_string($v) { return $v; } }'
        : 'function mysql_real_escape_string($v, $c = null) { return \mysqli_real_escape_string($GLOBALS["__c3e1_conn"], (string) $v); }
           final class Connection { public function real_escape_string($v) { return \mysqli_real_escape_string($GLOBALS["__c3e1_conn"], (string) $v); } public function escape_string($v) { return $this->real_escape_string($v); } }';
    if (!$raw && $conn === null) {
        // Building the legacy SQL without a database: escape like MySQL with utf8mb4.
        $prelude = 'function mysqli_real_escape_string($c, $v) { return \c3e1_escape((string) $v); }
           function mysql_real_escape_string($v, $c = null) { return \c3e1_escape((string) $v); }
           final class Connection { public function real_escape_string($v) { return \c3e1_escape((string) $v); } public function escape_string($v) { return \c3e1_escape((string) $v); } }';
    }

    $GLOBALS['__c3e1_conn'] = $conn;
    $body = '';
    foreach ($spec['inputs'] as $name => $value) {
        $body .= input_assignment($name, $value) . "\n";
    }
    foreach ($sliced['connection_variables'] as $name) {
        $body .= '$' . $name . " = \$GLOBALS['__c3e1_conn'] instanceof \\mysqli ? \$GLOBALS['__c3e1_conn'] : new Connection();\n";
    }
    foreach ($sliced['statements'] as $statement) {
        $body .= $statement['code'] . "\n";
    }
    $body .= '$__c3e1_sql = ' . $sliced['sql_expression'] . ";\n";
    $body .= 'return get_defined_vars();';

    $script = "namespace $namespace;\n$prelude\nreturn function () { $body };";
    try {
        $closure = eval($script);
        $variables = $closure();
    } catch (\Throwable $error) {
        return ['slice' => $sliced, 'status' => 'php_error', 'error' => get_class($error) . ': ' . $error->getMessage()];
    }

    $sql = $variables['__c3e1_sql'];
    unset($variables['__c3e1_sql']);
    $plain = [];
    foreach ($variables as $name => $value) {
        if (is_scalar($value) || $value === null) {
            $plain[$name] = $value;
        }
    }
    $output = ['slice' => $sliced, 'status' => 'built', 'sql' => $sql, 'variables' => $plain];

    if ($conn !== null) {
        $result = mysqli_query($conn, (string) $sql);
        if ($result === false) {
            $output['status'] = 'sql_error';
            $output['error'] = mysqli_errno($conn) . ': ' . mysqli_error($conn);
        } elseif ($result instanceof mysqli_result) {
            $columns = array_map(fn ($field) => $field->name, mysqli_fetch_fields($result));
            $output['status'] = 'executed';
            $output['columns'] = $columns;
            $output['rows'] = mysqli_fetch_all($result, MYSQLI_NUM);
        } else {
            $output['status'] = 'executed';
            $output['affected_rows'] = mysqli_affected_rows($conn);
            $output['insert_id'] = mysqli_insert_id($conn);
        }
        mysqli_close($conn);
    }
    return $output;
}

/** mysqli_real_escape_string for utf8mb4 without a connection (dry runs only). */
function c3e1_escape(string $value): string
{
    return strtr($value, ["\\" => "\\\\", "\0" => "\\0", "\n" => "\\n", "\r" => "\\r", "'" => "\\'", '"' => '\\"', "\x1a" => "\\Z"]);
}

$mode = $argv[1] ?? '';
if ($mode === 'slice' && $argc === 5) {
    $output = slice($argv[2], (int) $argv[3], $argv[4]);
} elseif ($mode === 'run' && $argc === 3) {
    $output = run(json_decode(file_get_contents($argv[2]), true, 512, JSON_THROW_ON_ERROR));
} else {
    fail('usage: legacy_runner.php slice <file> <line> <call> | run <spec.json>');
}
echo json_encode($output, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE | JSON_PARTIAL_OUTPUT_ON_ERROR), PHP_EOL;
