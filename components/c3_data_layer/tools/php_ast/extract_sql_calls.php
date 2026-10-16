<?php

declare(strict_types=1);

/*
 * Component 3 - Data Layer Migration
 *
 * AST-based SQL execution point extractor.
 *
 * Detects:
 *   mysqli_query(...)
 *   mysql_query(...)
 *   $object->query(...)
 *   $object->prepare(...)
 *   $object->exec(...)
 *
 * Output:
 *   JSON array containing source file, source line,
 *   executor type and SQL argument expression.
 */

require __DIR__ . '/../../vendor/autoload.php';

use PhpParser\Error;
use PhpParser\Node;
use PhpParser\NodeFinder;
use PhpParser\ParserFactory;
use PhpParser\PrettyPrinter\Standard;


if ($argc < 2) {
    fwrite(
        STDERR,
        "Usage: php extract_sql_calls.php <php-file-or-directory>\n"
    );

    exit(1);
}


$inputPath = $argv[1];

if (!file_exists($inputPath)) {
    fwrite(
        STDERR,
        "Input path does not exist: {$inputPath}\n"
    );

    exit(1);
}


$parser = (new ParserFactory())
    ->createForNewestSupportedVersion();

$nodeFinder = new NodeFinder();

$prettyPrinter = new Standard();


/**
 * Return all PHP files from a file or directory.
 */
function collectPhpFiles(string $path): array
{
    if (is_file($path)) {
        return [
            realpath($path) ?: $path
        ];
    }

    $files = [];

    $iterator = new RecursiveIteratorIterator(
        new RecursiveDirectoryIterator(
            $path,
            FilesystemIterator::SKIP_DOTS
        )
    );

    foreach ($iterator as $file) {
        if (!$file->isFile()) {
            continue;
        }

        if (strtolower($file->getExtension()) !== 'php') {
            continue;
        }

        $files[] = $file->getPathname();
    }

    sort($files);

    return $files;
}


/**
 * Convert an AST expression back to readable PHP.
 */
function expressionToString(
    Node $node,
    Standard $prettyPrinter
): string {
    return $prettyPrinter->prettyPrintExpr($node);
}


/**
 * Procedural functions that execute or prepare SQL, mapped to the
 * zero-based position of their SQL argument.
 */
const SQL_FUNCTION_ARGUMENT_POSITIONS = [
    'mysqli_query' => 1,
    'mysqli_real_query' => 1,
    'mysqli_multi_query' => 1,
    'mysqli_prepare' => 1,
    'mysqli_execute_query' => 1,
    'mysqli_stmt_prepare' => 1,
    'mysql_query' => 0,
    'mysql_unbuffered_query' => 0,
    'mysql_db_query' => 1,
];

/**
 * Object methods that execute or prepare SQL (mysqli and PDO).
 * The SQL is always the first argument.
 */
const SQL_METHOD_NAMES = [
    'query',
    'prepare',
    'exec',
    'real_query',
    'multi_query',
    'execute_query',
];

/**
 * Database APIs of other DBMSs. C3 targets MySQL only, so these are
 * reported as UNSUPPORTED_EXECUTOR rows instead of being ignored.
 */
const UNSUPPORTED_DB_FUNCTIONS = [
    'pg_query', 'pg_query_params', 'pg_prepare', 'pg_send_query',
    'pg_send_query_params', 'pg_send_prepare',
    'sqlite_query', 'sqlite_exec', 'sqlite_unbuffered_query',
    'sqlsrv_query', 'sqlsrv_prepare', 'mssql_query',
    'odbc_exec', 'odbc_prepare', 'odbc_do',
    'oci_parse', 'db2_exec', 'db2_prepare',
    'ibase_query', 'ibase_prepare', 'sybase_query',
];

/**
 * Return the SQL argument expression of a call, or null.
 *
 * A PHP 8 named argument "query:" wins; otherwise the positional
 * argument at $position is used, provided no named arguments precede
 * it (named arguments make positions unreliable).
 */
function findSqlArgument(Node $call, int $position): ?Node
{
    foreach ($call->args as $argument) {
        if (
            $argument instanceof Node\Arg
            && $argument->name instanceof Node\Identifier
            && strtolower($argument->name->toString()) === 'query'
        ) {
            return $argument->value;
        }
    }

    for ($index = 0; $index <= $position; $index++) {
        $argument = $call->args[$index] ?? null;

        if (!$argument instanceof Node\Arg || $argument->unpack) {
            return null;
        }

        if ($argument->name !== null && $index < $position) {
            return null;
        }
    }

    return $call->args[$position]->value;
}

/**
 * Extract a readable method name.
 */
function getMethodName(Node $name): ?string
{
    if ($name instanceof Node\Identifier) {
        return $name->toString();
    }

    return null;
}

/**
 * Attach parent references to AST nodes.
 *
 * PhpParser nodes do not contain parent references by default.
 * We add them so assignments can be checked for surrounding
 * control-flow structures such as if/elseif/else statements.
 */
function connectParentNodes(
    Node $node,
    ?Node $parent = null
): void {
    if ($parent !== null) {
        $node->setAttribute('parent', $parent);
    }

    foreach ($node->getSubNodeNames() as $subNodeName) {
        $child = $node->$subNodeName;

        if ($child instanceof Node) {
            connectParentNodes(
                $child,
                $node
            );
        } elseif (is_array($child)) {
            foreach ($child as $item) {
                if ($item instanceof Node) {
                    connectParentNodes(
                        $item,
                        $node
                    );
                }
            }
        }
    }
}


/**
 * Return the control-flow contexts surrounding a node.
 */
function getControlFlowContext(
    Node $node
): array {
    $contexts = [];

    $parent = $node->getAttribute('parent');

    while ($parent instanceof Node) {

        if ($parent instanceof Node\Stmt\If_) {
            $contexts[] = 'IF';
        } elseif ($parent instanceof Node\Stmt\ElseIf_) {
            $contexts[] = 'ELSEIF';
        } elseif ($parent instanceof Node\Stmt\Else_) {
            $contexts[] = 'ELSE';
        }

        $parent = $parent->getAttribute('parent');
    }

    return array_values(
        array_unique($contexts)
    );
}


/**
 * Return the conditionally executed blocks enclosing an AST node,
 * innermost first: the then-body of an if ("IF@<offset of the if>"),
 * an elseif body ("ELSEIF@..."), an else body ("ELSE@...") and a
 * catch body ("CATCH@...").
 *
 * Only the block that actually contains the node is recorded: a node in
 * an else body is NOT inside the if's then-body, even though the If_
 * node is one of its ancestors. A node in an if condition is in no block.
 */
function getBranchContext(
    Node $node
): array {
    $contexts = [];

    $child = $node;
    $parent = $node->getAttribute('parent');

    while ($parent instanceof Node) {
        $kind = null;

        if (
            $parent instanceof Node\Stmt\If_
            && in_array($child, $parent->stmts, true)
        ) {
            $kind = 'IF';
        } elseif (
            $parent instanceof Node\Stmt\ElseIf_
            && in_array($child, $parent->stmts, true)
        ) {
            $kind = 'ELSEIF';
        } elseif ($parent instanceof Node\Stmt\Else_) {
            $kind = 'ELSE';
        } elseif ($parent instanceof Node\Stmt\Catch_) {
            $kind = 'CATCH';
        }

        if ($kind !== null) {
            $contexts[] = $kind . '@' . $parent->getStartFilePos();
        }

        $child = $parent;
        $parent = $parent->getAttribute('parent');
    }

    return $contexts;
}


/**
 * Return the loops and switch cases enclosing an AST node,
 * innermost first.
 *
 * Each entry is "<KIND>@<start file offset>", so two nodes inside
 * the same loop or the same switch case get the same entry. Recovery
 * uses this to abstain when a SQL value can depend on loop iterations
 * or on which switch case ran.
 */
function getLoopContext(
    Node $node
): array {
    $contexts = [];

    $parent = $node->getAttribute('parent');

    while ($parent instanceof Node) {
        $kind = null;

        if ($parent instanceof Node\Stmt\For_) {
            $kind = 'FOR';
        } elseif ($parent instanceof Node\Stmt\Foreach_) {
            $kind = 'FOREACH';
        } elseif ($parent instanceof Node\Stmt\While_) {
            $kind = 'WHILE';
        } elseif ($parent instanceof Node\Stmt\Do_) {
            $kind = 'DO';
        } elseif ($parent instanceof Node\Stmt\Switch_) {
            $kind = 'SWITCH';
        } elseif ($parent instanceof Node\Stmt\Case_) {
            $kind = 'CASE';
        }

        if ($kind !== null) {
            $contexts[] = $kind . '@' . $parent->getStartFilePos();
        }

        $parent = $parent->getAttribute('parent');
    }

    return $contexts;
}


/**
 * Collect conditions controlling an AST node.
 *
 * Preserve the containing branch type and its condition.
 * ELSE uses the condition of its parent IF statement.
 */
/**
 * Return controlling PHP branch metadata.
 *
 * For ELSE branches, resolve the enclosing IF statement.
 * Suppress the duplicate IF entry associated with that ELSE.
 */
function getBranchConditions(
    Node $node,
    $prettyPrinter
): array {
    $conditions = [];
    $parent = $node->getAttribute('parent');

    while ($parent instanceof Node) {
        if ($parent instanceof Node\Stmt\ElseIf_) {
            $ifNode = $parent->getAttribute('parent');

            $conditions[] = [
                'branch' => 'ELSEIF',
                'condition' => expressionToString(
                    $parent->cond,
                    $prettyPrinter
                ),
                'branch_group_line' => (
                    $ifNode instanceof Node\Stmt\If_
                        ? $ifNode->getStartLine()
                        : null
                ),
            ];

            $parent = $ifNode;
            continue;
        }

        if ($parent instanceof Node\Stmt\Else_) {
            $ifNode = $parent->getAttribute('parent');

            if ($ifNode instanceof Node\Stmt\If_) {
                $conditions[] = [
                    'branch' => 'ELSE',
                    'condition' => expressionToString(
                        $ifNode->cond,
                        $prettyPrinter
                    ),
                    'branch_group_line' => $ifNode->getStartLine(),
                ];

                $parent = $ifNode->getAttribute('parent');
                continue;
            }
        }

        if ($parent instanceof Node\Stmt\If_) {
            $conditions[] = [
                'branch' => 'IF',
                'condition' => expressionToString(
                    $parent->cond,
                    $prettyPrinter
                ),
                'branch_group_line' => $parent->getStartLine(),
            ];
        }

        $parent = $parent->getAttribute('parent');
    }

    return $conditions;
}

/**
 * Executor calls directly inside a function body (nested closures,
 * functions and classes excluded), with their SQL argument node.
 */
function directExecutorCalls(Node\Stmt\Function_ $function, NodeFinder $nodeFinder): array
{
    $calls = [];

    foreach ($nodeFinder->find($function->stmts ?? [], function (Node $node) use ($function): bool {
        if (!(
            ($node instanceof Node\Expr\FuncCall && $node->name instanceof Node\Name
                && array_key_exists(strtolower($node->name->toString()), SQL_FUNCTION_ARGUMENT_POSITIONS))
            || ($node instanceof Node\Expr\MethodCall && $node->name instanceof Node\Identifier
                && in_array(strtolower($node->name->toString()), SQL_METHOD_NAMES, true))
        )) {
            return false;
        }
        for ($parent = $node->getAttribute('parent'); $parent !== null && $parent !== $function; $parent = $parent->getAttribute('parent')) {
            if ($parent instanceof Node\FunctionLike || $parent instanceof Node\Stmt\ClassLike) {
                return false;
            }
        }
        return true;
    }) as $call) {
        $executor = $call instanceof Node\Expr\FuncCall
            ? strtolower($call->name->toString())
            : strtolower($call->name->toString());
        $position = $call instanceof Node\Expr\FuncCall ? SQL_FUNCTION_ARGUMENT_POSITIONS[$executor] : 0;
        $calls[] = ['executor' => $executor, 'sql' => findSqlArgument($call, $position)];
    }

    return $calls;
}

/**
 * Find user-defined SQL wrapper functions across the project.
 *
 * A function is a wrapper only when ONE of its own parameters reaches ONE
 * direct executor call unmodified, as the whole SQL argument (the
 * `function query($query) { return mysqli_query($conn, $query); }` shape).
 * Abstain (not a wrapper, reason recorded) when the function:
 * - is defined conditionally or inside a namespace/class (not at file top level);
 * - is defined more than once with different bodies;
 * - calls itself (recursion);
 * - modifies the parameter, takes it by reference or as variadic;
 * - has several executor calls, or uses the parameter inside a larger string.
 * Calls through variable functions or dynamic method names are never matched.
 */
function findSqlWrappers(array $phpFiles, $parser, Standard $prettyPrinter, NodeFinder $nodeFinder): array
{
    $definitions = [];

    foreach ($phpFiles as $phpFile) {
        $code = file_get_contents($phpFile);
        if ($code === false) {
            continue;
        }
        try {
            $ast = $parser->parse($code);
        } catch (Error $error) {
            continue;
        }
        if ($ast === null) {
            continue;
        }
        foreach ($ast as $rootNode) {
            if ($rootNode instanceof Node) {
                connectParentNodes($rootNode);
            }
        }
        foreach ($nodeFinder->findInstanceOf($ast, Node\Stmt\Function_::class) as $function) {
            $definitions[strtolower($function->name->toString())][] = [
                'file' => $phpFile,
                'node' => $function,
                'body' => $prettyPrinter->prettyPrint([$function]),
            ];
        }
    }

    $wrappers = [];
    $rejected = [];

    foreach ($definitions as $name => $defs) {
        $analysed = array_map(fn ($def) => analyseWrapperDefinition($def, $name, $nodeFinder), $defs);
        $shaped = array_filter($analysed, fn ($a) => $a['shaped']);

        if (!$shaped) {
            continue; // no definition passes a parameter straight to an executor
        }

        $where = array_map(fn ($d) => $d['file'] . ':' . $d['node']->getStartLine(), $defs);
        $reason = null;

        if (count(array_unique(array_column($defs, 'body'))) > 1) {
            $reason = 'defined more than once with different bodies';
        } else {
            foreach ($analysed as $a) {
                if ($a['reason'] !== null) {
                    $reason = $a['reason'];
                    break;
                }
            }
        }

        if ($reason !== null) {
            $rejected[$name] = ['reason' => $reason, 'definitions' => $where];
            continue;
        }

        $first = $analysed[0];
        $wrappers[$name] = [
            'name' => $defs[0]['node']->name->toString(),
            'parameter' => $first['parameter'],
            'parameter_index' => $first['parameter_index'],
            'executor' => $first['executor'],
            'definitions' => $where,
        ];
    }

    return ['wrappers' => $wrappers, 'rejected' => $rejected];
}

/**
 * One definition: is it wrapper-shaped (a parameter is the whole SQL
 * argument of an executor), and if so, is there a reason to abstain?
 */
function analyseWrapperDefinition(array $def, string $name, NodeFinder $nodeFinder): array
{
    $function = $def['node'];
    $params = [];
    foreach ($function->params as $index => $param) {
        if ($param->var instanceof Node\Expr\Variable && is_string($param->var->name)) {
            $params[$param->var->name] = ['index' => $index, 'node' => $param];
        }
    }

    $uses = [];
    foreach (directExecutorCalls($function, $nodeFinder) as $call) {
        if ($call['sql'] instanceof Node\Expr\Variable && is_string($call['sql']->name)
            && isset($params[$call['sql']->name])) {
            $uses[] = ['executor' => $call['executor'], 'parameter' => $call['sql']->name];
        }
    }

    $result = ['shaped' => (bool) $uses, 'reason' => null, 'parameter' => null, 'parameter_index' => null, 'executor' => null];
    if (!$uses) {
        return $result;
    }

    if ($function->getAttribute('parent') !== null) {
        $result['reason'] = 'defined conditionally or inside a namespace/class';
        return $result;
    }
    if (count($uses) > 1) {
        $result['reason'] = 'several executor calls use parameters';
        return $result;
    }

    $parameter = $uses[0]['parameter'];
    $paramNode = $params[$parameter]['node'];
    if ($paramNode->byRef || $paramNode->variadic) {
        $result['reason'] = 'SQL parameter is by reference or variadic';
        return $result;
    }

    $mutated = $nodeFinder->findFirst($function->stmts ?? [], fn (Node $n) =>
        (($n instanceof Node\Expr\Assign || $n instanceof Node\Expr\AssignOp || $n instanceof Node\Expr\AssignRef
            || $n instanceof Node\Expr\PreInc || $n instanceof Node\Expr\PostInc
            || $n instanceof Node\Expr\PreDec || $n instanceof Node\Expr\PostDec)
            && $n->var instanceof Node\Expr\Variable && $n->var->name === $parameter)
        || ($n instanceof Node\Stmt\Unset_
            && array_filter($n->vars, fn ($v) => $v instanceof Node\Expr\Variable && $v->name === $parameter)));
    if ($mutated) {
        $result['reason'] = 'SQL parameter is modified before execution';
        return $result;
    }

    $recursive = $nodeFinder->findFirst($function->stmts ?? [], fn (Node $n) =>
        $n instanceof Node\Expr\FuncCall && $n->name instanceof Node\Name && strtolower($n->name->toString()) === $name);
    if ($recursive) {
        $result['reason'] = 'recursive';
        return $result;
    }

    $result['parameter'] = $parameter;
    $result['parameter_index'] = $params[$parameter]['index'];
    $result['executor'] = $uses[0]['executor'];
    return $result;
}

/**
 * SQL argument of a call to a wrapper: the named argument matching the
 * wrapper's parameter, else the positional one (null if unpacked/unsure).
 */
function wrapperSqlArgument(Node\Expr\FuncCall $call, array $wrapper): ?Node
{
    foreach ($call->args as $argument) {
        if ($argument instanceof Node\Arg && $argument->name instanceof Node\Identifier) {
            if ($argument->name->toString() === $wrapper['parameter']) {
                return $argument->value;
            }
        }
    }
    for ($index = 0; $index <= $wrapper['parameter_index']; $index++) {
        $argument = $call->args[$index] ?? null;
        if (!$argument instanceof Node\Arg || $argument->unpack || $argument->name !== null) {
            return null;
        }
    }
    return $call->args[$wrapper['parameter_index']]->value;
}

$results = [];
$wrapperResults = [];
$assignments = [];
$functions = [];
$includes = [];
$unsupportedOperations = [];
$phpFiles = collectPhpFiles($inputPath);
$sqlWrapperScan = findSqlWrappers($phpFiles, $parser, $prettyPrinter, $nodeFinder);
$sqlWrappers = $sqlWrapperScan['wrappers'];


foreach ($phpFiles as $phpFile) {

    $code = file_get_contents($phpFile);

    if ($code === false) {
        continue;
    }

    try {
        $ast = $parser->parse($code);

        if ($ast === null) {
            continue;
        }

	        foreach ($ast as $rootNode) {
            if ($rootNode instanceof Node) {
                connectParentNodes(
                    $rootNode
                );
            }
        }

    } catch (Error $error) {

        $results[] = [
            'source_file' => $phpFile,
            'source_line' => $error->getStartLine(),
            'executor' => 'PARSE_ERROR',
            'sql_argument' => null,
            'status' => 'UNRESOLVED',
            'reason' => $error->getMessage(),
        ];

        continue;
    }

        /*
     * Collect simple named PHP function definitions.
     * Metadata only; no PHP functions are executed.
     */
    $functionNodes = $nodeFinder->findInstanceOf(
        $ast,
        Node\Stmt\Function_::class
    );

    foreach ($functionNodes as $functionNode) {

        $parameters = [];
        $supportedParameters = true;

        foreach ($functionNode->params as $parameter) {
            if (
                !$parameter->var instanceof Node\Expr\Variable
                || !is_string($parameter->var->name)
            ) {
                $supportedParameters = false;
                break;
            }

            $parameters[] = $parameter->var->name;
        }

        $returnExpression = null;

        // Only a function whose entire body is one return
        // statement qualifies for simple return extraction.
        $statements = $functionNode->stmts ?? [];

        if (
            $supportedParameters
            && count($statements) === 1
            && $statements[0] instanceof Node\Stmt\Return_
            && $statements[0]->expr !== null
        ) {
            $returnExpression = expressionToString(
                $statements[0]->expr,
                $prettyPrinter
            );
        }

        $functions[] = [
            'name' => $functionNode->name->toString(),
            'source_file' => $phpFile,
            'source_line' => $functionNode->getStartLine(),
            'parameters' => $parameters,
            'return_expression' => $returnExpression,
            'simple_return' => $returnExpression !== null,
        ];
    }


    /*
     * Collect PHP include/require relationships.
     * Only static __DIR__ + literal paths are resolved.
     */
    $includeNodes = $nodeFinder->findInstanceOf(
        $ast,
        Node\Expr\Include_::class
    );

    foreach ($includeNodes as $includeNode) {
        $includeTypes = [
            Node\Expr\Include_::TYPE_INCLUDE => 'include',
            Node\Expr\Include_::TYPE_INCLUDE_ONCE => 'include_once',
            Node\Expr\Include_::TYPE_REQUIRE => 'require',
            Node\Expr\Include_::TYPE_REQUIRE_ONCE => 'require_once',
        ];

        $expression = $includeNode->expr;
        $targetFile = null;

        if (
            $expression instanceof Node\Expr\BinaryOp\Concat
            && $expression->left instanceof Node\Scalar\MagicConst\Dir
            && $expression->right instanceof Node\Scalar\String_
        ) {
            $relativePath = ltrim(
                str_replace(
                    '\\',
                    '/',
                    $expression->right->value
                ),
                '/'
            );

            $candidate = dirname($phpFile)
                . DIRECTORY_SEPARATOR
                . $relativePath;

            if (is_file($candidate)) {
                $targetFile = realpath($candidate);
            }
        }

        $includes[] = [
            'source_file' => $phpFile,
            'source_line' => $includeNode->getStartLine(),
            'include_type' => $includeTypes[$includeNode->type] ?? 'unknown',
            'expression' => expressionToString(
                $expression,
                $prettyPrinter
            ),
            'target_file' => $targetFile,
            'supported' => $targetFile !== null,
            'control_flow' => getControlFlowContext(
                $includeNode
            ),
        ];
    }

    /*
     * Collect unsupported variable operations.
     *
     * Metadata only. The extractor does not execute PHP.
     */
    $unsupportedNodes = $nodeFinder->find(
        $ast,
        function (Node $node): bool {
            return (
                $node instanceof Node\Stmt\Unset_
                ||
                $node instanceof Node\Expr\AssignRef
            );
        }
    );

    foreach ($unsupportedNodes as $unsupportedNode) {
        $variables = [];
        $operationType = 'UNKNOWN';

        if ($unsupportedNode instanceof Node\Stmt\Unset_) {
            $operationType = 'UNSET';

            foreach ($unsupportedNode->vars as $variableNode) {
                if (
                    $variableNode instanceof Node\Expr\Variable
                    && is_string($variableNode->name)
                ) {
                    $variables[] = '$' . $variableNode->name;
                }
            }
        } elseif ($unsupportedNode instanceof Node\Expr\AssignRef) {
            $operationType = 'REFERENCE_ASSIGN';

            foreach ([$unsupportedNode->var, $unsupportedNode->expr] as $variableNode) {
                if (
                    $variableNode instanceof Node\Expr\Variable
                    && is_string($variableNode->name)
                ) {
                    $variables[] = '$' . $variableNode->name;
                }
            }
        }

        $unsupportedOperations[] = [
            'source_file' => $phpFile,
            'source_line' => $unsupportedNode->getStartLine(),
            'operation_type' => $operationType,
            'variables' => array_values(array_unique($variables)),
            'control_flow' => getControlFlowContext($unsupportedNode),
        ];
    }

    /*
     * Collect variable assignments.
     *
     * Examples:
     *
     * $sql = "SELECT * FROM users";
     *
     * $sql = "DELETE FROM users ";
     * $sql .= "WHERE id = " . $id;
     */

    $assignmentNodes = $nodeFinder->find(
        $ast,
        function (Node $node): bool {
            return (
                $node instanceof Node\Expr\Assign
                ||
                $node instanceof Node\Expr\AssignOp
                ||
                $node instanceof Node\Expr\PreInc
                ||
                $node instanceof Node\Expr\PostInc
                ||
                $node instanceof Node\Expr\PreDec
                ||
                $node instanceof Node\Expr\PostDec
            );
        }
    );

    foreach ($assignmentNodes as $assignment) {

        if (!$assignment->var instanceof Node\Expr\Variable) {
            continue;
        }

        if (!is_string($assignment->var->name)) {
            continue;
        }

        $variableName = '$' . $assignment->var->name;

        $assignmentType = 'UNSUPPORTED_MUTATION';

        if ($assignment instanceof Node\Expr\Assign) {
            $assignmentType = 'ASSIGN';
        } elseif (
            $assignment instanceof Node\Expr\AssignOp\Concat
        ) {
            $assignmentType = 'CONCAT_ASSIGN';
        }

        $functionCallMetadata = null;

        $expressionNode = property_exists($assignment, 'expr')
            ? $assignment->expr
            : null;

        if (
            $expressionNode instanceof Node\Expr\FuncCall
            && $expressionNode->name instanceof Node\Name
        ) {
            $arguments = [];

            foreach ($expressionNode->args as $argument) {
                $value = $argument->value;

                $argumentInfo = [
                    'expression' => expressionToString(
                        $value,
                        $prettyPrinter
                    ),
                    'kind' => 'UNSUPPORTED',
                ];

                if (
                    $value instanceof Node\Expr\BinaryOp\Coalesce
                    && $value->left instanceof Node\Expr\ArrayDimFetch
                    && $value->left->var instanceof Node\Expr\Variable
                    && is_string($value->left->var->name)
                    && $value->left->dim instanceof Node\Scalar\String_
                    && $value->right instanceof Node\Scalar\String_
                ) {
                    $argumentInfo = [
                        'expression' => expressionToString(
                            $value,
                            $prettyPrinter
                        ),
                        'kind' => 'COALESCE',
                        'input_source' => $value->left->var->name,
                        'input_key' => $value->left->dim->value,
                        'default_expression' => expressionToString(
                            $value->right,
                            $prettyPrinter
                        ),
                    ];
                }

                $arguments[] = $argumentInfo;
            }

            $functionCallMetadata = [
                'name' => $expressionNode->name->toString(),
                'arguments' => $arguments,
            ];
        }

        $assignments[] = [
            'source_file' => $phpFile,
            'source_line' => $assignment->getStartLine(),
            'variable' => $variableName,
            'assignment_type' => $assignmentType,
            'expression' => $expressionNode instanceof Node
                ? expressionToString(
                    $expressionNode,
                    $prettyPrinter
                )
                : null,
            'control_flow' => getControlFlowContext(
                $assignment
            ),
            'branch_conditions' => getBranchConditions(
                $assignment,
                $prettyPrinter
            ),
            'loop_context' => getLoopContext($assignment),
            'branch_context' => getBranchContext($assignment),
            'function_call' => $functionCallMetadata,
        ];
    }
    /*
     * Detect procedural calls:
     *
     * mysqli_query($conn, $sql)
     * mysql_query($sql)
     */

    $functionCalls = $nodeFinder->findInstanceOf(
        $ast,
        Node\Expr\FuncCall::class
    );

    foreach ($functionCalls as $call) {

        if (!$call->name instanceof Node\Name) {
            continue;
        }

        $functionName = strtolower(
            $call->name->toString()
        );

        /*
         * Database calls that are recognised but not supported
         * (other DBMS APIs). They are reported, never dropped.
         */
        if (in_array($functionName, UNSUPPORTED_DB_FUNCTIONS, true)) {
            $results[] = [
                'source_file' => $phpFile,
                'source_line' => $call->getStartLine(),
                'loop_context' => getLoopContext($call),
                'branch_context' => getBranchContext($call),
                'executor' => $functionName,
                'sql_argument' => null,
                'status' => 'UNSUPPORTED_EXECUTOR',
                'reason' => 'unsupported_executor',
            ];

            continue;
        }

        /*
         * A call to a recognised SQL wrapper (see findSqlWrappers) is an
         * SQL site of its own, at the call's file and line. It carries the
         * wrapper's underlying executor, so recovery and conversion apply
         * exactly the rules of a direct executor call. These sites are
         * appended after all direct sites, so existing query IDs keep
         * their numbers.
         */
        if (isset($sqlWrappers[$functionName]) && count($call->name->getParts()) === 1) {
            $wrapper = $sqlWrappers[$functionName];
            $sqlArgument = wrapperSqlArgument($call, $wrapper);
            $wrapperResults[] = [
                'source_file' => $phpFile,
                'source_line' => $call->getStartLine(),
                'loop_context' => getLoopContext($call),
                'branch_context' => getBranchContext($call),
                'executor' => $wrapper['executor'],
                'sql_argument' => $sqlArgument === null ? null : expressionToString($sqlArgument, $prettyPrinter),
                'status' => $sqlArgument === null ? 'UNRESOLVED' : 'LOCATED',
                'reason' => $sqlArgument === null ? 'missing_sql_argument' : null,
                'wrapper' => [
                    'name' => $wrapper['name'],
                    'parameter' => $wrapper['parameter'],
                    'definitions' => $wrapper['definitions'],
                ],
            ];

            continue;
        }

        if (!array_key_exists($functionName, SQL_FUNCTION_ARGUMENT_POSITIONS)) {
            continue;
        }

        /*
         * Position of the SQL argument, e.g.
         * mysqli_query($conn, $sql) -> 1, mysql_query($sql) -> 0.
         * PHP 8 named arguments (query: $sql) take precedence.
         */
        $sqlArgument = findSqlArgument(
            $call,
            SQL_FUNCTION_ARGUMENT_POSITIONS[$functionName]
        );


        if ($sqlArgument === null) {

            $results[] = [
                'source_file' => $phpFile,
                'source_line' => $call->getStartLine(),
                'loop_context' => getLoopContext($call),
                'branch_context' => getBranchContext($call),
                'executor' => $functionName,
                'sql_argument' => null,
                'status' => 'UNRESOLVED',
                'reason' => 'missing_sql_argument',
            ];

            continue;
        }


        $sqlNode = $sqlArgument;


        $results[] = [
            'source_file' => $phpFile,
            'source_line' => $call->getStartLine(),
            'loop_context' => getLoopContext($call),
                'branch_context' => getBranchContext($call),
            'executor' => $functionName,
            'sql_argument' => expressionToString(
                $sqlNode,
                $prettyPrinter
            ),
            'status' => 'LOCATED',
            'reason' => null,
        ];
    }


    /*
     * Detect object method calls:
     *
     * $conn->query($sql)
     * $pdo->query($sql)
     * $pdo->prepare($sql)
     * $pdo->exec($sql)
     */

    $methodCalls = $nodeFinder->findInstanceOf(
        $ast,
        Node\Expr\MethodCall::class
    );

    foreach ($methodCalls as $call) {

        $methodName = getMethodName(
            $call->name
        );

        if ($methodName === null) {
            continue;
        }

        $normalizedMethod =
            strtolower($methodName);


        if (!in_array(
            $normalizedMethod,
            SQL_METHOD_NAMES,
            true
        )) {
            continue;
        }

        $sqlArgument = findSqlArgument($call, 0);


        if ($sqlArgument === null) {

            $results[] = [
                'source_file' => $phpFile,
                'source_line' => $call->getStartLine(),
                'loop_context' => getLoopContext($call),
                'branch_context' => getBranchContext($call),
                'executor' => $normalizedMethod,
                'sql_argument' => null,
                'status' => 'UNRESOLVED',
                'reason' => 'missing_sql_argument',
            ];

            continue;
        }


        $sqlNode = $sqlArgument;


        $results[] = [
            'source_file' => $phpFile,
            'source_line' => $call->getStartLine(),
            'loop_context' => getLoopContext($call),
                'branch_context' => getBranchContext($call),
            'executor' => $normalizedMethod,
            'sql_argument' => expressionToString(
                $sqlNode,
                $prettyPrinter
            ),
            'status' => 'LOCATED',
            'reason' => null,
        ];
    }
}


$output = [
    'sql_calls' => array_merge($results, $wrapperResults),
    'assignments' => $assignments,
    'functions' => $functions,
    'includes' => $includes,
    'unsupported_operations' => $unsupportedOperations,
];

// Only when wrapper-shaped functions exist, so other output is unchanged.
if ($sqlWrapperScan['wrappers'] || $sqlWrapperScan['rejected']) {
    $output['sql_wrappers'] = $sqlWrapperScan;
}

echo json_encode(
    $output,
    JSON_PRETTY_PRINT |
    JSON_UNESCAPED_SLASHES
);
