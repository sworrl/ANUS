<?php
// A.N.U.S. v1.4.0 - API Update
header("Access-Control-Allow-Origin: *");
header("Content-Type: application/json; charset=UTF-8");
header("Cache-Control: no-store, no-cache, must-revalidate, max-age=0");
header("Pragma: no-cache");
header("Expires: 0");
set_time_limit(60);

// --- Configuration ---
$db_path = '/var/db/anus_metrics.db';
$client_ip_file = '/var/db/anus_client_ip.txt';
$targets_config_file = '/var/www/html/anus/assets/targets.json';
$ssl_info_file = '/var/www/html/anus/assets/ssl_info.json'; 

// --- Helper Functions ---
function get_db_connection() {
    global $db_path;
    try {
        $db = new PDO("sqlite:{$db_path}", null, null, [PDO::ATTR_TIMEOUT => 10]);
        $db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        return $db;
    } catch (PDOException $e) {
        http_response_code(500);
        echo json_encode(['error' => 'Database connection failed: ' . $e->getMessage()]);
        exit;
    }
}

function get_settings($db) {
    $defaults = [
        'onlineDetectionMethod' => 'smart_check',
        'smartCheckThreshold' => 3,
        'criticalServices' => 'Gateway,NextDNS Primary,Google.com,Cloudflare.com',
        'updateInterval' => '2000'
    ];
    try {
        $stmt = $db->query("SELECT key, value FROM settings");
        while ($row = $stmt->fetch(PDO::FETCH_ASSOC)) {
            $defaults[$row['key']] = $row['value'];
        }
    } catch (PDOException $e) {
        error_log('Error fetching settings: ' . $e->getMessage());
    }
    return $defaults;
}

function calculate_internet_quality_score($latest_metrics) {
    $total_ping = 0; $total_jitter = 0; $total_packet_loss = 0; $ping_count = 0;
    foreach ($latest_metrics as $metric) {
        if ($metric['status'] === 'UP' && $metric['ping'] !== null) {
            $total_ping += $metric['ping'];
            $total_jitter += $metric['jitter'] ?? 0;
            $total_packet_loss += $metric['packet_loss'] ?? 0;
            $ping_count++;
        }
    }
    if ($ping_count === 0) return 0;
    $avg_ping = $total_ping / $ping_count;
    $avg_jitter = $total_jitter / $ping_count;
    $avg_packet_loss = $total_packet_loss / $ping_count;
    $ping_score = max(0, 200 - ($avg_ping * 2));
    $jitter_score = max(0, 150 - ($avg_jitter * 5));
    $loss_score = max(0, 150 - ($avg_packet_loss * 1.5));
    return round($ping_score + $jitter_score + $loss_score);
}

function calculate_internet_status($latest_metrics, $settings) {
    $method = $settings['onlineDetectionMethod'];
    $critical_services = array_map('trim', explode(',', $settings['criticalServices']));
    $up_count = 0; $down_count = 0; $critical_up_count = 0; $critical_down_count = 0; $gateway_status = 'DOWN';
    foreach ($latest_metrics as $metric) {
        $is_critical = in_array($metric['name'], $critical_services);
        if ($metric['status'] === 'UP') { $up_count++; if ($is_critical) $critical_up_count++; } 
        else { $down_count++; if ($is_critical) $critical_down_count++; }
        if ($metric['name'] === 'Gateway') { $gateway_status = $metric['status']; }
    }
    if ($method === 'gateway') return $gateway_status;
    if ($method === 'majority') return ($up_count >= $down_count) ? 'UP' : 'DOWN';
    if ($method === 'critical_services') return ($critical_down_count === 0 && !empty($critical_services)) ? 'UP' : 'DOWN';
    if ($gateway_status === 'DOWN') return 'DOWN';
    return ($critical_down_count >= (int)$settings['smartCheckThreshold']) ? 'DOWN' : 'UP';
}

function format_long_duration($seconds) {
    if ($seconds <= 0) return "0 Seconds";
    $time_units = [['d', 86400], ['h', 3600], ['m', 60], ['s', 1]];
    $parts = [];
    foreach ($time_units as [$unit, $value]) {
        if ($seconds >= $value) {
            $amount = floor($seconds / $value);
            $parts[] = "{$amount}{$unit}";
            $seconds %= $value;
        }
    }
    return implode(' ', array_slice($parts, 0, 2));
}

function get_ssl_cert_details_from_file($cert_path) {
    if (!file_exists($cert_path)) return null;
    try {
        $cert_content = file_get_contents($cert_path);
        $cert_info = openssl_x509_parse($cert_content);
        if ($cert_info === false) return null;
        
        $end_date = date('Y-m-d H:i:s', $cert_info['validTo_time_t']);
        $issuer = $cert_info['issuer']['CN'] ?? 'Unknown';
        
        openssl_x509_export($cert_content, $cert_out);
        $fingerprint = openssl_digest($cert_out, 'sha256');

        return ['issuer' => $issuer, 'end_date' => $end_date, 'fingerprint' => $fingerprint];
    } catch (Exception $e) {
        error_log("Error reading SSL cert: " . $e->getMessage());
        return null;
    }
}

// --- API Endpoint Logic ---
$db = get_db_connection();
$input = file_get_contents('php://input');
$data = json_decode($input, true);
$action = $data['action'] ?? $_GET['action'] ?? '';

$client_ip = $_SERVER['REMOTE_ADDR'] ?? null;
if ($client_ip) { @file_put_contents($client_ip_file, $client_ip); }

switch ($action) {
    case 'get_all_latest_metrics':
        $stmt = $db->query("SELECT name, ping, jitter, status, dns_info, traceroute_info, timestamp, packet_loss, min_ping_15m, max_ping_15m, packet_loss_15m FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY name ORDER BY timestamp DESC) as rn FROM metrics WHERE is_on_demand = 0) WHERE rn = 1");
        $latest_metrics = $stmt->fetchAll(PDO::FETCH_ASSOC);
        $settings = get_settings($db);
        $overall_status = calculate_internet_status($latest_metrics, $settings);
        
        $is_packet_loss_happening = false;
        $packet_loss_details = [];
        foreach ($latest_metrics as $metric) {
            if (isset($metric['packet_loss']) && $metric['packet_loss'] > 0) {
                $is_packet_loss_happening = true;
                $packet_loss_details[] = ['name' => $metric['name'], 'loss' => $metric['packet_loss']];
            }
        }

        $stmt_resource = $db->query("SELECT cpu_usage, mem_usage, net_down_kbps, net_up_kbps FROM resource_metrics ORDER BY timestamp DESC LIMIT 1");
        $resource_usage = $stmt_resource->fetch(PDO::FETCH_ASSOC) ?: ['cpu_usage' => 0, 'mem_usage' => 0, 'net_down_kbps' => 0, 'net_up_kbps' => 0];
        
        $stmt_gateway_ip = $db->query("SELECT gateway_ip FROM local_network_info LIMIT 1");
        $gateway_ip = $stmt_gateway_ip->fetchColumn();
        $gateway_details_from_scan = ['ip' => $gateway_ip ?: 'N/A', 'mac_address' => 'N/A', 'vendor' => 'N/A'];
        if ($gateway_ip) {
            $stmt_gateway_details = $db->prepare("SELECT ip, mac_address, vendor FROM nmap_scan_results WHERE ip = ?");
            $stmt_gateway_details->execute([$gateway_ip]);
            $gateway_details_from_scan = $stmt_gateway_details->fetch(PDO::FETCH_ASSOC) ?: $gateway_details_from_scan;
        }

        $stmt_log_start = $db->prepare("SELECT startTime FROM event_log WHERE status = ? ORDER BY startTime DESC LIMIT 1");
        $stmt_log_start->execute([$overall_status]);
        $status_start_time = $stmt_log_start->fetchColumn() ?: time();
        
        $ssl_cert_details = null;
        if (file_exists($ssl_info_file)) {
            $ssl_info = json_decode(file_get_contents($ssl_info_file), true);
            if (isset($ssl_info['cert_path'])) {
                 $ssl_cert_details = get_ssl_cert_details_from_file($ssl_info['cert_path']);
            }
        }

        $server_status = [
            'service_status' => trim(@shell_exec('systemctl is-active anus_service.service')) === 'active',
            'apache_status' => true, 'php_fpm_status' => true,
            'server_ip' => $_SERVER['SERVER_ADDR'] ?? '127.0.0.1',
            'client_ip' => $client_ip,
            'server_gateway_ip' => $gateway_details_from_scan['ip'],
            'gateway_details' => $gateway_details_from_scan,
            'resource_usage' => $resource_usage,
            'overall_status' => $overall_status,
            'internet_quality_score' => calculate_internet_quality_score($latest_metrics),
            'status_start_time' => date('c', $status_start_time),
            'is_packet_loss_happening' => $is_packet_loss_happening,
            'packet_loss_details' => $packet_loss_details,
            'ssl_cert_details' => $ssl_cert_details
        ];

        foreach ($latest_metrics as &$metric_data) {
            $metric_data['dns_info'] = json_decode($metric_data['dns_info'], true);
            $metric_data['traceroute_info'] = json_decode($metric_data['traceroute_info'], true);
        }
        echo json_encode(['pings' => $latest_metrics, 'server_status' => $server_status]);
        break;

    case 'get_client_details':
        if (!$client_ip) { echo json_encode(['error' => 'Client IP not found.']); break; }
        $mac_address = 'N/A'; $hostname = 'N/A';
        $arp_output = @shell_exec("ip neigh show " . escapeshellarg($client_ip));
        if ($arp_output && preg_match('/lladdr ([0-9a-f:]+)/', $arp_output, $matches)) { $mac_address = $matches[1]; }
        $nmb_output = @shell_exec("nmblookup -A " . escapeshellarg($client_ip));
        if ($nmb_output && preg_match('/<(20)>/', $nmb_output, $matches, PREG_OFFSET_CAPTURE)) {
             $line = strtok(substr($nmb_output, 0, $matches[0][1]), "\n");
             $hostname = trim(strtok($line, " "));
        }
        echo json_encode(['mac' => $mac_address, 'hostname' => $hostname]);
        break;

    case 'get_uptime_stats':
        $now = time();
        $periods = ['24h' => 86400, '7d' => 604800, '30d' => 2592000];
        $stats = [];
        foreach ($periods as $label => $seconds) {
            $start_time = $now - $seconds;
            $stmt = $db->prepare("SELECT status, startTime, endTime FROM event_log WHERE startTime < ? AND (endTime > ? OR endTime IS NULL)");
            $stmt->execute([$now, $start_time]);
            $events = $stmt->fetchAll(PDO::FETCH_ASSOC);
            $total_downtime = 0;
            foreach ($events as $event) {
                if ($event['status'] === 'DOWN') {
                    $event_start = max($event['startTime'], $start_time);
                    $event_end = $event['endTime'] ? min($event['endTime'], $now) : $now;
                    $total_downtime += ($event_end - $event_start);
                }
            }
            $uptime_percentage = (($seconds - $total_downtime) / $seconds) * 100;
            $stats[$label] = ['uptime_percentage' => round($uptime_percentage, 2), 'total_downtime_seconds' => $total_downtime];
        }
        $stmt_longest_online = $db->query("SELECT MAX(endTime - startTime) FROM event_log WHERE status = 'UP' AND endTime IS NOT NULL");
        $stats['longest_online_formatted'] = format_long_duration($stmt_longest_online->fetchColumn() ?: 0);
        echo json_encode($stats);
        break;

    case 'on_demand_ping':
        $target = $data['target'] ?? ''; $count = $data['count'] ?? 4; $size = $data['size'] ?? 56;
        $ping_cmd = "ping -c " . escapeshellarg($count) . " -s " . escapeshellarg($size) . " " . escapeshellarg($target);
        $ping_output = shell_exec($ping_cmd);
        $dns_output = shell_exec("dig " . escapeshellarg($target) . " +short");
        $trace_output = shell_exec("traceroute -q 1 -w 1 " . escapeshellarg($target));
        echo json_encode(['ping' => $ping_output, 'dns' => explode("\n", trim($dns_output)), 'traceroute' => explode("\n", trim($trace_output))]);
        break;
        
    case 'trigger_network_scan':
        echo json_encode(['status' => 'success', 'message' => 'Scan will run on its next scheduled interval.']);
        break;

    case 'get_network_neighborhood':
        $stmt = $db->query("SELECT ip, mac_address, vendor, hostname, is_up, services, os FROM nmap_scan_results ORDER BY is_up DESC, ip ASC");
        $hosts = $stmt->fetchAll(PDO::FETCH_ASSOC);
        foreach ($hosts as &$host) { $host['is_up'] = (bool)$host['is_up']; $host['services'] = json_decode($host['services'], true); }
        $server_ip = $_SERVER['SERVER_ADDR'] ?? '127.0.0.1';
        $client_ip_from_file = trim(@file_get_contents($client_ip_file));
        $stmt_gateway = $db->query("SELECT gateway_ip FROM local_network_info LIMIT 1");
        $gateway_ip = $stmt_gateway->fetchColumn();
        echo json_encode(['hosts' => $hosts, 'server_ip' => $server_ip, 'client_ip' => $client_ip_from_file, 'gateway_ip' => $gateway_ip]);
        break;
        
    case 'get_recent_history':
        $target_name = $data['target'] ?? null;
        if (!$target_name) { http_response_code(400); echo json_encode(['error' => 'Target not provided.']); break; }
        $stmt = $db->prepare("SELECT timestamp, ping FROM (SELECT timestamp, ping FROM metrics WHERE name = ? AND is_on_demand = 0 ORDER BY timestamp DESC LIMIT 30) sub ORDER BY timestamp ASC");
        $stmt->execute([$target_name]);
        echo json_encode($stmt->fetchAll(PDO::FETCH_ASSOC));
        break;

    case 'get_resource_history':
        $stmt = $db->query("SELECT timestamp, cpu_usage, mem_usage, net_down_kbps, net_up_kbps FROM resource_metrics WHERE timestamp >= strftime('%s', 'now', '-24 hours') ORDER BY timestamp ASC");
        echo json_encode($stmt->fetchAll(PDO::FETCH_ASSOC));
        break;

    case 'get_historical_data':
        $start = $data['start'] ?? time() - 86400; $end = $data['end'] ?? time();
        $stmt = $db->prepare("SELECT name, timestamp, ping FROM metrics WHERE timestamp >= ? AND timestamp <= ? AND is_on_demand = 0 AND ping IS NOT NULL ORDER BY timestamp ASC");
        $stmt->execute([$start, $end]);
        $results = $stmt->fetchAll(PDO::FETCH_ASSOC);
        $grouped = [];
        foreach ($results as $row) { $grouped[$row['name']][] = $row; }
        echo json_encode($grouped);
        break;

    case 'get_log':
        $stmt = $db->query("SELECT status, startTime, endTime FROM event_log ORDER BY startTime DESC");
        $events = $stmt->fetchAll(PDO::FETCH_ASSOC);
        foreach ($events as &$event) {
            $event['startTime'] = date('c', $event['startTime']);
            $event['endTime'] = $event['endTime'] ? date('c', $event['endTime']) : date('c', time());
        }
        echo json_encode($events);
        break;

    case 'clear_log':
        $db->exec("DELETE FROM metrics"); 
        $db->exec("DELETE FROM resource_metrics");
        $db->exec("DELETE FROM event_log");
        $db->exec("INSERT INTO event_log (status, startTime, endTime) VALUES ('Stats Reset', ".time().", ".time().")");
        echo json_encode(['status' => 'success']);
        break;
        
    case 'get_settings':
        echo json_encode(get_settings($db));
        break;

    case 'save_settings':
        if (isset($data['settings']) && is_array($data['settings'])) {
            $db->beginTransaction();
            $stmt = $db->prepare("REPLACE INTO settings (key, value) VALUES (?, ?)");
            foreach ($data['settings'] as $key => $value) {
                if ($value !== null) $stmt->execute([$key, $value]);
            }
            $db->commit();
            echo json_encode(['status' => 'success']);
        } else {
            http_response_code(400);
            echo json_encode(['error' => 'Invalid settings data.']);
        }
        break;
        
    case 'pong':
        echo json_encode(['status' => 'success']);
        break;

    default:
        http_response_code(400);
        echo json_encode(['error' => 'Invalid action specified.']);
        break;
}
?>

