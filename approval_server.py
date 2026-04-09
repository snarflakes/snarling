#!/usr/bin/env python3
"""
Approval Server for Snarling Display
Receives approval requests and forwards responses back to OpenClaw
"""

import time
import threading
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# Store pending approvals
pending_requests = {}
request_lock = threading.Lock()

# OpenClaw Gateway settings
OPENCLAW_GATEWAY_URL = "http://localhost:18789"
OPENCLAW_GATEWAY_TOKEN = "c1e2798a58fcf2414a4602f743a193838f6e4416eb5a61ed"
TARGET_SESSION_KEY = "agent:main:main"  # The main session to notify


@app.route('/approval/request', methods=['POST'])
def approval_request():
    """
    Receive approval request from OpenClaw plugin.
    Forwards to snarling display on port 5000.
    
    Expected JSON:
    {
        "request_id": "uuid",
        "message": "Description of action needing approval",
        "callback_url": "http://...",
        "timeout_seconds": 7200
    }
    """
    data = request.json
    
    if not data:
        return jsonify({"error": "No JSON data provided"}), 400
    
    request_id = data.get('request_id')
    message = data.get('message')
    timeout = data.get('timeout_seconds', 7200)
    
    if not request_id or not message:
        return jsonify({"error": "Missing request_id or message"}), 400
    
    # Store the request
    with request_lock:
        pending_requests[request_id] = {
            'request_data': data,
            'timestamp': time.time(),
            'timeout': timeout
        }
    
    # Forward to snarling display
    try:
        alert_payload = {
            "request_id": request_id,
            "message": message,
            "timeout_seconds": timeout
        }
        
        response = requests.post(
            "http://localhost:5000/approval/alert",
            json=alert_payload,
            timeout=5
        )
        
        if response.ok:
            print(f"[approval_server] Forwarded alert to snarling: {request_id}")
            return jsonify({
                "request_id": request_id,
                "status": "displayed"
            })
        else:
            print(f"[approval_server] Snarling alert failed: {response.status_code}")
            return jsonify({
                "request_id": request_id,
                "status": "failed",
                "error": f"Snarling returned {response.status_code}"
            }), 500
            
    except Exception as e:
        print(f"[approval_server] Error forwarding to snarling: {e}")
        return jsonify({
            "request_id": request_id,
            "status": "failed",
            "error": str(e)
        }), 500


def notify_openclaw_session(request_id, approved, message):
    """
    Send a notification to the OpenClaw session using the Tools Invoke API
    """
    try:
        # Call sessions_send via the Tools Invoke API
        tool_payload = {
            "tool": "sessions_send",
            "sessionKey": "main",  # Use main session
            "args": {
                "sessionKey": TARGET_SESSION_KEY,
                "message": f"🚨 **Approval Update**\n\nRequest: {message}\nStatus: {'✅ APPROVED' if approved else '❌ REJECTED'}\nRequest ID: {request_id}"
            }
        }
        
        response = requests.post(
            f"{OPENCLAW_GATEWAY_URL}/tools/invoke",
            json=tool_payload,
            headers={
                "Authorization": f"Bearer {OPENCLAW_GATEWAY_TOKEN}",
                "Content-Type": "application/json"
            },
            timeout=10
        )
        
        if response.ok:
            print(f"[approval_server] Notified OpenClaw session: {request_id} = {approved}")
            return True
        else:
            print(f"[approval_server] Failed to notify OpenClaw: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"[approval_server] Error notifying OpenClaw: {e}")
        return False


@app.route('/approval/response', methods=['POST'])
def approval_response():
    """
    Receive approval response from snarling display.
    Forwards the decision to the original callback_url AND notifies OpenClaw session.
    
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
    message = stored['request_data'].get('message', 'Unknown action')
    
    print(f"[approval_server] Approval response for {request_id}: {'APPROVED' if approved else 'REJECTED'}")
    
    # Notify OpenClaw session directly (this is the key part!)
    notify_openclaw_session(request_id, approved, message)
    
    # Forward decision to original callback (if provided)
    if callback_url and callback_url != f"http://localhost:5001/approval/response":
        try:
            forward_payload = {
                "request_id": request_id,
                "approved": approved,
                "timestamp": time.time()
            }
            
            response = requests.post(
                callback_url,
                json=forward_payload,
                timeout=5
            )
            
            print(f"[approval_server] Forwarded to {callback_url}, status: {response.status_code}")
            
            return jsonify({
                "status": "forwarded",
                "request_id": request_id,
                "callback_status": response.status_code
            })
            
        except Exception as e:
            print(f"[approval_server] Failed to forward: {e}")
            # Still return success since we notified OpenClaw
            return jsonify({
                "status": "notified_only",
                "request_id": request_id,
                "error": str(e)
            })
    else:
        # No callback URL, but we notified OpenClaw
        return jsonify({
            "status": "notified",
            "request_id": request_id
        })


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


@app.route('/approval/status/<request_id>', methods=['GET'])
def get_approval_status(request_id):
    """Get status of a specific approval request"""
    with request_lock:
        stored = pending_requests.get(request_id)
        if stored:
            return jsonify({
                "request_id": request_id,
                "status": "pending",
                "age_seconds": int(time.time() - stored['timestamp']),
                "message": stored['request_data'].get('message', 'N/A')
            })
    
    # Not found in pending - check if it was recently completed
    return jsonify({
        "request_id": request_id,
        "status": "not_found"
    }), 404


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
