<?php
use function Hestiacp\quoteshellarg\quoteshellarg;
$TAB = "BACKUP";
include $_SERVER["DOCUMENT_ROOT"] . "/inc/main.php";
if ($_SESSION["userContext"] !== "admin" || !empty($_SESSION["look"])) {
	header("Location: /list/backup/");
	exit();
}
$selected_backup_user = $_GET["backup_user"] ?? "";
if (!is_string($selected_backup_user)) {
	$selected_backup_user = "";
	$_SESSION["error_msg"] = _("Backup user not found.");
}
if ($_SERVER["REQUEST_METHOD"] === "POST") {
	verify_csrf($_POST);
	$target = $_POST["backup_user"] ?? "";
	$selected_backup_user = is_string($target) ? $target : "";
	$frequency = $_POST["frequency"] ?? "";
	$numbers = [];
	foreach (
		["hour" => [0, 23], "minute" => [0, 59], "weekday" => [0, 6], "day" => [1, 28]]
		as $key => $range
	) {
		$value = $_POST[$key] ?? null;
		$numbers[$key] = is_string($value)
			? filter_var($value, FILTER_VALIDATE_INT, [
				"options" => ["min_range" => $range[0], "max_range" => $range[1]],
			])
			: false;
	}
	if ($read_only === true) {
		$_SESSION["error_msg"] = _("This account is read-only.");
	} elseif (
		!is_string($target) ||
		!preg_match("/\A[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\z/", $target) ||
		!is_string($frequency) ||
		!in_array($frequency, ["manual", "daily", "weekly", "monthly"], true) ||
		in_array(false, $numbers, true)
	) {
		$_SESSION["error_msg"] = _("Invalid backup schedule.");
	} else {
		$args = array_merge([$target, $frequency], array_values($numbers));
		exec(
			HESTIA_CMD .
				"v-change-sys-backup-schedule " .
				implode(" ", array_map(static fn($value) => quoteshellarg((string) $value), $args)),
			$output,
			$return_var,
		);
		check_return_code($return_var, $output);
		unset($output);
		if (empty($_SESSION["error_msg"])) {
			$_SESSION["ok_msg"] = _("Changes have been saved.");
			header(
				"Location: /list/backup/schedule/?" . http_build_query(["backup_user" => $target]),
			);
			exit();
		}
	}
}
$schedules = null;
exec(HESTIA_CMD . "v-list-sys-backup-schedules", $output, $return_var);
check_return_code($return_var, $output);
if ($return_var === 0) {
	$schedule_data = json_decode(implode("\n", $output), true);
	if (
		is_array($schedule_data) &&
		is_array($schedule_data["users"] ?? null) &&
		is_bool($schedule_data["supported"] ?? null)
	) {
		$schedules = $schedule_data;
	} else {
		$_SESSION["error_msg"] = _("Unable to read backup schedules.");
	}
}
$backup_account = null;
if ($schedules !== null && $selected_backup_user !== "") {
	foreach ($schedules["users"] as $candidate_account) {
		if ($candidate_account["user"] === $selected_backup_user) {
			$backup_account = $candidate_account;
			break;
		}
	}
	if ($backup_account === null) {
		$_SESSION["error_msg"] = _("Backup user not found.");
	}
}
render_page($user, $TAB, "list_backup_schedule");
unset($_SESSION["error_msg"], $_SESSION["ok_msg"]);
