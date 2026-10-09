<?php
use function Hestiacp\quoteshellarg\quoteshellarg;

$TAB = "MAIL";
include $_SERVER["DOCUMENT_ROOT"] . "/inc/main.php";

// Global mail settings must never be exposed to a domain owner or impersonated user.
if ($_SESSION["userContext"] !== "admin" || !empty($_SESSION["look"])) {
	header("Location: /list/mail/");
	exit();
}

$v_dnsbl_supported = in_array($_SESSION["MAIL_SYSTEM"] ?? "", ["exim", "exim4"], true);
$v_dnsbl_host = "";
$v_dnsbl_hosts = [];
$v_dnsbl_loaded = false;
$v_mail_settings = null;
$v_validity_supported = ($_SESSION["ANTISPAM_SYSTEM"] ?? "") === "spamd";

if ($_SERVER["REQUEST_METHOD"] === "POST") {
	verify_csrf($_POST);
	$action = $_POST["action"] ?? "";
	$host = $_POST["host"] ?? "";
	if ($read_only === true) {
		$_SESSION["error_msg"] = _("This account is read-only.");
	} elseif ($action === "notifications") {
		$email = $_POST["notification_email"] ?? "";
		if (
			!$v_dnsbl_supported ||
			!is_string($email) ||
			!filter_var($email, FILTER_VALIDATE_EMAIL)
		) {
			$_SESSION["error_msg"] = _("Invalid notification email or unsupported mail service.");
		} else {
			exec(
				HESTIA_CMD . "v-change-sys-mail-security notifications " . quoteshellarg($email),
				$output,
				$return_var,
			);
			check_return_code($return_var, $output);
			unset($output);
		}
	} elseif ($action === "validity-disable") {
		if (!$v_validity_supported) {
			$_SESSION["error_msg"] = _("Validity management requires spamd.");
		} else {
			exec(HESTIA_CMD . "v-change-sys-mail-security validity-disable", $output, $return_var);
			check_return_code($return_var, $output);
			unset($output);
		}
	} elseif (!$v_dnsbl_supported) {
		$_SESSION["error_msg"] = _("DNSBL management requires Exim.");
	} elseif (
		!is_string($action) ||
		!in_array($action, ["add", "delete"], true) ||
		!is_string($host) ||
		!preg_match("/\A[a-zA-Z0-9.-]+(?:!=[0-9.,]+)?\z/", trim($host))
	) {
		$_SESSION["error_msg"] = _("Invalid DNSBL entry.");
	} else {
		$host = trim($host);
		$v_dnsbl_host = $action === "add" ? $host : "";
		$command = $action === "add" ? "v-add-sys-mail-dnsbl" : "v-delete-sys-mail-dnsbl";
		// The existing CLI validates, persists and audits changes. Use its reload mode.
		exec(HESTIA_CMD . $command . " " . quoteshellarg($host) . " no", $output, $return_var);
		check_return_code($return_var, $output);
		unset($output);
	}
	if (empty($_SESSION["error_msg"])) {
		$_SESSION["ok_msg"] = _("Changes have been saved.");
		header("Location: /list/mail/security/");
		exit();
	}
}

if ($v_dnsbl_supported || $v_validity_supported) {
	exec(HESTIA_CMD . "v-list-sys-mail-security json", $output, $return_var);
	check_return_code($return_var, $output);
	if ($return_var === 0) {
		$settings = json_decode(implode("\n", $output), true);
		if (
			is_array($settings) &&
			is_array($settings["notifications"] ?? null) &&
			in_array(
				$settings["notifications"]["state"] ?? "",
				["configured", "missing", "unsupported"],
				true,
			) &&
			is_string($settings["notifications"]["recipient"] ?? null) &&
			in_array(
				$settings["validity"] ?? "",
				["disabled", "missing", "custom", "unsupported"],
				true,
			)
		) {
			$v_mail_settings = $settings;
		} else {
			$_SESSION["error_msg"] = _("Unable to read mail security settings.");
		}
	}
	unset($output);
}

if ($v_dnsbl_supported) {
	exec(HESTIA_CMD . "v-list-sys-mail-dnsbl plain", $output, $return_var);
	check_return_code($return_var, $output);
	if ($return_var === 0) {
		$v_dnsbl_loaded = true;
		foreach ($output as $host) {
			$host = trim($host);
			if ($host !== "" && $host[0] !== "#") {
				$v_dnsbl_hosts[] = $host;
			}
		}
	}
	unset($output);
}

render_page($user, $TAB, "list_mail_security");
unset($_SESSION["error_msg"], $_SESSION["ok_msg"]);
