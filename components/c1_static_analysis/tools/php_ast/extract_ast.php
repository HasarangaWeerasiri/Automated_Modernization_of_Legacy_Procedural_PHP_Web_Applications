<?php

declare(strict_types=1);

/*
 * Component 1 / Static Analysis Core - PHP AST extractor (schema 1.0)
 *
 * Reads ONE php file and prints ONE JSON document to stdout.
 * It is a pure function of (file bytes, file id, pinned parser version):
 * no timestamps, no absolute paths, no host-dependent values.
 *
 * Usage:
 *   php extract_ast.php --version
 *   php extract_ast.php --file <path> --file-id f000
 *
 * Output (stdout):
 *   {"fileId","rootId","parseErrors":[...],"nodes":[...]}
 * `nodes` is in PRE-ORDER, so node ids ascend in list order.
 *
 * Node:
 *   id        "<fileId>#<5-digit preorder ordinal>", the root is #00000
 *   kind      nikic/PHP-Parser type name ("Stmt_Echo"), or "File" for the root
 *   loc       {startLine,endLine,startCol,endCol,startByte,endByte}
 *             lines are 1-based. Columns and bytes are 0-based BYTE offsets,
 *             end exclusive: source_bytes[startByte:endByte] is the node text.
 *   parent    id of the parent, null for the root
 *   children  child ids, in pre-order
 *   fields    sub-node name -> child id | [child ids] (null holes kept) | null
 *   attrs     scalar sub-nodes. Keys starting with "_" are parser attributes
 *             (_kind, _rawValue, _parenthesized).
 *             A string that is not valid UTF-8 is written as {"__b64": "..."}.
 *             A non-finite float is written as {"__float": "INF"}.
 *
 * Stmt_InlineHTML: attrs.value holds the text exactly as PHP would output it.
 * PHP itself swallows ONE newline directly after "?>", so that newline is not
 * in the value. Use loc.startByte/endByte for exact source slices.
 */

ini_set('serialize_precision', '-1');
ini_set('display_errors', 'stderr');

require __DIR__ . '/../../vendor/autoload.php';

use PhpParser\ErrorHandler;
use PhpParser\Node;
use PhpParser\ParserFactory;
use PhpParser\PhpVersion;

/** The PHP syntax level we parse as. Recorded in manifest.json. Do not make it host-dependent. */
const TARGET_PHP_VERSION = '8.3';

function parserVersion(): string
{
    if (class_exists('Composer\InstalledVersions')) {
        $v = \Composer\InstalledVersions::getPrettyVersion('nikic/php-parser');
        if (is_string($v)) {
            return $v;
        }
    }
    return 'unknown';
}

/** Make a scalar JSON-safe without losing information. */
function encodeScalar($v)
{
    if (is_string($v)) {
        if (preg_match('//u', $v) === 1) {
            return $v;
        }
        return ['__b64' => base64_encode($v)];
    }
    if (is_float($v) && !is_finite($v)) {
        return ['__float' => is_nan($v) ? 'NAN' : ($v > 0 ? 'INF' : '-INF')];
    }
    return $v;
}

final class Emitter
{
    /** @var array<int, array<string, mixed>> */
    public array $nodes = [];
    private int $counter = 0;
    /** @var int[] byte offset of the start of each line (index 0 = line 1) */
    private array $lineStarts;

    public function __construct(private string $fileId, string $src)
    {
        $this->lineStarts = [0];
        $len = strlen($src);
        $pos = 0;
        while (($pos = strpos($src, "\n", $pos)) !== false) {
            $pos++;
            if ($pos <= $len) {
                $this->lineStarts[] = $pos;
            }
        }
    }

    private function nextId(): string
    {
        return sprintf('%s#%05d', $this->fileId, $this->counter++);
    }

    private function lineStart(int $line): int
    {
        return $this->lineStarts[$line - 1] ?? 0;
    }

    /** @return array<string, int>|null */
    private function loc(Node $n): ?array
    {
        $sl = $n->getStartLine();
        $el = $n->getEndLine();
        $sp = $n->getStartFilePos();
        $ep = $n->getEndFilePos();
        if ($sl < 1 || $el < 1 || $sp < 0 || $ep < 0) {
            return null;
        }
        $startByte = $sp;
        $endByte = $ep + 1;
        return [
            'startLine' => $sl,
            'endLine' => $el,
            'startCol' => $startByte - $this->lineStart($sl),
            'endCol' => $endByte - $this->lineStart($el),
            'startByte' => $startByte,
            'endByte' => $endByte,
        ];
    }

    /** Emit the synthetic root that holds the top-level statements. */
    public function emitRoot(array $stmts, int $srcLen, int $lastLine): string
    {
        $id = $this->nextId();
        $idx = count($this->nodes);
        $this->nodes[$idx] = [];
        $list = [];
        foreach ($stmts as $s) {
            $list[] = $this->visit($s, $id);
        }
        $this->nodes[$idx] = [
            'id' => $id,
            'kind' => 'File',
            'loc' => [
                'startLine' => 1,
                'endLine' => $lastLine,
                'startCol' => 0,
                'endCol' => $srcLen - $this->lineStart($lastLine),
                'startByte' => 0,
                'endByte' => $srcLen,
            ],
            'parent' => null,
            'children' => $list,
            'fields' => ['stmts' => $list],
            'attrs' => new \stdClass(),
        ];
        return $id;
    }

    private function visit(Node $n, string $parentId): string
    {
        $id = $this->nextId();
        $idx = count($this->nodes);
        $this->nodes[$idx] = []; // reserve the slot so list order == pre-order

        $children = [];
        $fields = [];
        $attrs = [];

        foreach ($n->getSubNodeNames() as $name) {
            $v = $n->$name;
            if ($v instanceof Node) {
                $cid = $this->visit($v, $id);
                $fields[$name] = $cid;
                $children[] = $cid;
            } elseif (is_array($v)) {
                $list = [];
                foreach ($v as $item) {
                    if ($item instanceof Node) {
                        $cid = $this->visit($item, $id);
                        $list[] = $cid;
                        $children[] = $cid;
                    } elseif ($item === null) {
                        $list[] = null;
                    } else {
                        // Unexpected: a scalar inside a node list. Keep it, never drop silently.
                        $list[] = ['__scalar' => encodeScalar($item)];
                    }
                }
                $fields[$name] = $list;
            } elseif ($v === null) {
                $fields[$name] = null;
            } else {
                $attrs[$name] = encodeScalar($v);
            }
        }

        foreach (['kind', 'rawValue', 'parenthesized'] as $a) {
            if ($n->hasAttribute($a)) {
                $attrs['_' . $a] = encodeScalar($n->getAttribute($a));
            }
        }

        $this->nodes[$idx] = [
            'id' => $id,
            'kind' => $n->getType(),
            'loc' => $this->loc($n),
            'parent' => $parentId,
            'children' => $children,
            'fields' => $fields === [] ? new \stdClass() : $fields,
            'attrs' => $attrs === [] ? new \stdClass() : $attrs,
        ];
        return $id;
    }
}

function main(array $argv): int
{
    $opts = getopt('', ['file:', 'file-id:', 'version']);
    if (isset($opts['version'])) {
        echo json_encode(['name' => 'nikic/php-parser', 'version' => parserVersion(), 'targetPhpVersion' => TARGET_PHP_VERSION]) . "\n";
        return 0;
    }
    if (!isset($opts['file'], $opts['file-id']) || !is_string($opts['file']) || !is_string($opts['file-id'])) {
        fwrite(STDERR, "Usage: php extract_ast.php --file <path> --file-id fNNN | --version\n");
        return 2;
    }
    if (!preg_match('/^f\d{3,}$/', $opts['file-id'])) {
        fwrite(STDERR, "Bad --file-id (expected f000, f001, ...): {$opts['file-id']}\n");
        return 2;
    }
    $src = @file_get_contents($opts['file']);
    if ($src === false) {
        fwrite(STDERR, "Cannot read file: {$opts['file']}\n");
        return 1;
    }

    $parser = (new ParserFactory())->createForVersion(PhpVersion::fromString(TARGET_PHP_VERSION));
    $errors = new ErrorHandler\Collecting();
    $stmts = $parser->parse($src, $errors) ?? [];

    $parseErrors = [];
    foreach ($errors->getErrors() as $e) {
        $sl = $e->getStartLine();
        $el = $e->getEndLine();
        $parseErrors[] = [
            'message' => $e->getRawMessage(),
            'startLine' => $sl > 0 ? $sl : null,
            'endLine' => $el > 0 ? $el : null,
        ];
    }

    $lastLine = substr_count($src, "\n") + 1;
    $emitter = new Emitter($opts['file-id'], $src);
    $rootId = $emitter->emitRoot($stmts, strlen($src), $lastLine);

    $json = json_encode(
        ['fileId' => $opts['file-id'], 'rootId' => $rootId, 'parseErrors' => $parseErrors, 'nodes' => $emitter->nodes],
        JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRESERVE_ZERO_FRACTION | JSON_THROW_ON_ERROR
    );
    echo $json . "\n";
    return 0;
}

try {
    exit(main($argv));
} catch (\Throwable $t) {
    fwrite(STDERR, get_class($t) . ': ' . $t->getMessage() . "\n");
    exit(1);
}
