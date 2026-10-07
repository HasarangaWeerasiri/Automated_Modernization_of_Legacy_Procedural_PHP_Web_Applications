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

$results = [];
$assignments = [];
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
                $node instanceof Node\Expr\AssignOp\Concat
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

        $assignmentType =
            $assignment instanceof Node\Expr\AssignOp\Concat
                ? 'CONCAT_ASSIGN'
                : 'ASSIGN';

	        $assignments[] = [
            'source_file' => $phpFile,
            'source_line' => $assignment->getStartLine(),
            'variable' => $variableName,
            'assignment_type' => $assignmentType,
            'expression' => expressionToString(
                $assignment->expr,
                $prettyPrinter
            ),
            'control_flow' => getControlFlowContext(
                $assignment
            ),
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

        if (!in_array(
            $functionName,
            [
                'mysqli_query',
                'mysql_query',
            ],
            true
        )) {
            continue;
        }


        /*
         * mysqli_query:
         * argument 0 = connection
         * argument 1 = SQL
         *
         * mysql_query:
         * argument 0 = SQL
         */

        $sqlArgumentIndex =
            $functionName === 'mysqli_query'
                ? 1
                : 0;


        if (!isset($call->args[$sqlArgumentIndex])) {

            $results[] = [
                'source_file' => $phpFile,
                'source_line' => $call->getStartLine(),
                'executor' => $functionName,
                'sql_argument' => null,
                'status' => 'UNRESOLVED',
                'reason' => 'missing_sql_argument',
            ];

            continue;
        }


        $sqlNode =
            $call->args[$sqlArgumentIndex]->value;


        $results[] = [
            'source_file' => $phpFile,
            'source_line' => $call->getStartLine(),
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
            [
                'query',
                'prepare',
                'exec',
            ],
            true
        )) {
            continue;
        }


        if (!isset($call->args[0])) {

            $results[] = [
                'source_file' => $phpFile,
                'source_line' => $call->getStartLine(),
                'executor' => $normalizedMethod,
                'sql_argument' => null,
                'status' => 'UNRESOLVED',
                'reason' => 'missing_sql_argument',
            ];

            continue;
        }


        $sqlNode =
            $call->args[0]->value;


        $results[] = [
            'source_file' => $phpFile,
            'source_line' => $call->getStartLine(),
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
];

echo json_encode(
    $output,
    JSON_PRETTY_PRINT |
    JSON_UNESCAPED_SLASHES
);