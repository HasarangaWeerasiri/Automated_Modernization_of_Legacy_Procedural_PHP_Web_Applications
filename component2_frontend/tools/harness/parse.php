<?php
/**
 * Component 2 whole-app test harness, PHP side: parse only, never execute.
 *
 * Reads a manifest (JSON list of {"source": <php file>, "out": <json file>}) and, for each file,
 * writes nikic/php-parser's AST as JSON. Three attributes are added from the token stream, because
 * the timeline needs exact source positions that the AST alone does not give:
 *
 *   parenFirstToken / parenStartPos / parenEndPos
 *       on if, elseif, while, do, for, foreach, switch: the first significant token inside the
 *       statement's parentheses (the condition's node id) and the byte range between them
 *       (condExpr / iterExpr). The AST drops redundant parentheses; the tokens do not.
 *   spanEndPos
 *       on echo: the statement's last byte. Its ";" is included; a closing tag is not.
 *
 * Usage: php parse.php <manifest.json>
 */

require __DIR__ . '/vendor/autoload.php';

use PhpParser\ErrorHandler\Collecting;
use PhpParser\Node;
use PhpParser\NodeTraverser;
use PhpParser\NodeVisitorAbstract;
use PhpParser\ParserFactory;
use PhpParser\PhpVersion;

const PARSER_PACKAGE = 'nikic/php-parser';

function is_ignorable($token): bool
{
    return in_array($token->id, [T_WHITESPACE, T_COMMENT, T_DOC_COMMENT], true);
}

final class Positions extends NodeVisitorAbstract
{
    /** @var \PhpParser\Token[] */
    private array $tokens;

    public function __construct(array $tokens)
    {
        $this->tokens = $tokens;
    }

    public function enterNode(Node $node)
    {
        if ($node instanceof Node\Stmt\If_ || $node instanceof Node\Stmt\ElseIf_ || $node instanceof Node\Stmt\While_
            || $node instanceof Node\Stmt\For_ || $node instanceof Node\Stmt\Foreach_
            || $node instanceof Node\Stmt\Switch_) {
            $open = $this->firstOpenParen($node->getStartTokenPos() + 1);
            if ($open !== null) {
                $this->setParens($node, $open, $this->matchForward($open));
            }
        } elseif ($node instanceof Node\Stmt\Do_) {
            $close = $this->lastCloseParen($node->getEndTokenPos());
            if ($close !== null) {
                $this->setParens($node, $this->matchBackward($close), $close);
            }
        } elseif ($node instanceof Node\Stmt\Echo_) {
            $node->setAttribute('spanEndPos', $this->echoEnd($node->getStartTokenPos()));
        }
        return null;
    }

    private function setParens(Node $node, ?int $open, ?int $close): void
    {
        if ($open === null || $close === null) {
            return;
        }
        $first = $open + 1;
        while ($first < $close && is_ignorable($this->tokens[$first])) {
            $first++;
        }
        $node->setAttribute('parenFirstToken', $first);
        $node->setAttribute('parenStartPos', $this->tokens[$open]->pos + 1);
        $node->setAttribute('parenEndPos', $this->tokens[$close]->pos);
    }

    private function firstOpenParen(int $from): ?int
    {
        for ($i = $from; $i < count($this->tokens); $i++) {
            if ($this->tokens[$i]->text === '(') {
                return $i;
            }
            if (!is_ignorable($this->tokens[$i])) {
                return null;
            }
        }
        return null;
    }

    private function lastCloseParen(int $from): ?int
    {
        for ($i = $from; $i >= 0; $i--) {
            if ($this->tokens[$i]->text === ')') {
                return $i;
            }
        }
        return null;
    }

    private function matchForward(int $open): ?int
    {
        $depth = 0;
        for ($i = $open; $i < count($this->tokens); $i++) {
            if ($this->tokens[$i]->text === '(') {
                $depth++;
            } elseif ($this->tokens[$i]->text === ')' && --$depth === 0) {
                return $i;
            }
        }
        return null;
    }

    private function matchBackward(int $close): ?int
    {
        $depth = 0;
        for ($i = $close; $i >= 0; $i--) {
            if ($this->tokens[$i]->text === ')') {
                $depth++;
            } elseif ($this->tokens[$i]->text === '(' && --$depth === 0) {
                return $i;
            }
        }
        return null;
    }

    /** The echo statement's last byte: up to its ";" (included), or the last token before a closing tag. */
    private function echoEnd(int $start): int
    {
        $depth = 0;
        $count = count($this->tokens);
        for ($j = $start + 1; $j < $count; $j++) {
            $token = $this->tokens[$j];
            if ($depth === 0 && ($token->id === T_CLOSE_TAG || $token->text === ';')) {
                break;
            }
            if ($token->text === '(' || $token->text === '[') {
                $depth++;
            } elseif ($token->text === ')' || $token->text === ']') {
                $depth--;
            }
        }
        $last = $j;
        if ($j >= $count || $this->tokens[$j]->text !== ';') {
            $last = min($j, $count) - 1;
            while ($last > $start && (is_ignorable($this->tokens[$last]) || $this->tokens[$last]->text === "\0")) {
                $last--;
            }
        }
        return $this->tokens[$last]->pos + strlen($this->tokens[$last]->text) - 1;
    }
}

function parse_file(string $code): array
{
    $factory = new ParserFactory();
    $attempts = ['newest' => fn() => $factory->createForNewestSupportedVersion(),
                 '5.6' => fn() => $factory->createForVersion(PhpVersion::fromString('5.6'))];
    $result = null;
    foreach ($attempts as $label => $make) {
        $parser = $make();
        $errors = new Collecting();
        $ast = $parser->parse($code, $errors);
        $result = ['syntax' => $label, 'ast' => $ast, 'tokens' => $parser->getTokens(),
                   'errors' => array_map(fn($e) => $e->getRawMessage() . ' on line ' . $e->getStartLine(),
                                         $errors->getErrors())];
        if (!$result['errors'] && $ast !== null) {
            break;
        }
    }
    return $result;
}

$manifest = json_decode(file_get_contents($argv[1]), true);
$version = \Composer\InstalledVersions::getPrettyVersion(PARSER_PACKAGE);
foreach ($manifest as $entry) {
    $code = file_get_contents($entry['source']);
    $parsed = parse_file($code);
    $ast = $parsed['ast'] ?? [];
    if ($ast) {
        $traverser = new NodeTraverser(new Positions($parsed['tokens']));
        $ast = $traverser->traverse($ast);
    }
    $out = ['parser' => PARSER_PACKAGE . ' ' . $version, 'php' => PHP_VERSION, 'syntax' => $parsed['syntax'],
            'errors' => $parsed['errors'], 'ast' => $ast];
    $json = json_encode($out, JSON_INVALID_UTF8_SUBSTITUTE | JSON_PARTIAL_OUTPUT_ON_ERROR, 100000);
    if ($json === false) {
        $json = json_encode(['parser' => $out['parser'], 'php' => PHP_VERSION, 'syntax' => $parsed['syntax'],
                             'errors' => ['json: ' . json_last_error_msg()], 'ast' => []]);
    }
    file_put_contents($entry['out'], $json);
}
