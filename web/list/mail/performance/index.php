<?php
use function Hestiacp\quoteshellarg\quoteshellarg;
$TAB = "MAIL";
include $_SERVER["DOCUMENT_ROOT"] . "/inc/main.php";
if ($_SESSION["userContext"] !== "admin" || !empty($_SESSION["look"])) {
	header("Location: /list/mail/");
	exit();
}
$period = $_GET["period"] ?? "24";
if (!is_string($period) || !in_array($period, ["1", "24", "168"], true)) {
	$period = "24";
}
if ($_SERVER["REQUEST_METHOD"] === "POST") {
	verify_csrf($_POST);
	$email = $_POST["recipient"] ?? "";
	$numbers = [];
	foreach (
		["queue_threshold" => [1, 10000], "consecutive" => [2, 30], "cooldown_minutes" => [5, 1440]]
		as $key => $range
	) {
		$value = $_POST[$key] ?? null;
		$numbers[$key] = is_string($value)
			? filter_var($value, FILTER_VALIDATE_INT, [
				"options" => ["min_range" => $range[0], "max_range" => $range[1]],
			])
			: false;
	}
	$enabled = $_POST["enabled"] ?? "no";
	if ($read_only === true) {
		$_SESSION["error_msg"] = _("This account is read-only.");
	} elseif (
		!is_string($email) ||
		!filter_var($email, FILTER_VALIDATE_EMAIL) ||
		!is_string($enabled) ||
		!in_array($enabled, ["yes", "no"], true) ||
		in_array(false, $numbers, true)
	) {
		$_SESSION["error_msg"] = _("Invalid monitoring settings.");
	} else {
		$args = array_merge([$enabled, $email], array_values($numbers));
		exec(
			HESTIA_CMD .
				"v-change-sys-webmail-monitor " .
				implode(" ", array_map(static fn($value) => quoteshellarg((string) $value), $args)),
			$output,
			$return_var,
		);
		check_return_code($return_var, $output);
		unset($output);
		if (empty($_SESSION["error_msg"])) {
			$_SESSION["ok_msg"] = _("Changes have been saved.");
			header("Location: /list/mail/performance/");
			exit();
		}
	}
}
$monitor = null;
exec(HESTIA_CMD . "v-list-sys-webmail-monitor " . quoteshellarg($period), $output, $return_var);
check_return_code($return_var, $output);
if ($return_var === 0) {
	$monitor_data = json_decode(implode("\n", $output), true);
	if (
		is_array($monitor_data) &&
		is_array($monitor_data["config"] ?? null) &&
		is_array($monitor_data["state"] ?? null) &&
		is_array($monitor_data["history"] ?? null) &&
		is_int($monitor_data["now"] ?? null)
	) {
		$monitor = $monitor_data;
	} else {
		$_SESSION["error_msg"] = _("Unable to read webmail monitoring.");
	}
}
render_page($user, $TAB, "list_mail_performance");
unset($_SESSION["error_msg"], $_SESSION["ok_msg"]);
