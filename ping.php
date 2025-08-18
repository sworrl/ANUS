<?php // A.N.U.S. v1.3.3
// Set headers for CORS and JSON response
header("Access-Control-Allow-Origin: *");
header("Content-Type: application/json; charset=UTF-8");
header("Cache-Control: no-store, no-cache, must-revalidate, max-age=0");
header("Pragma: no-cache");
header("Expires: 0");
set_time_limit(120);

// --- Configuration ---
$db_path = '/var/db/anus_metrics.db';
$client_ip_file = '/var/db/anus_client_ip.txt';
$targets_config_file = '/var/www/html/anus/assets/targets.json';
$socket_path = '/var/tmp/anus_service_cmd.sock';
$ssl_info_file = '/var/www/html/anus/assets/ssl_info.json';

// --- Database Setup ---
try {
    $db = new PDO("sqlite:{$db_path}");
    $db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
} catch (PDOException $e) {
    http_response_code(500);
    echo json_encode(['error' => 'Database connection failed: ' . $e->getMessage()]);
    exit;
}

// --- Helper Functions ---
function get_data_from_socket($command) {
    global $socket_path;
    $client = stream_socket_client("unix://{$socket_path}", $errno, $errstr, 30);
    if (!$client) {
        throw new Exception("Socket connection failed: {$errstr} ({$errno})");
    }
    fwrite($client, $command);
    $response = '';
    while (!feof($client)) {
        $response .= fread($client, 8192);
    }
    fclose($client);
    return json_decode($response, true);
}

function get_gateway_details_from_db($db) {
    $stmt_gateway = $db->query("SELECT gateway_ip, gateway_mac, gateway_vendor FROM local_network_info ORDER BY last_updated DESC LIMIT 1");
    return $stmt_gateway->fetch(PDO::FETCH_ASSOC) ?: ['ip' => 'N/A', 'mac' => 'N/A', 'vendor' => 'N/A'];
}

function get_ssl_cert_details($cert_path) {
    if (!$cert_path || !file_exists($cert_path)) {
        return null;
    }
    $details = [];
    try {
        $cert_content = file_get_contents($cert_path);
        $cert_info = openssl_x509_parse($cert_content);
        
        $details['issuer'] = $cert_info['issuer']['CN'] ?? 'N/A';
        $details['start_date'] = date('Y-m-d H:i:s', $cert_info['validFrom_time_t']);
        $details['end_date'] = date('Y-m-d H:i:s', $cert_info['validTo_time_t']);
        $details['fingerprint'] = openssl_x509_fingerprint($cert_content, "sha256");
    } catch (Exception $e) {
        return null;
    }
    return $details;
}

// --- API Endpoint Logic ---
$input = file_get_contents('php://input');
$data = json_decode($input, true);
$action = $data['action'] ?? $_GET['action'] ?? '';

$client_ip = $_SERVER['REMOTE_ADDR'] ?? null;
if ($client_ip && $action !== 'get_targets' && $action !== 'save_targets') {
    @file_put_contents($client_ip_file, $client_ip);
}

switch ($action) {
    case 'get_all_latest_metrics':
        try {
            $time_15m_ago = time() - 900;
            $stmt = $db->prepare("
                SELECT
                    m.name, m.ping, m.jitter, m.status, m.dns_info, m.traceroute_info, m.timestamp, m.packet_loss,
                    COALESCE(agg.min_ping_15m, 0) as min_ping_15m,
                    COALESCE(agg.max_ping_15m, 0) as max_ping_15m,
                    COALESCE(agg.packet_loss_15m, 0) as packet_loss_15m
                FROM metrics m
                INNER JOIN (
                    SELECT name, MAX(timestamp) AS max_timestamp
                    FROM metrics
                    GROUP BY name
                ) AS latest ON m.name = latest.name AND m.timestamp = latest.max_timestamp
                LEFT JOIN (
                    SELECT
                        name,
                        MIN(ping) as min_ping_15m,
                        MAX(ping) as max_ping_15m,
                        AVG(packet_loss) as packet_loss_15m
                    FROM metrics
                    WHERE timestamp >= ?
                    GROUP BY name
                ) AS agg ON m.name = agg.name
            ");
            $stmt->execute([$time_15m_ago]);
            $latest_metrics = $stmt->fetchAll(PDO::FETCH_ASSOC);


            foreach ($latest_metrics as &$metric_data) {
                $metric_data['dns_info'] = json_decode($metric_data['dns_info'], true);
                $metric_data['traceroute_info'] = json_decode($metric_data['traceroute_info'], true);
                $metric_data['packet_loss_15m'] = round(floatval($metric_data['packet_loss_15m']), 2);
            }
            unset($metric_data);

            $stmt_resource = $db->query("SELECT cpu_usage, mem_usage, net_down_kbps, net_up_kbps FROM resource_metrics ORDER BY timestamp DESC LIMIT 1");
            $resource_usage = $stmt_resource->fetch(PDO::FETCH_ASSOC) ?: ['cpu_usage' => 0, 'mem_usage' => 0, 'net_down_kbps' => 0, 'net_up_kbps' => 0];

            $gateway_details = get_gateway_details_from_db($db);
            
            $stmt_status = $db->query("SELECT status, quality_score, timestamp FROM event_log ORDER BY timestamp DESC LIMIT 1");
            $status_data = $stmt_status->fetch(PDO::FETCH_ASSOC) ?: ['status' => 'DOWN', 'quality_score' => 0, 'timestamp' => time()];
            
            $ssl_cert_details = null;
            if (file_exists($ssl_info_file)) {
                $ssl_config = json_decode(file_get_contents($ssl_info_file), true);
                if (isset($ssl_config['cert_path'])) {
                    $ssl_cert_details = get_ssl_cert_details($ssl_config['cert_path']);
                }
            }


            $server_status = [
                'service_status' => trim(@shell_exec('systemctl is-active anus_service.service')) === 'active',
                'apache_status' => true, 'php_fpm_status' => true, 'db_status' => true,
                'server_ip' => $_SERVER['SERVER_ADDR'] ?? '127.0.0.1',
                'client_ip' => $client_ip,
                'server_gateway_ip' => $gateway_details['ip'],
                'gateway_details' => $gateway_details,
                'resource_usage' => $resource_usage,
                'overall_status' => $status_data['status'],
                'internet_quality_score' => $status_data['quality_score'],
                'status_start_time' => date('c', $status_data['timestamp']),
                'ssl_cert_details' => $ssl_cert_details
            ];
            echo json_encode(['pings' => $latest_metrics, 'server_status' => $server_status]);
        } catch (Exception $e) {
            http_response_code(500);
            echo json_encode(['error' => 'Error fetching latest metrics: ' . $e->getMessage()]);
        }
        break;

    case 'on_demand_diagnostic':
        try {
            $command = json_encode([
                'command' => 'on_demand_diagnostic', 
                'target' => $data['target'],
                'count' => $data['count'],
                'size' => $data['size']
            ]);
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
            if ($hosts === false) {
                $hosts = []; // Ensure hosts is an array even if query fails
            }
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
            // Return a valid structure on error so the frontend doesn't break
            echo json_encode([
                'error' => 'Error fetching network neighborhood: ' . $e->getMessage(),
                'hosts' => [],
                'server_ip' => $_SERVER['SERVER_ADDR'] ?? '127.0.0.1',
                'client_ip' => trim(@file_get_contents($client_ip_file)),
                'gateway_ip' => get_gateway_details_from_db($db)['ip']
            ]);
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
        $stmt = $db->prepare("SELECT timestamp, ping FROM (SELECT timestamp, ping FROM metrics WHERE name = ? ORDER BY timestamp DESC LIMIT 30) sub ORDER BY timestamp ASC");
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
            $start_time = $data['start'] ?? (time() - 86400);
            $end_time = $data['end'] ?? time();
            $range_seconds = $end_time - $start_time;

            // Determine aggregation level based on time range
            if ($range_seconds < 7200) { // Less than 2 hours
                $group_format = "'%Y-%m-%d %H:%M:%S'"; // No aggregation (by second)
            } elseif ($range_seconds < 172800) { // Less than 2 days
                $group_format = "'%Y-%m-%d %H:%M:00'"; // By minute
            } elseif ($range_seconds < 1209600) { // Less than 14 days
                $group_format = "'%Y-%m-%d %H:00:00'"; // By hour
            } else {
                $group_format = "'%Y-%m-%d 00:00:00'"; // By day
            }

            $stmt = $db->prepare("
                SELECT 
                    name,
                    strftime({$group_format}, timestamp, 'unixepoch') as time_bucket,
                    AVG(ping) as ping
                FROM metrics
                WHERE timestamp >= ? AND timestamp <= ?
                GROUP BY name, time_bucket
                ORDER BY time_bucket ASC
            ");
            $stmt->execute([$start_time, $end_time]);
            $results = $stmt->fetchAll(PDO::FETCH_ASSOC);
            
            // Re-group by name for the chart
            $grouped_results = [];
            foreach ($results as $row) {
                $grouped_results[$row['name']][] = [
                    'timestamp' => strtotime($row['time_bucket']),
                    'ping' => $row['ping']
                ];
            }
            
            echo json_encode($grouped_results);
        } catch (Exception $e) { 
            http_response_code(500); 
            echo json_encode(['error' => 'Error fetching historical data: ' . $e->getMessage()]); 
        }
        break;

    case 'get_log':
        try {
            $stmt = $db->query("SELECT status, startTime, endTime FROM event_log ORDER BY startTime DESC");
            echo json_encode($stmt->fetchAll(PDO::FETCH_ASSOC));
        } catch (Exception $e) { http_response_code(500); echo json_encode(['error' => 'Error generating log: ' . $e->getMessage()]); }
        break;

    case 'clear_log':
        try {
            $db->exec("DELETE FROM metrics"); $db->exec("DELETE FROM client_pings"); $db->exec("DELETE FROM resource_metrics"); $db->exec("DELETE FROM event_log");
            echo json_encode(['status' => 'success, all metrics cleared.']);
        } catch (Exception $e) { http_response_code(500); echo json_encode(['error' => 'Error clearing log: ' . $e->getMessage()]); }
        break;
        
    case 'save_settings':
        try {
            if (isset($data['settings'])) {
                foreach ($data['settings'] as $key => $value) {
                    $stmt = $db->prepare("REPLACE INTO settings (key, value) VALUES (?, ?)");
                    $stmt->execute([$key, $value]);
                }
            }
            echo json_encode(['status' => 'success']);
        } catch (Exception $e) { http_response_code(500); echo json_encode(['error' => 'Error saving settings: ' . $e->getMessage()]); }
        break;

    default:
        http_response_code(400);
        echo json_encode(['error' => 'Invalid action specified.']);
        break;
}
?>
