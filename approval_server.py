#!/usr/bin/env python3
"""
Approval Server for snarling - Receives approval requests and forwards responses.
Runs on port 5001.
"""

from flask import Flask, request, jsonify
import requests
import threading
import time

app = Flask(__name__)

# Store pending approval requests
# Format: {request_id: {timestamp, request_data}}
pending_requests = {}

# Callback URL for snarling display to signal when alert is shown
SNARLING_DISPLAY_URL = "http://localhost:5000/approval/alert"

# Lock for thread-safe access to pending_requests
request_lock = threading.Lock()


def cleanup_old_requests():
    """Remove requests older than 5 minutes"""
    current_time = time.time()
    with request_lock:
        expired = [
            req_id for req_id, data in pending_requests.items()
            if current_time - data.get('timestamp', 0) > 300
        ]
        for req_id in expired:
            del pending_requests[req_id]
            print(f"[approval_server] Cleaned up expired request: {req_id}")


@app.route('/approval/request', methods=['POST'])
def approval_request():
    """
    Receive approval request from OpenClaw.
    Stores request locally and signals snarling display to show alert.
    
    Expected JSON:
    {
        "request_id": "uuid",
        "message": "Description of action requiring approval",
        "callback_url": "http://callback/to/notify/openclaw",
        "timeout_seconds": 300
    }
    """
    data = request.json
    
    if not data:
        return jsonify({"error": "No JSON data provided"}), 400
    
    request_id = data.get('request_id')
    callback_url = data.get('callback_url')
    message = data.get('message', 'Approval required')
    
    if not request_id or not callback_url:
        return jsonify({"error": "Missing request_id or callback_url"}), 400
    
    # Store request with timestamp
    with request_lock:
        pending_requests[request_id] = {
            'timestamp': time.time(),
            'request_data': data
        }
    
    print(f"[approval_server] Received approval request: {request_id}")
    print(f"[approval_server] Message: {message}")
    
    # Signal snarling display to show alert
    # Try to notify the display service
    try:
        display_payload = {
            'request_id': request_id,
            'message': message,
            'state': 'awaiting_approval'
        }
        # Fire-and-forget notification to snarling display
        threading.Thread(
            target=lambda: requests.post(
                SNARLING_DISPLAY_URL,
                json=display_payload,
                timeout=2
            ),
            daemon=True
        ).start()
    except Exception as e:
        print(f"[approval_server] Could not signal display: {e}")
    
    # Clean up old requests periodically
    threading.Thread(target=cleanup_old_requests, daemon=True).start()
    
    return jsonify({
        "status": "displayed",
        "request_id": request_id
    })


@app.route('/approval/response', methods=['POST'])
def approval_response():
    """
    Receive approval response from snarling display.
    Forwards the decision to the original callback_url.
    
    Expected JSON:
    {
        "request_id": "uuid",
        "approved": true/false,
        "approved_by": "user"  # optional
    }
    """
    data = request.json
    
    if not data:
        return jsonify({"error": "No JSON data provided"}), 400
    
    request_id = data.get('request_id')
    approved = data.get('approved')
    
    if request_id is None or approved is None:
        return jsonify({"error": "Missing request_id or approved"}), 400
    
    # Get the original request
    with request_lock:
        stored = pending_requests.get(request_id)
    
    if not stored:
        return jsonify({"error": "Request not found or expired"}), 404
    
    callback_url = stored['request_data'].get('callback_url')
    
    print(f"[approval_server] Approval response for {request_id}: {'APPROVED' if approved else 'REJECTED'}")
    
    # Forward decision to original callback
    try:
        forward_payload = {
            "request_id": request_id,
            "approved": approved,
            "timestamp": time.time()
        }
        
        response = requests.post(
            callback_url,
            json=forward_payload,
            timeout=10
        )
        
        # Remove from pending after forwarding
        with request_lock:
            pending_requests.pop(request_id, None)
        
        print(f"[approval_server] Forwarded to {callback_url}, status: {response.status_code}")
        
        return jsonify({
            "status": "forwarded",
            "request_id": request_id,
            "callback_status": response.status_code
        })
        
    except Exception as e:
        print(f"[approval_server] Failed to forward: {e}")
        # Keep request in pending in case we need to retry
        return jsonify({
            "status": "failed_to_forward",
            "error": str(e)
        }), 500


@app.route('/approval/pending', methods=['GET'])
def get_pending():
    """Get list of pending approval requests (for debugging)"""
    with request_lock:
        return jsonify({
            "pending_count": len(pending_requests),
            "requests": [
                {
                    "request_id": req_id,
                    "message": data['request_data'].get('message', 'N/A'),
                    "age_seconds": int(time.time() - data['timestamp'])
                }
                for req_id, data in pending_requests.items()
            ]
        })


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy"})


if __name__ == '__main__':
    print("[approval_server] Starting snarling approval server on port 5001")
    print("[approval_server] Endpoints:")
    print("  POST /approval/request  - Receive approval requests")
    print("  POST /approval/response - Receive approval responses")
    print("  GET  /approval/pending  - List pending requests")
    print("  GET  /health            - Health check")
    print()
    app.run(host='0.0.0.0', port=5001)