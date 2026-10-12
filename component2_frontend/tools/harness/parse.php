<?php
/**
 * Component 2 whole-app test harness, AST producer: parse only, never execute.
 *
 * Writes one AST file per PHP source in Member 01's format (CONTEXT.md section 3):
 *
 *   {schemaVersion, fileId, path, sha256, bytes, parseOk, parseErrors, rootId, nodeCount,
 *    nodes: [{id, kind, parent, children, fields, attrs, loc}]}
 *
 * Nodes are nikic/php-parser's nodes under one root `File`, numbered in pre-order from
 * "<fileId>#00000" (parent before children, children in sub-node order). A sub-node that is a node,
 * a list of nodes or absent (null) is a role in `fields` (an id, a list of ids, or null); any other
 * sub-node value is in `attrs`, together with nikic's `kind` and `rawValue` attributes as `_kind`
 * and `_rawValue`. `loc` comes from the node's byte range only: lines 1-based, columns 0-based, every
 * end exclusive. Object keys are written in sorted order, as in Member 01's files.
 *
 * Checked node by node against Member 01's real AST of HMS admin-panel1.php
 * (legacy-apps/real/, tests/test_real_ast.py). Not shown by that file, so still our own choice:
 * the items of `parseErrors` (message strings), other nikic attributes (e.g. a heredoc's label),
 * and how non-ASCII text is escaped.
 *
 * Usage: php parse.php <manifest.json>
 *   manifest = {"meta": <json file>, "files": [{"source", "out", "fileId", "path"}]}
 *   meta receives {"parser", "php", "syntax": {<fileId>: "newest" | "5.6"}}
 */

require __DIR__ . '/vendor/autoload.php';

use PhpParser\ErrorHandler\Collecting;
use PhpParser\Node;
use PhpParser\ParserFactory;
use PhpParser\PhpVersion;

const PARSER_PACKAGE = 'nikic/php-parser';
const SCHEMA_VERSION = '1.0';

final class AstWriter
{
    /** @var array<int, array> */
    public array $nodes = [];
    /** @var int[] byte offset of the start of each line */
    private array $lineStarts = [0];

    public function __construct(private string $fileId, private string $code)
    {
        $offset = 0;
        while (($offset = strpos($code, "\n", $offset)) !== false) {
            $this->lineStarts[] = ++$offset;
        }
    }

    private function id(int $ordinal): string
    {
        return sprintf('%s#%05d', $this->fileId, $ordinal);
    }

    /** [1-based line, 0-based byte column] of a byte offset. */
    private function position(int $offset): array
    {
        $lo = 0;
        $hi = count($this->lineStarts) - 1;
        while ($lo < $hi) {
            $mid = intdiv($lo + $hi + 1, 2);
            if ($this->lineStarts[$mid] <= $offset) {
                $lo = $mid;
            } else {
                $hi = $mid - 1;
            }
        }
        return [$lo + 1, $offset - $this->lineStarts[$lo]];
    }

    private function loc(int $startByte, int $endByte): array
    {
        [$startLine, $startCol] = $this->position($startByte);
        [$endLine, $endCol] = $this->position($endByte);
        return ['startLine' => $startLine, 'endLine' => $endLine, 'startCol' => $startCol, 'endCol' => $endCol,
                'startByte' => $startByte, 'endByte' => $endByte];
    }

    /** @param Node[] $stmts */
    public function file(array $stmts): void
    {
        $this->nodes[0] = null;  // reserve the root's place: ids are pre-order
        $children = [];
        foreach ($stmts as $stmt) {
            $children[] = $this->node($stmt, $this->id(0));
        }
        $this->nodes[0] = ['id' => $this->id(0), 'kind' => 'File', 'parent' => null, 'children' => $children,
                           'fields' => ['stmts' => $children], 'attrs' => new stdClass(),
                           'loc' => $this->loc(0, strlen($this->code))];
    }

    private function node(Node $node, string $parent): string
    {
        $ordinal = count($this->nodes);
        $id = $this->id($ordinal);
        $this->nodes[$ordinal] = null;
        $children = [];
        $fields = [];
        $attrs = [];
        foreach ($node->getSubNodeNames() as $name) {
            $value = $node->$name;
            if ($value instanceof Node) {
                $fields[$name] = $children[] = $this->node($value, $id);
            } elseif ($value === null) {
                $fields[$name] = null;  // an absent node (no else, an unnamed argument) is still a role
            } elseif (is_array($value)) {
                $ids = [];
                foreach ($value as $item) {
                    if ($item instanceof Node) {
                        $ids[] = $children[] = $this->node($item, $id);
                    } else {
                        $ids[] = null;  // e.g. a skipped position in list(, $b)
                    }
                }
                $fields[$name] = $ids;
            } else {
                $attrs[$name] = $value;
            }
        }
        foreach (['kind', 'rawValue'] as $attribute) {
            if ($node->hasAttribute($attribute)) {
                $attrs['_' . $attribute] = $node->getAttribute($attribute);
            }
        }
        $this->nodes[$ordinal] = [
            'id' => $id, 'kind' => $node->getType(), 'parent' => $parent, 'children' => $children,
            'fields' => $fields ?: new stdClass(), 'attrs' => $attrs ?: new stdClass(),
            'loc' => $this->loc($node->getStartFilePos(), $node->getEndFilePos() + 1),
        ];
        return $id;
    }
}

/** The same value with every object's keys in sorted order (lists keep their order). */
function sorted_keys($value)
{
    if (!is_array($value)) {
        return $value;
    }
    if (!array_is_list($value)) {
        ksort($value, SORT_STRING);
    }
    return array_map('sorted_keys', $value);
}

function parse_file(string $code): array
{
    $factory = new ParserFactory();
    $attempts = ['newest' => fn() => $factory->createForNewestSupportedVersion(),
                 '5.6' => fn() => $factory->createForVersion(PhpVersion::fromString('5.6'))];
    $result = null;
    foreach ($attempts as $label => $make) {
        $errors = new Collecting();
        $ast = $make()->parse($code, $errors);
        $result = ['syntax' => $label, 'ast' => $ast,
                   'errors' => array_map(fn($e) => $e->getRawMessage() . ' on line ' . $e->getStartLine(),
                                         $errors->getErrors())];
        if (!$result['errors'] && $ast !== null) {
            break;
        }
    }
    return $result;
}

$manifest = json_decode(file_get_contents($argv[1]), true);
$meta = ['parser' => PARSER_PACKAGE . ' ' . \Composer\InstalledVersions::getPrettyVersion(PARSER_PACKAGE),
         'php' => PHP_VERSION, 'syntax' => []];
$flags = JSON_INVALID_UTF8_SUBSTITUTE | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE;
foreach ($manifest['files'] as $entry) {
    $code = file_get_contents($entry['source']);
    $parsed = parse_file($code);
    $writer = new AstWriter($entry['fileId'], $code);
    $writer->file($parsed['ast'] ?? []);
    $meta['syntax'][$entry['fileId']] = $parsed['syntax'];
    $out = ['schemaVersion' => SCHEMA_VERSION, 'fileId' => $entry['fileId'], 'path' => $entry['path'],
            'sha256' => hash('sha256', $code), 'bytes' => strlen($code),
            'parseOk' => !$parsed['errors'] && $parsed['ast'] !== null, 'parseErrors' => $parsed['errors'],
            'rootId' => $writer->nodes[0]['id'], 'nodeCount' => count($writer->nodes), 'nodes' => $writer->nodes];
    $json = json_encode(sorted_keys($out), $flags, 100000);
    if ($json === false) {
        fwrite(STDERR, $entry['path'] . ': json: ' . json_last_error_msg() . "\n");
        exit(1);
    }
    file_put_contents($entry['out'], $json . "\n");
}
file_put_contents($manifest['meta'], json_encode($meta, $flags) . "\n");
