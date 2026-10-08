<?php
/**
 * Parse Hestia object records as data, without executing shell expressions.
 * Accept quoted/unquoted values, concatenated quotes, and backslash escapes.
 * Single-quoted content is literal; reserved shell/config keys are filtered.
 */
declare(strict_types=1);

function parse_hestia_object(string $unparsed, array $reserved, array $exceptions): array {
	$result = [];
	while ($unparsed !== "") {
		$unparsed = ltrim($unparsed);
		if ($unparsed === "") {
			break;
		}

		$key_name_extracted = preg_match("/^([a-zA-Z][a-zA-Z0-9_]*)=/", $unparsed, $m);

		if ($key_name_extracted !== 1) {
			// example: `eval(code)=123`
			throw new \InvalidArgumentException(
				"Invalid key value format. Could not extract key name from: " . $unparsed,
			);
		}

		$key_name = $m[1];
		$unparsed = substr($unparsed, strlen($m[0]));
		$skip_reserved =
			!in_array($key_name, $exceptions, true) &&
			(in_array($key_name, $reserved, true) || strpos($key_name, "BASH_FUNC_") === 0);

		$key_value = "";
		$is_in_quote = false;

		while (true) {
			if ($is_in_quote) {
				// Inside single quotes, only a single quote is special.
				$pos = strpos($unparsed, "'");

				if ($pos === false) {
					// example: `KEY='value` (missing closing quote)
					throw new \InvalidArgumentException(
						"Invalid key value format. No closing quote for key: " .
							$key_name .
							" in: " .
							$unparsed,
					);
				}

				$key_value .= substr($unparsed, 0, $pos);
				$unparsed = substr($unparsed, $pos + 1);
				$is_in_quote = false;

				if ($unparsed === "") {
					// parsing complete
					break;
				}
				continue;
			}

			// Outside single quotes, whitespace, single quote, and backslash are special.
			$match = preg_match('/\s|\'|\\\\/u', $unparsed, $m, PREG_OFFSET_CAPTURE);
			if ($match !== 1) {
				// No more special chars; rest of string is the value.
				$key_value .= $unparsed;
				$unparsed = "";
				break;
			}

			$matched_char = $m[0][0];
			$pos = $m[0][1];

			// Add everything before the matched special character.
			$key_value .= substr($unparsed, 0, $pos);
			$unparsed = substr($unparsed, $pos + strlen($matched_char));

			if ($matched_char === "'") {
				if ($unparsed === "") {
					// example: `KEY='` - missing closing quote. nearest legal alternative is `KEY=''`
					throw new \InvalidArgumentException(
						"Invalid key value format. No closing quote for key: " . $key_name,
					);
				}
				$is_in_quote = true;
				continue;
			}
			if ($matched_char === "\\") {
				if ($unparsed === "") {
					// example: `KEY=foo\`
					throw new \InvalidArgumentException(
						"Invalid key value format. Escape character cannot be the last character in value for key: " .
							$key_name .
							" in: " .
							$unparsed,
					);
				}

				// Backslash escapes the next character verbatim
				$next_char = mb_substr($unparsed, 0, 1, "UTF-8"); // remember multi-byte unicode support, æøåÆØÅ
				$key_value .= $next_char;
				$unparsed = substr($unparsed, strlen($next_char));
				continue;
			}

			// Matched whitespace: end of this key-value pair.
			$unparsed = ltrim($unparsed);
			break;
		}
		if ($skip_reserved) {
			continue;
		}
		if (array_key_exists($key_name, $result)) {
			$msg = "Warning: Duplicate key name: " . $key_name . ". ";

			if ($result[$key_name] === $key_value) {
				$msg .= "value is identical.";
			} else {
				$msg .= var_export(
					[
						"old_value" => $result[$key_name],
						"new_value" => $key_value,
					],
					true,
				);
			}
			fwrite(STDERR, $msg . PHP_EOL);
		}
		$result[$key_name] = $key_value;
	}
	return $result;
}
