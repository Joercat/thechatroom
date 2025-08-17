import os
import requests
from datetime import datetime
from flask import Flask, jsonify, render_template_string, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from dotenv import load_dotenv

# --- Configuration & Initialization ---
load_dotenv() # Load environment variables from a .env file for local development

app = Flask(__name__)
# It's crucial to use a secret key for session management in production
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a_fallback_dev_secret_key') 
# Use eventlet as the async mode for gunicorn compatibility, which is essential for production
socketio = SocketIO(app, async_mode='eventlet') 

# --- Base44 API Client (A more structured way to handle API calls) ---
class Base44Client:
    BASE_URL = "https://app.base44.com/api/apps/68a2086478d7f7d462e01628/entities/Chat"
    API_KEY = os.environ.get('BASE44_API_KEY') # Use environment variable for security

    def __init__(self):
        if not self.API_KEY:
            raise ValueError("BASE44_API_KEY environment variable not set.")
        self.headers = {'api_key': self.API_KEY, 'Content-Type': 'application/json'}

    def get_messages(self):
        response = requests.get(self.BASE_URL, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def post_message(self, username, message):
        payload = {"username": username, "message": message}
        response = requests.post(self.BASE_URL, headers=self.headers, json=payload)
        response.raise_for_status()
        return response.json()

    def is_username_taken(self, username):
        params = {'filter': f'[["username", "is", "{username}"]]'}
        response = requests.get(self.BASE_URL, headers=self.headers, params=params)
        response.raise_for_status()
        return len(response.json()) > 0

base44 = Base44Client()
connected_users = {} # In-memory store for online users: {sid: username}

# --- Frontend HTML Template ---
# This template is now powered by WebSocket logic on the client-side.
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Advanced Chat Room</title>
    <script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
    <style>
        :root { --main-bg: #1a1a1d; --sidebar-bg: #25252a; --chat-bg: #1f1f23; --text-color: #f5f5f5; --accent-color: #6f2232; --accent-hover: #952d40; --input-bg: #4e4e50; --online-color: #28a745; }
        body { font-family: 'Segoe UI', sans-serif; margin: 0; background-color: var(--main-bg); color: var(--text-color); display: flex; height: 100vh; }
        .app-layout { display: flex; width: 100%; height: 100%; }
        #sidebar { width: 240px; background-color: var(--sidebar-bg); padding: 20px; border-right: 1px solid #333; display: flex; flex-direction: column; }
        #sidebar h3 { margin-top: 0; border-bottom: 2px solid var(--accent-color); padding-bottom: 10px; }
        #user-list { list-style: none; padding: 0; margin: 0; overflow-y: auto; }
        #user-list li { padding: 8px 0; display: flex; align-items: center; }
        #user-list li::before { content: '●'; color: var(--online-color); margin-right: 10px; font-size: 14px; }
        .chat-container { flex-grow: 1; display: flex; flex-direction: column; }
        #messages { flex-grow: 1; overflow-y: auto; padding: 20px; }
        .message { margin-bottom: 15px; max-width: 70%; display: flex; flex-direction: column; }
        .message-info { display: flex; align-items: baseline; margin-bottom: 5px; }
        .message .username { font-weight: bold; }
        .message .timestamp { font-size: 0.75em; color: #999; margin-left: 8px; }
        .message-bubble { padding: 10px 15px; border-radius: 18px; word-wrap: break-word; background-color: #3a3a3c; }
        .my-message { align-self: flex-end; align-items: flex-end; }
        .my-message .message-bubble { background-color: var(--accent-color); }
        .system-message { text-align: center; color: #aaa; font-style: italic; margin: 10px 0; }
        #typing-indicator { height: 20px; padding: 0 20px; font-style: italic; color: #ccc; }
        #message-form { display: flex; padding: 20px; background-color: var(--sidebar-bg); }
        #message-input { flex-grow: 1; border: none; background-color: var(--input-bg); border-radius: 20px; padding: 12px 18px; font-size: 16px; color: var(--text-color); }
        #message-input:focus { outline: none; box-shadow: 0 0 0 2px var(--accent-hover); }
        #message-form button { background-color: var(--accent-color); color: white; border: none; border-radius: 20px; padding: 12px 25px; margin-left: 10px; cursor: pointer; font-size: 16px; transition: background-color 0.2s; }
        #message-form button:hover { background-color: var(--accent-hover); }
        .modal-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); display: flex; justify-content: center; align-items: center; z-index: 1000; }
        .modal-content { background: var(--sidebar-bg); padding: 30px; border-radius: 8px; text-align: center; box-shadow: 0 5px 15px rgba(0,0,0,0.5); }
        .modal-content input { padding: 10px; width: 80%; margin-top: 10px; border-radius: 5px; border: 1px solid #ccc; background: var(--input-bg); color: var(--text-color); border: none; }
        .modal-content button { margin-top: 20px; padding: 10px 20px; border-radius: 5px; border: none; background-color: var(--accent-color); color: white; cursor: pointer; }
        #error-message { color: #e57373; margin-top: 10px; display: none; }
    </style>
</head>
<body>
    <div id="username-modal" class="modal-overlay">
        <div class="modal-content">
            <h2>Choose your identity</h2>
            <input type="text" id="username-input" placeholder="Enter your username" required>
            <button id="username-submit">Enter Chat</button>
            <p id="error-message">This username is already taken.</p>
        </div>
    </div>
    <div class="app-layout" style="display: none;">
        <aside id="sidebar">
            <h3>Online Users</h3>
            <ul id="user-list"></ul>
        </aside>
        <main class="chat-container">
            <div id="messages"></div>
            <div id="typing-indicator"></div>
            <form id="message-form">
                <input type="text" id="message-input" placeholder="Type a message..." autocomplete="off" required>
                <button type="submit">Send</button>
            </form>
        </main>
    </div>
    <script>
        document.addEventListener('DOMContentLoaded', () => {
            const socket = io();
            const messagesDiv = document.getElementById('messages');
            const messageForm = document.getElementById('message-form');
            const messageInput = document.getElementById('message-input');
            const userList = document.getElementById('user-list');
            const typingIndicator = document.getElementById('typing-indicator');
            const usernameModal = document.getElementById('username-modal');
            const usernameInput = document.getElementById('username-input');
            const usernameSubmit = document.getElementById('username-submit');
            const errorMessage = document.getElementById('error-message');
            const appLayout = document.querySelector('.app-layout');
            let username = localStorage.getItem('chat_username');
            let typingTimeout;

            // --- Username Handling ---
            const setupUsername = () => {
                if (username) {
                    socket.emit('register_user', { username });
                } else {
                    usernameModal.style.display = 'flex';
                }
            };
            
            usernameInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') usernameSubmit.click();
            });

            usernameSubmit.addEventListener('click', async () => {
                const desiredUsername = usernameInput.value.trim();
                if (!desiredUsername) return;

                const response = await fetch(`/api/check_username?username=${desiredUsername}`);
                const data = await response.json();
                
                if (data.is_taken) {
                    errorMessage.style.display = 'block';
                } else {
                    username = desiredUsername;
                    localStorage.setItem('chat_username', username);
                    socket.emit('register_user', { username });
                }
            });

            // --- Socket Event Listeners ---
            socket.on('registration_success', (data) => {
                usernameModal.style.display = 'none';
                appLayout.style.display = 'flex';
                loadInitialMessages(data.history);
            });

            socket.on('update_user_list', (users) => {
                userList.innerHTML = '';
                users.forEach(user => {
                    const li = document.createElement('li');
                    li.textContent = user;
                    userList.appendChild(li);
                });
            });

            socket.on('receive_message', (data) => {
                addMessage(data, data.type);
            });
            
            socket.on('show_typing', (data) => {
                typingIndicator.textContent = `${data.username} is typing...`;
            });

            socket.on('hide_typing', () => {
                typingIndicator.textContent = '';
            });

            // --- Event Emitters ---
            messageForm.addEventListener('submit', (e) => {
                e.preventDefault();
                const messageText = messageInput.value.trim();
                if (messageText) {
                    socket.emit('new_message', { message: messageText });
                    messageInput.value = '';
                    socket.emit('stop_typing');
                }
            });

            messageInput.addEventListener('keyup', () => {
                clearTimeout(typingTimeout);
                socket.emit('typing');
                typingTimeout = setTimeout(() => {
                    socket.emit('stop_typing');
                }, 2000); // User is considered "stopped" after 2 seconds
            });
            
            // --- Helper Functions ---
            const formatTimestamp = (isoString) => {
                return new Date(isoString).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            };
            
            const addMessage = (data, type = 'user') => {
                const messageElement = document.createElement('div');
                if (type === 'system') {
                    messageElement.className = 'system-message';
                    messageElement.textContent = data.message;
                } else {
                    messageElement.classList.add('message');
                    if (data.username === username) {
                        messageElement.classList.add('my-message');
                    }
                    messageElement.innerHTML = `
                        <div class="message-info">
                            <span class="username">${data.username}</span>
                            <span class="timestamp">${formatTimestamp(data.timestamp)}</span>
                        </div>
                        <div class="message-bubble">${data.message}</div>`;
                }
                messagesDiv.appendChild(messageElement);
                messagesDiv.scrollTop = messagesDiv.scrollHeight;
            };

            const loadInitialMessages = (history) => {
                messagesDiv.innerHTML = '';
                history.sort((a, b) => new Date(a.createdAt) - new Date(b.createdAt));
                history.forEach(msg => addMessage({
                    ...msg,
                    timestamp: msg.createdAt // Map createdAt to timestamp for consistency
                }));
            };

            // --- Initial Load ---
            setupUsername();
        });
    </script>
</body>
</html>
"""

# --- HTTP Routes ---
@app.route('/')
def index():
    """Serves the main application page."""
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/check_username', methods=['GET'])
def check_username():
    """REST endpoint to check if a username is available before establishing WebSocket connection."""
    username_to_check = request.args.get('username')
    if not username_to_check:
        return jsonify({"error": "Username parameter is required"}), 400
    try {
        is_taken = base44.is_username_taken(username_to_check)
        return jsonify({"is_taken": is_taken})
    except requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 500

# --- WebSocket Event Handlers ---
@socketio.on('connect')
def handle_connect():
    """A new client has connected."""
    print(f"Client connected: {request.sid}")

@socketio.on('register_user')
def handle_user_registration(data):
    """When a user provides their username."""
    username = data['username']
    connected_users[request.sid] = username
    
    # Broadcast to all clients that a user has joined
    emit('receive_message', {'message': f'{username} has joined the chat.', 'type': 'system'}, broadcast=True)
    
    # Send updated user list to all clients
    emit('update_user_list', list(connected_users.values()), broadcast=True)
    
    # Send chat history to the newly connected user
    try {
        history = base44.get_messages()
        emit('registration_success', {'history': history})
    } except requests.RequestException as e:
        print(f"Error fetching history: {e}")
        emit('registration_success', {'history': []}) # Send empty history on error


@socketio.on('new_message')
def handle_new_message(data):
    """A client sends a new message."""
    username = connected_users.get(request.sid)
    if username:
        message_text = data['message']
        try:
            # Persist the message to the Base44 backend
            base44.post_message(username, message_text)
            
            # Broadcast the new message to all connected clients
            emit('receive_message', {
                'username': username,
                'message': message_text,
                'timestamp': datetime.utcnow().isoformat() + 'Z' # UTC timestamp
            }, broadcast=True)
        except requests.exceptions.RequestException as e:
            print(f"Error posting message to Base44: {e}")
            # Optionally, inform the user of the error
            emit('receive_message', {'message': 'Error: Could not send message.', 'type': 'system'})


@socketio.on('typing')
def handle_typing():
    """A client is typing."""
    username = connected_users.get(request.sid)
    if username:
        emit('show_typing', {'username': username}, broadcast=True, include_self=False)

@socketio.on('stop_typing')
def handle_stop_typing():
    """A client has stopped typing."""
    emit('hide_typing', broadcast=True, include_self=False)

@socketio.on('disconnect')
def handle_disconnect():
    """A client has disconnected."""
    username = connected_users.pop(request.sid, None)
    if username:
        # Broadcast that the user has left
        emit('receive_message', {'message': f'{username} has left the chat.', 'type': 'system'}, broadcast=True)
        # Broadcast the updated user list
        emit('update_user_list', list(connected_users.values()), broadcast=True)
    print(f"Client disconnected: {request.sid}")

# --- Main Execution ---
if __name__ == '__main__':
    # This mode is for local development only.
    # In production, Render will use gunicorn to run the 'app' object.
    socketio.run(app, debug=True, port=5001)

