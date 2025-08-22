<?php
// Set headers for CORS and JSON response
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
$socket_path = '/var/run/anus_service_cmd.sock';

// --- Database Setup & Helper Functions ---
function get_db_connection() {
    global $db_path;
    try {
        $db = new PDO("sqlite:{$db_path}");
        $db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        // Create event_log table if it doesn't exist
        $db->exec("CREATE TABLE IF NOT EXISTS event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL,
            startTime INTEGER NOT NULL,
            endTime INTEGER
        )");
        $db->exec("CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )");
        return $db;
    } catch (PDOException $e) {
        http_response_code(500);
        echo json_encode(['error' => 'Database connection failed: ' . $e->getMessage()]);
        exit;
    }
}

function get_settings($db) {
    $settings = [
        'onlineDetectionMethod' => 'smart_check',
        'smartCheckThreshold' => 3,
        'criticalServices' => 'Gateway,OpenDNS Primary,Google DNS,Cloudflare DNS'
    ];
    try {
        $stmt = $db->query("SELECT key, value FROM settings");
        while ($row = $stmt->fetch(PDO::FETCH_ASSOC)) {
            $settings[$row['key']] = $row['value'];
        }
    } catch (PDOException $e) {
        error_log('Error fetching settings: ' . $e->getMessage());
        // Fallback to default settings
    }
    return $settings;
}

function get_data_from_socket($command) {
    global $socket_path;
    $client = @stream_socket_client("unix://{$socket_path}", $errno, $errstr, 5);
    if (!$client) {
        // Don't throw an exception, just return an error state
        return ['error' => "Socket connection failed: {$errstr} ({$errno})"];
    }
    fwrite($client, $command);
    $response = '';
    while (!feof($client)) {
        $response .= fread($client, 8192);
    }
    fclose($client);
    $decoded_response = json_decode($response, true);
    if (json_last_error() !== JSON_ERROR_NONE) {
        return ['error' => "Invalid JSON response from service: " . $response];
    }
    return $decoded_response;
}

function get_gateway_details_from_db($db) {
    $stmt_gateway = $db->query("SELECT gateway_ip, gateway_mac, gateway_vendor FROM local_network_info ORDER BY last_updated DESC LIMIT 1");
    return $stmt_gateway->fetch(PDO::FETCH_ASSOC) ?: ['ip' => 'N/A', 'mac' => 'N/A', 'vendor' => 'N/A'];
}

function calculate_internet_status($latest_metrics, $settings) {
    $method = $settings['onlineDetectionMethod'] ?? 'smart_check';
    $critical_services = array_map('trim', explode(',', $settings['criticalServices'] ?? ''));

    $up_count = 0;
    $down_count = 0;
    $critical_up_count = 0;
    $critical_down_count = 0;
    $gateway_status = 'DOWN';

    foreach ($latest_metrics as $metric) {
        $is_critical = in_array($metric['name'], $critical_services);
        if ($metric['status'] === 'UP') {
            $up_count++;
            if ($is_critical) $critical_up_count++;
        } else {
            $down_count++;
            if ($is_critical) $critical_down_count++;
        }
        if ($metric['name'] === 'Gateway') {
            $gateway_status = $metric['status'];
        }
    }

    switch ($method) {
        case 'gateway':
            return $gateway_status;
        case 'majority':
            return ($up_count >= $down_count) ? 'UP' : 'DOWN';
        case 'critical_services':
            return ($critical_down_count === 0 && count($critical_services) > 0) ? 'UP' : 'DOWN';
        case 'smart_check':
        default:
            if ($gateway_status === 'DOWN') return 'DOWN';
            $smart_threshold = (int)($settings['smartCheckThreshold'] ?? 3);
            if ($critical_down_count >= $smart_threshold) return 'DOWN';
            return 'UP';
    }
}

function update_event_log($db, $current_status) {
    $stmt = $db->query("SELECT id, status FROM event_log ORDER BY startTime DESC LIMIT 1");
    $last_event = $stmt->fetch(PDO::FETCH_ASSOC);

    if (!$last_event) {
        // First run, log startup status
        $insert_stmt = $db->prepare("INSERT INTO event_log (status, startTime) VALUES (?, ?)");
        $insert_stmt->execute([$current_status, time()]);
        return;
    }

    if ($last_event['status'] !== $current_status) {
        // Status has changed, end the last event and start a new one
        $update_stmt = $db->prepare("UPDATE event_log SET endTime = ? WHERE id = ?");
        $update_stmt->execute([time(), $last_event['id']]);

        $insert_stmt = $db->prepare("INSERT INTO event_log (status, startTime) VALUES (?, ?)");
        $insert_stmt->execute([$current_status, time()]);
    }
}

function format_long_duration($seconds) {
    if ($seconds <= 0) return "0 Seconds";
    $days = floor($seconds / 86400);
    $seconds %= 86400;
    $hours = floor($seconds / 3600);
    $seconds %= 3600;
    $minutes = floor($seconds / 60);
    $seconds = floor($seconds % 60);

    $parts = [];
    if ($days > 0) $parts[] = $days . " Day" . ($days > 1 ? "s" : "");
    if ($hours > 0) $parts[] = $hours . " Hour" . ($hours > 1 ? "s" : "");
    if ($minutes > 0) $parts[] = $minutes . " Minute" . ($minutes > 1 ? "s" : "");
    if ($seconds > 0) $parts[] = $seconds . " Second" . ($seconds > 1 ? "s" : "");

    return implode(", ", $parts);
}


// --- API Endpoint Logic ---
$db = get_db_connection();
$input = file_get_contents('php://input');
$data = json_decode($input, true);
$action = $data['action'] ?? $_GET['action'] ?? '';

$client_ip = $_SERVER['REMOTE_ADDR'] ?? null;
if ($client_ip && !in_array($action, ['get_targets', 'save_targets'])) {
    @file_put_contents($client_ip_file, $client_ip);
}

switch ($action) {
    case 'get_all_latest_metrics':
        try {
            // Fetch latest metrics
            $stmt = $db->query("SELECT name, ping, jitter, status, dns_info, traceroute_info, timestamp, packet_loss, min_ping_15m, max_ping_15m, packet_loss_15m FROM metrics WHERE is_on_demand = 0 GROUP BY name");
            $latest_metrics = $stmt->fetchAll(PDO::FETCH_ASSOC);

            // Fetch settings
            $settings = get_settings($db);

            // Calculate overall status
            $overall_status = calculate_internet_status($latest_metrics, $settings);
            
            // Update the event log with the new status
            update_event_log($db, $overall_status);
            
            // Check for current packet loss
            $is_packet_loss_happening = false;
            $packet_loss_details = [];
            foreach ($latest_metrics as $metric) {
                if (isset($metric['packet_loss']) && $metric['packet_loss'] > 0) {
                    $is_packet_loss_happening = true;
                    $packet_loss_details[] = ['name' => $metric['name'], 'loss' => $metric['packet_loss']];
                }
            }

            // Fetch other details
            $stmt_resource = $db->query("SELECT cpu_usage, mem_usage, net_down_kbps, net_up_kbps FROM resource_metrics ORDER BY timestamp DESC LIMIT 1");
            $resource_usage = $stmt_resource->fetch(PDO::FETCH_ASSOC) ?: ['cpu_usage' => 0, 'mem_usage' => 0, 'net_down_kbps' => 0, 'net_up_kbps' => 0];
            $gateway_details = get_gateway_details_from_db($db);
            
            // Get status start time from the latest event log entry
            $stmt_log_start = $db->query("SELECT startTime FROM event_log ORDER BY startTime DESC LIMIT 1");
            $status_start_time = $stmt_log_start->fetchColumn() ?: time();
            
            // Get SSL cert details
            $ssl_cert_details = null;
            $ssl_info_file = '/var/db/anus_ssl_info.json';
            if (file_exists($ssl_info_file)) {
                $ssl_cert_details = json_decode(file_get_contents($ssl_info_file), true);
            }


            $server_status = [
                'service_status' => trim(@shell_exec('systemctl is-active anus_service.service')) === 'active',
                'apache_status' => true, 'php_fpm_status' => true, 'db_status' => true,
                'server_ip' => $_SERVER['SERVER_ADDR'] ?? '127.0.0.1',
                'client_ip' => $client_ip,
                'server_gateway_ip' => $gateway_details['ip'],
                'gateway_details' => $gateway_details,
                'resource_usage' => $resource_usage,
                'overall_status' => $overall_status,
                'status_start_time' => date('c', $status_start_time),
                'is_packet_loss_happening' => $is_packet_loss_happening,
                'packet_loss_details' => $packet_loss_details,
                'ssl_cert_details' => $ssl_cert_details
            ];

            foreach ($latest_metrics as &$metric_data) {
                $metric_data['dns_info'] = json_decode($metric_data['dns_info'], true);
                $metric_data['traceroute_info'] = json_decode($metric_data['traceroute_info'], true);
            }
            unset($metric_data);

            echo json_encode(['pings' => $latest_metrics, 'server_status' => $server_status]);
        } catch (Exception $e) {
            http_response_code(500);
            echo json_encode(['error' => 'Error fetching latest metrics: ' . $e->getMessage()]);
        }
        break;

    case 'get_uptime_stats':
        try {
            $now = time();
            $periods = [
                '15m' => $now - 900,
                '30m' => $now - 1800,
                '1h' => $now - 3600,
                '6h' => $now - 21600,
                '24h' => $now - 86400,
                '7d' => $now - 604800,
                '30d' => $now - 2592000
            ];
            $stats = [];

            foreach ($periods as $label => $start_time) {
                $stmt = $db->prepare("SELECT status, startTime, endTime FROM event_log WHERE endTime >= ? OR (endTime IS NULL AND startTime >= ?)");
                $stmt->execute([$start_time, $start_time]);
                $events = $stmt->fetchAll(PDO::FETCH_ASSOC);

                $total_downtime = 0;
                foreach ($events as $event) {
                    if ($event['status'] === 'DOWN') {
                        $event_start = max($event['startTime'], $start_time);
                        $event_end = $event['endTime'] ? min($event['endTime'], $now) : $now;
                        $total_downtime += ($event_end - $event_start);
                    }
                }
                $total_period_seconds = $now - $start_time;
                $uptime_percentage = (($total_period_seconds - $total_downtime) / $total_period_seconds) * 100;
                $stats[$label] = [
                    'uptime_percentage' => round($uptime_percentage, 2),
                    'total_downtime_seconds' => $total_downtime
                ];
            }
            
            // Longest continuous status
            $stmt_longest_online = $db->query("SELECT MAX(endTime - startTime) FROM event_log WHERE status = 'UP' AND endTime IS NOT NULL");
            $longest_online = $stmt_longest_online->fetchColumn() ?: 0;
            
            $stmt_longest_offline = $db->query("SELECT MAX(endTime - startTime) FROM event_log WHERE status = 'DOWN' AND endTime IS NOT NULL");
            $longest_offline = $stmt_longest_offline->fetchColumn() ?: 0;

            $stats['longest_online_formatted'] = format_long_duration($longest_online);
            $stats['longest_offline_formatted'] = format_long_duration($longest_offline);

            echo json_encode($stats);

        } catch (Exception $e) {
            http_response_code(500);
            echo json_encode(['error' => 'Error fetching uptime stats: ' . $e->getMessage()]);
        }
        break;

    case 'on_demand_ping':
        try {
            $command = json_encode(['action' => 'on_demand_ping', 'target' => $data['target'], 'count' => $data['count'], 'size' => $data['size']]);
            $response = get_data_from_socket($command);
            echo json_encode($response);
        } catch (Exception $e) {
            http_response_code(500);
            echo json_encode(['error' => 'Error communicating with service: ' . $e->getMessage()]);
        }
        break;

    case 'get_network_neighborhood':
        try {
            $stmt = $db->query("SELECT ip, mac_address, vendor, hostname, is_up, services, os FROM nmap_scan_results ORDER BY is_up DESC, ip ASC");
            $hosts = $stmt->fetchAll(PDO::FETCH_ASSOC);
            foreach ($hosts as &$host) {
                $host['is_up'] = (bool)$host['is_up'];
                $host['services'] = json_decode($host['services'], true);
            }
            $server_ip = $_SERVER['SERVER_ADDR'] ?? '127.0.0.1';
            $client_ip_from_file = trim(@file_get_contents($client_ip_file));
            
            echo json_encode([
                'hosts' => $hosts,
                'server_ip' => $server_ip,
                'client_ip' => $client_ip_from_file,
                'gateway_ip' => get_gateway_details_from_db($db)['ip']
            ]);
        } catch (Exception $e) {
            http_response_code(500);
            echo json_encode(['error' => 'Error fetching network neighborhood: ' . $e->getMessage()]);
        }
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
        
    case 'get_recent_history':
        $target_name = $data['target'] ?? $_GET['target'] ?? null;
        if (!$target_name) { http_response_code(400); echo json_encode(['error' => 'Target name not provided.']); break; }
        $stmt = $db->prepare("SELECT timestamp, ping FROM (SELECT timestamp, ping FROM metrics WHERE name = ? AND is_on_demand = 0 ORDER BY timestamp DESC LIMIT 30) sub ORDER BY timestamp ASC");
        $stmt->execute([$target_name]);
        echo json_encode($stmt->fetchAll(PDO::FETCH_ASSOC));
        break;

    case 'get_resource_history':
        $stmt = $db->query("SELECT timestamp, cpu_usage, mem_usage, net_down_kbps, net_up_kbps FROM resource_metrics ORDER BY timestamp ASC");
        echo json_encode($stmt->fetchAll(PDO::FETCH_ASSOC));
        break;

    case 'get_targets':
        if (file_exists($targets_config_file)) { echo file_get_contents($targets_config_file); } 
        else { http_response_code(404); echo json_encode(['error' => 'Targets file not found.']); }
        break;

    case 'save_targets':
        if (isset($data['targets'])) {
            $decoded = json_decode($data['targets']);
            if (json_last_error() === JSON_ERROR_NONE && is_array($decoded)) {
                file_put_contents($targets_config_file, json_encode($decoded, JSON_PRETTY_PRINT));
                echo json_encode(['status' => 'success']);
            } else { http_response_code(400); echo json_encode(['error' => 'Invalid JSON format for targets.']); }
        } else { http_response_code(400); echo json_encode(['error' => 'No targets data provided.']); }
        break;

    case 'pong':
        echo json_encode(['status' => 'success']);
        break;

    case 'get_historical_data':
        try {
            $start = $data['start'] ?? time() - 86400;
            $end = $data['end'] ?? time();
            $stmt = $db->prepare("SELECT name, ping, jitter, status, timestamp FROM metrics WHERE timestamp >= ? AND timestamp <= ? AND is_on_demand = 0 ORDER BY timestamp ASC");
            $stmt->execute([$start, $end]);
            $results = $stmt->fetchAll(PDO::FETCH_ASSOC | PDO::FETCH_GROUP);
            echo json_encode($results);
        } catch (Exception $e) { 
            http_response_code(500); 
            echo json_encode(['error' => 'Error fetching historical data: ' . $e->getMessage()]); 
        }
        break;

    case 'get_log':
        try {
            $stmt = $db->query("SELECT status, startTime, endTime FROM event_log ORDER BY startTime DESC");
            $events = $stmt->fetchAll(PDO::FETCH_ASSOC);
            foreach ($events as &$event) {
                $event['startTime'] = date('c', $event['startTime']);
                if ($event['endTime']) {
                    $event['endTime'] = date('c', $event['endTime']);
                } else {
                    $event['endTime'] = date('c', time());
                }
            }
            echo json_encode($events);
        } catch (Exception $e) { 
            http_response_code(500); 
            echo json_encode(['error' => 'Error generating log: ' . $e->getMessage()]); 
        }
        break;

    case 'clear_log':
        try {
            $db->exec("DELETE FROM metrics"); 
            $db->exec("DELETE FROM client_pings"); 
            $db->exec("DELETE FROM resource_metrics");
            $db->exec("DELETE FROM event_log");
            echo json_encode(['status' => 'success, all metrics cleared.']);
        } catch (Exception $e) { 
            http_response_code(500); 
            echo json_encode(['error' => 'Error clearing log: ' . $e->getMessage()]); 
        }
        break;
        
    case 'get_settings':
        try {
            echo json_encode(get_settings($db));
        } catch (Exception $e) {
            http_response_code(500);
            echo json_encode(['error' => 'Error fetching settings: ' . $e->getMessage()]);
        }
        break;

    case 'save_settings':
        try {
            if (isset($data['settings']) && is_array($data['settings'])) {
                $db->beginTransaction();
                $stmt = $db->prepare("REPLACE INTO settings (key, value) VALUES (?, ?)");
                foreach ($data['settings'] as $key => $value) {
                    $stmt->execute([$key, $value]);
                }
                $db->commit();
                echo json_encode(['status' => 'success']);
            } else {
                http_response_code(400);
                echo json_encode(['error' => 'Invalid or missing settings data.']);
            }
        } catch (Exception $e) { 
            if ($db->inTransaction()) {
                $db->rollBack();
            }
            http_response_code(500); 
            echo json_encode(['error' => 'Error saving settings: ' . $e->getMessage()]); 
        }
        break;

    default:
        http_response_code(400);
        echo json_encode(['error' => 'Invalid action specified.']);
        break;
}
?>