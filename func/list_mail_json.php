<?php
/** Render a mail list using one PHP process, regardless of its record count. */
declare(strict_types=1);

require __DIR__ . "/object_parser.php";

try {
	$fields = match ($argv[1] ?? "") {
		"domains" => [
			"DOMAIN",
			"ANTIVIRUS",
			"ANTISPAM",
			"REJECT",
			"RATE_LIMIT",
			"DKIM",
			"CATCHALL",
			"ACCOUNTS",
			"U_DISK",
			"SSL",
			"SUSPENDED",
			"TIME",
			"DATE",
			"WEBMAIL_ALIAS",
			"WEBMAIL",
		],
		"accounts" => [
			"ACCOUNT",
			"ALIAS",
			"FWD",
			"FWD_ONLY",
			"AUTOREPLY",
			"QUOTA",
			"U_DISK",
			"SUSPENDED",
			"TIME",
			"DATE",
		],
		default => throw new InvalidArgumentException("Invalid mail list type"),
	};
	$reserved = preg_split("/\s+/", trim($argv[3] ?? ""), -1, PREG_SPLIT_NO_EMPTY);
	$exceptions = preg_split("/\s+/", trim($argv[4] ?? ""), -1, PREG_SPLIT_NO_EMPTY);
	$file = @fopen($argv[2], "r");
	if ($file === false) {
		throw new RuntimeException("Unable to open mail records");
	}
	$key = array_shift($fields);
	$records = [];
	while (($line = fgets($file)) !== false) {
		if (trim($line) === "") {
			continue;
		}
		$record = parse_hestia_object(rtrim($line, "\r\n"), $reserved, $exceptions);
		if (!isset($record[$key]) || $record[$key] === "") {
			throw new InvalidArgumentException("Missing mail record identifier");
		}
		$values = [];
		foreach ($fields as $field) {
			$values[$field] = $record[$field] ?? "";
		}
		$records[$record[$key]] = $values;
	}
	if (!feof($file)) {
		throw new RuntimeException("Unable to read mail records");
	}
	fclose($file);
	// Cast explicitly so empty lists and numeric account names remain JSON objects.
	echo json_encode((object) $records, JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR) . PHP_EOL;
} catch (Throwable $error) {
	fwrite(STDERR, $error->getMessage() . PHP_EOL);
	exit(2);
}
