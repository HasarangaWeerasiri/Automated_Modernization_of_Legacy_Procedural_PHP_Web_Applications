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

$results = [];
$assignments = [];
$functions = [];
$includes = [];
$unsupportedOperations = [];
$phpFiles = collectPhpFiles($inputPath);


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
    'sql_calls' => $results,
    'assignments' => $assignments,
    'functions' => $functions,
    'includes' => $includes,
    'unsupported_operations' => $unsupportedOperations,
];

echo json_encode(
    $output,
    JSON_PRETTY_PRINT |
    JSON_UNESCAPED_SLASHES
);
