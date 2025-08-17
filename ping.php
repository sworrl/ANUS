<?php
// Set headers for CORS and JSON response
header("Access-Control-Allow-Origin: *");
header("Content-Type: application/json; charset=UTF-8");
// FIX: Add cache-control headers to prevent browser caching of API responses
header("Cache-Control: no-store, no-cache, must-revalidate, max-age=0");
header("Pragma: no-cache");
header("Expires: 0");
set_time_limit(60);

// --- Configuration ---
$db_path = '/var/db/anus_metrics.db';
$client_ip_file = '/var/db/anus_client_ip.txt';
$targets_config_file = '/var/www/html/anus/assets/targets.json';
$fuzzy_sayings_file = '/var/www/html/anus/assets/fuzzy_sayings.json';

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
function get_latest_resource_usage($db) {
    $stmt = $db->query("SELECT cpu_usage, mem_usage, net_down_kbps, net_up_kbps FROM resource_metrics ORDER BY timestamp DESC LIMIT 1");
    $usage = $stmt->fetch(PDO::FETCH_ASSOC);
    return $usage ?: ['cpu_usage' => 0, 'mem_usage' => 0, 'net_down_kbps' => 0, 'net_up_kbps' => 0];
}

function calculate_internet_quality_score($pings) {
    if (empty($pings)) return 0;
    $total_score = 0;
    $target_count = 0;
    foreach ($pings as $ping) {
        if ($ping['name'] === 'Gateway' || $ping['status'] !== 'UP' || $ping['ping'] === null) continue;
        $ping_score = max(0, 100 - ($ping['ping'] / 2));
        $jitter_score = max(0, 100 - ($ping['jitter'] * 2));
        $packet_loss_score = 100 - ($ping['packet_loss'] ?? 0);
        $target_score = ($ping_score * 0.4) + ($jitter_score * 0.2) + ($packet_loss_score * 0.4);
        $total_score += $target_score;
        $target_count++;
    }
    if ($target_count === 0) return 0;
    // Scale score to be out of 500
    return round(($total_score / $target_count) * 5);
}

function get_gateway_details_from_db($db) {
    $stmt = $db->query("SELECT gateway_ip, gateway_mac, gateway_vendor FROM local_network_info ORDER BY last_updated DESC LIMIT 1");
    $result = $stmt->fetch(PDO::FETCH_ASSOC);
    return $result ?: ['ip' => 'N/A', 'mac' => 'N/A', 'vendor' => 'N/A'];
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
            $query = "
                WITH LatestMetrics AS (
                    SELECT
                        m.id, m.name, m.ping, m.jitter, m.status, m.dns_info, m.traceroute_info, m.timestamp, m.packet_loss,
                        ROW_NUMBER() OVER(PARTITION BY name ORDER BY timestamp DESC) as rn
                    FROM metrics m
                )
                SELECT
                    lm.name, lm.ping, lm.jitter, lm.status, lm.dns_info, lm.traceroute_info, lm.timestamp, lm.packet_loss,
                    (SELECT AVG(packet_loss) FROM metrics WHERE name = lm.name AND timestamp >= :time_15m_ago) as packet_loss_15m,
                    (SELECT MIN(ping) FROM metrics WHERE name = lm.name AND timestamp >= :time_15m_ago) as min_ping_15m,
                    (SELECT MAX(ping) FROM metrics WHERE name = lm.name AND timestamp >= :time_15m_ago) as max_ping_15m
                FROM LatestMetrics lm
                WHERE lm.rn = 1;
            ";

            $stmt = $db->prepare($query);
            $stmt->execute([':time_15m_ago' => $time_15m_ago]);
            $latest_metrics = $stmt->fetchAll(PDO::FETCH_ASSOC);

            foreach ($latest_metrics as &$metric_data) {
                $metric_data['dns_info'] = json_decode($metric_data['dns_info'], true);
                $metric_data['traceroute_info'] = json_decode($metric_data['traceroute_info'], true);
                $metric_data['packet_loss_15m'] = ($metric_data['packet_loss_15m'] !== null) ? round($metric_data['packet_loss_15m'], 2) : null;
                $metric_data['min_ping_15m'] = ($metric_data['min_ping_15m'] !== null) ? round($metric_data['min_ping_15m'], 2) : null;
                $metric_data['max_ping_15m'] = ($metric_data['max_ping_15m'] !== null) ? round($metric_data['max_ping_15m'], 2) : null;
            }
            unset($metric_data);

            $server_to_client_ping = null;
            if ($client_ip) {
                $stmt_client_ping = $db->prepare("SELECT ping FROM client_pings WHERE ip = ? LIMIT 1");
                $stmt_client_ping->execute([$client_ip]);
                $result = $stmt_client_ping->fetch(PDO::FETCH_ASSOC);
                if ($result) { $server_to_client_ping = $result['ping']; }
            }
            
            $gateway_details = get_gateway_details_from_db($db);
            $server_ip = $_SERVER['SERVER_ADDR'] ?? '127.0.0.1';

            $server_status = [
                'service_status' => trim(@shell_exec('systemctl is-active anus_service.service')) === 'active',
                'apache_status' => true, 'php_fpm_status' => true, 'db_status' => true,
                'server_ip' => $server_ip,
                'client_ip' => $client_ip,
                'server_gateway_ip' => $gateway_details['ip'],
                'gateway_details' => $gateway_details,
                'server_to_client_ping' => $server_to_client_ping,
                'resource_usage' => get_latest_resource_usage($db),
                'internet_quality_score' => calculate_internet_quality_score($latest_metrics)
            ];
            echo json_encode(['pings' => $latest_metrics, 'server_status' => $server_status]);
        } catch (Exception $e) {
            http_response_code(500);
            echo json_encode(['error' => 'Error fetching latest metrics: ' . $e->getMessage()]);
        }
        break;

    case 'get_network_neighborhood':
        try {
            // FIX: Added 'os' column to the SELECT statement to provide data to the frontend
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
        // FIX: Optimized query to fetch only the last 24 hours of data to prevent timeouts on large datasets.
        try {
            $time_24h_ago = time() - 86400;
            $stmt = $db->prepare("SELECT name, ping, jitter, status, timestamp FROM metrics WHERE timestamp >= ? ORDER BY timestamp ASC");
            $stmt->execute([$time_24h_ago]);
            $results = $stmt->fetchAll(PDO::FETCH_ASSOC | PDO::FETCH_GROUP);
            echo json_encode($results);
        } catch (Exception $e) { 
            http_response_code(500); 
            echo json_encode(['error' => 'Error fetching historical data: ' . $e->getMessage()]); 
        }
        break;

    case 'get_log':
        // FIX: The log generation logic was flawed and didn't correctly use the last known status.
        // This new logic correctly reconstructs the event timeline from the raw ping data.
        try {
            $stmt = $db->prepare("SELECT status, timestamp FROM metrics WHERE name = 'Google.com' ORDER BY timestamp ASC");
            $stmt->execute();
            $pings = $stmt->fetchAll(PDO::FETCH_ASSOC);
            $event_log = [];
            if (count($pings) > 0) {
                $last_status = $pings[0]['status'];
                $event_start_time = $pings[0]['timestamp'];
                
                for ($i = 1; $i < count($pings); $i++) {
                    if ($pings[$i]['status'] !== $last_status) {
                        $event_log[] = [
                            'status' => $last_status,
                            'startTime' => date('c', $event_start_time),
                            'endTime' => date('c', $pings[$i]['timestamp'])
                        ];
                        $last_status = $pings[$i]['status'];
                        $event_start_time = $pings[$i]['timestamp'];
                    }
                }
                // Add the final, ongoing event
                $event_log[] = [
                    'status' => $last_status,
                    'startTime' => date('c', $event_start_time),
                    'endTime' => date('c', time())
                ];
            }
            echo json_encode(array_reverse($event_log));
        } catch (Exception $e) { http_response_code(500); echo json_encode(['error' => 'Error generating log: ' . $e->getMessage()]); }
        break;

    case 'clear_log':
        try {
            $db->exec("DELETE FROM metrics"); $db->exec("DELETE FROM client_pings"); $db->exec("DELETE FROM resource_metrics");
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
