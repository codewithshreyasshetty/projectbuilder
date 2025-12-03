# app.py - Main Streamlit Application
import streamlit as st
import sqlite3
import os
import subprocess
import threading
import time
from groq import Groq
import json
from datetime import datetime
import signal
import sys
from threading import Timer
import time
import webbrowser

# Authentication helpers
from werkzeug.security import generate_password_hash, check_password_hash
import jwt

# Secret for JWT (override with env var in production)
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev_secret_key')

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "<PUT_YOUR_GROQ_API_KEY_HERE>")  # Set via env var or replace the placeholder (do NOT commit secrets)

if 'running_projects' not in st.session_state:
    st.session_state.running_projects = {}  # {project_id: {'port': port, 'process': process}}


# Initialize Groq client
def init_groq():
    # Allow temporary key via session state for convenience (not persisted)
    api_key = None
    if st.session_state.get('GROQ_API_KEY_TEMP'):
        api_key = st.session_state.get('GROQ_API_KEY_TEMP')
    else:
        api_key = GROQ_API_KEY

    if not api_key or api_key.startswith('<'):
        # Return None to indicate no remote AI client available; caller will fall back to local stub
        return None

    try:
        return Groq(api_key=api_key)
    except Exception as e:
        st.warning(f"Failed to initialize Groq client: {e}")
        return None

# Database initialization
def init_database():
    conn = sqlite3.connect('project_builder.db')
    cursor = conn.cursor()
    
    # Projects table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            prompt TEXT NOT NULL,
            backend_code TEXT,
            frontend_code TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'created',
            port INTEGER,
            framework TEXT DEFAULT 'react'
        )
    ''')
    
    # Project runs table for tracking deployments
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS project_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            pid INTEGER,
            port INTEGER,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            stopped_at TIMESTAMP,
            status TEXT DEFAULT 'running',
            FOREIGN KEY (project_id) REFERENCES projects (id)
        )
    ''')
    
    # Template history for learning from past generations
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS generation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            prompt TEXT,
            response TEXT,
            tokens_used INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects (id)
        )
    ''')
    
    conn.commit()
    conn.close()

    # Ensure users table exists
    conn = sqlite3.connect('project_builder.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

    # Ensure projects table has owner_id column for multi-user support
    conn = sqlite3.connect('project_builder.db')
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE projects ADD COLUMN owner_id INTEGER")
    except Exception:
        # Column probably exists or table missing - ignore
        pass
    conn.commit()
    conn.close()

def create_user(email, password):
    conn = sqlite3.connect('project_builder.db')
    cursor = conn.cursor()
    pw_hash = generate_password_hash(password)
    try:
        cursor.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)', (email, pw_hash))
        conn.commit()
        user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        user_id = None
    conn.close()
    return user_id

def get_user_by_email(email):
    conn = sqlite3.connect('project_builder.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, email, password_hash FROM users WHERE email = ?', (email,))
    user = cursor.fetchone()
    conn.close()
    return user

def authenticate_user(email, password):
    user = get_user_by_email(email)
    if not user:
        return None
    uid, uemail, pw_hash = user
    if check_password_hash(pw_hash, password):
        return {'id': uid, 'email': uemail}
    return None

def generate_jwt_token(user_id, email):
    payload = {
        'user_id': user_id,
        'email': email,
        'iat': int(time.time())
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm='HS256')
    return token

def verify_jwt_token(token):
    try:
        data = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        return data
    except Exception:
        return None

# Generate project code using Groq AI
def generate_project_code(client, prompt, framework='react'):
    system_prompt = f"""You are an expert full-stack developer. Generate complete, production-ready code for the requested application.

Requirements:
1. Backend: Flask Python - single file (app.py)
   - Include all necessary routes with TODO comments for database integration
   - Add CORS support for frontend
   - Include error handling and logging
   - Add placeholder routes for CRUD operations, with placeholder data
   - Use proper Flask best practices
   - It is only provides rest api endpoints

2. Frontend: Single index.html file with vanilla js
- Tailwind CSS has to be used, through script tag
- can use ajax if needed
- Responsive design
- Modern UI components
- API integration ready
- Include loading states and error handling
- USE RELATIVE URLs for API calls (e.g., '/todos' not 'http://localhost:5000/todos')
- Add this at top of scripts: const API_BASE_URL = window.location.origin;
- Use fetch(`${{API_BASE_URL}}/endpoint`) for all API calls

3. Return ONLY valid JSON with this structure:
{{
    "project_name": "descriptive-project-name",
    "description": "brief description",
    "backend": "complete Flask app.py code",
    "frontend": "complete index.html code",
    "setup_instructions": "how to run",
    "features": ["feature1", "feature2"]
}}

Make the code production-ready, well-commented, and easy to extend."""

    # If no client is provided (no API key), use a deterministic local stub so UI can be tested offline
    if client is None:
        # Create a simple stubbed project
        stub_backend = '''from flask import Flask, jsonify
app = Flask(__name__)

@app.route('/health')
def health():
    return jsonify({'status':'ok'})

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000)
'''
        stub_frontend = '''<!doctype html>
<html>
<head><meta charset="utf-8"><title>Stub App</title></head>
<body>
<h1>Stub Project</h1>
<p>This is an offline stub project generated for testing.</p>
</body>
</html>'''
        result = {
            'project_name': 'stub-project',
            'description': 'Offline stub project for testing (no AI key)',
            'backend': stub_backend,
            'frontend': stub_frontend,
            'setup_instructions': 'Run `python app.py` in the project folder',
            'features': ['stub-backend', 'stub-frontend']
        }
        return result, 0

    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.7,
            max_tokens=7000
        )

        response_text = chat_completion.choices[0].message.content
        cleaned = response_text.strip()
        try:
            result = json.loads(cleaned)
            tokens_used = getattr(getattr(chat_completion, "usage", None), "total_tokens", 0)
            return result, tokens_used
        except json.JSONDecodeError:
            pass

        import re
        
        # Find the main JSON structure
        match = re.search(r'\{[\s\S]*\}', cleaned)
        if match:
            cleaned = match.group(0)
        
        
        # Method 2: Manual extraction (more reliable for code fields)
        try:
            # Extract fields manually with regex
            project_name_match = re.search(r'"project_name"\s*:\s*"([^"]+)"', cleaned)
            description_match = re.search(r'"description"\s*:\s*"([^"]+)"', cleaned)
            
            backend_match = re.search(r'"backend"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,', cleaned, re.DOTALL)
            frontend_match = re.search(r'"frontend"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,', cleaned, re.DOTALL)
            setup_match = re.search(r'"setup_instructions"\s*:\s*"([^"]*)"', cleaned)
            features_match = re.search(r'"features"\s*:\s*\[(.*?)\]', cleaned, re.DOTALL)
            
            # Build result manually
            result = {}
            
            if project_name_match:
                result["project_name"] = project_name_match.group(1)
            if description_match:
                result["description"] = description_match.group(1)
            if backend_match:
                # Decode escape sequences
                backend_raw = backend_match.group(1)
                backend_code = backend_raw.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"')
                result["backend"] = backend_code
            if frontend_match:
                frontend_raw = frontend_match.group(1)
                frontend_code = frontend_raw.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"')
                result["frontend"] = frontend_code
            if setup_match:
                result["setup_instructions"] = setup_match.group(1)
            if features_match:
                features_str = features_match.group(1)
                features = re.findall(r'"([^"]+)"', features_str)
                result["features"] = features
            
            if len(result) >= 4:  # At least name, description, backend, frontend
                tokens_used = getattr(getattr(chat_completion, "usage", None), "total_tokens", 0)
                st.success("✅ Successfully extracted project data using manual parsing")
                return result, tokens_used
        except Exception as manual_error:
            st.error(f"Manual extraction failed: {manual_error}")
        
        # If all else fails, show error
        st.error("❌ Failed to parse AI response after multiple attempts")
        with st.expander("🔍 View Raw Response (first 2000 chars)"):
            st.code(response_text[:2000], language="text")
        return None, 0

    except Exception as e:
        st.error(f"❌ Error generating code: {type(e).__name__}: {e}")
        if "response_text" in locals():
            with st.expander("🔍 View Response"):
                st.code(response_text[:2000], language="text")
        return None, 0

# Save project to database
def save_project(name, description, prompt, backend, frontend, framework='react', owner_id=None):
    conn = sqlite3.connect('project_builder.db')
    cursor = conn.cursor()
    if owner_id:
        cursor.execute('''
            INSERT INTO projects (name, description, prompt, backend_code, frontend_code, framework, owner_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (name, description, prompt, backend, frontend, framework, owner_id))
    else:
        cursor.execute('''
            INSERT INTO projects (name, description, prompt, backend_code, frontend_code, framework)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (name, description, prompt, backend, frontend, framework))
    project_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return project_id

# Get all projects
def get_all_projects():
    conn = sqlite3.connect('project_builder.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM projects ORDER BY created_at DESC')
    projects = cursor.fetchall()
    conn.close()
    return projects

# Get project by ID
def get_project(project_id):
    conn = sqlite3.connect('project_builder.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM projects WHERE id = ?', (project_id,))
    project = cursor.fetchone()
    conn.close()
    return project

# Delete project
def delete_project(project_id):
    conn = sqlite3.connect('project_builder.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM projects WHERE id = ?', (project_id,))
    cursor.execute('DELETE FROM project_runs WHERE project_id = ?', (project_id,))
    cursor.execute('DELETE FROM generation_history WHERE project_id = ?', (project_id,))
    conn.commit()
    conn.close()

# Update project
def update_project(project_id, backend_code, frontend_code):
    conn = sqlite3.connect('project_builder.db')
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE projects 
        SET backend_code = ?, frontend_code = ?, last_modified = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (backend_code, frontend_code, project_id))
    conn.commit()
    conn.close()

# Find available port
def find_available_port(start_port=5000):
    import socket
    port = start_port
    while port < 65535:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('localhost', port)) != 0:
                return port
        port += 1
    return None

# Run project
# Run project
def run_project(project_id):
    project = get_project(project_id)
    if not project:
        return None, "Project not found"
    
    # Create project directory
    project_dir = f"./projects/project_{project_id}"
    os.makedirs(project_dir, exist_ok=True)
    
    # Find available port
    port = find_available_port()
    if not port:
        return None, "No available ports"
    
    # Modify Flask code to use specific port and serve index.html
    backend_code = project[4]
    
    # Add route to serve index.html if not present
    if "send_from_directory" not in backend_code:
        if "from flask import" in backend_code:
            backend_code = backend_code.replace(
                "from flask import",
                "from flask import send_from_directory,"
            )
        else:
            backend_code = "from flask import send_from_directory\n" + backend_code
    
    # Add index route
    if "@app.route('/')" not in backend_code:
        index_route = """
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')
"""
        # Insert before if __name__
        if "if __name__" in backend_code:
            backend_code = backend_code.replace("if __name__", index_route + "\nif __name__")
        else:
            backend_code += "\n" + index_route
    
    # Modify app.run
    if "app.run(" in backend_code:
        backend_code = backend_code.replace("app.run(debug=True)", f"app.run(debug=True, port={port}, host='127.0.0.1')")
        backend_code = backend_code.replace("app.run()", f"app.run(debug=True, port={port}, host='127.0.0.1')")
    else:
        backend_code += f"\n\nif __name__ == '__main__':\n    app.run(debug=True, port={port}, host='127.0.0.1')"
    
    # Write backend code
    backend_path = os.path.join(project_dir, "app.py")
    with open(backend_path, 'w', encoding='utf-8') as f:
        f.write(backend_code)
    
    # Write frontend code
    frontend_code = project[5]
    frontend_path = os.path.join(project_dir, "index.html")
    frontend_code = frontend_code.replace('localhost:5000', f'localhost:{port}')
    frontend_code = frontend_code.replace('127.0.0.1:5000', f'127.0.0.1:{port}')
    frontend_code = frontend_code.replace("localhost:5000", f"localhost:{port}")  # Handle both quote types
    frontend_code = frontend_code.replace("127.0.0.1:5000", f"127.0.0.1:{port}")

    # Also handle relative URLs like '/todos' -> need to update API base
    # Add base URL replacement for fetch calls
    if "fetch('/" in frontend_code or 'fetch("/' in frontend_code:
        # Inject port info at the top of script tags
        if "<script" in frontend_code:
            frontend_code = frontend_code.replace(
                "<script",
                f"<script>\nconst API_BASE_URL = 'http://localhost:{port}';\n</script>\n<script",
                1
            )
            # Update fetch calls to use API_BASE_URL
            frontend_code = frontend_code.replace("fetch('/", "fetch(`${API_BASE_URL}/")
            frontend_code = frontend_code.replace('fetch("/', 'fetch(`${API_BASE_URL}/')

    with open(frontend_path, 'w', encoding='utf-8') as f:
        f.write(frontend_code)

    # Write a Dockerfile for the generated project
    dockerfile_path = os.path.join(project_dir, 'Dockerfile')
    try:
        with open(dockerfile_path, 'w', encoding='utf-8') as df:
            df.write(generate_dockerfile(get_project(project_id)))
    except Exception:
        pass
    
    # Start Flask server
    try:
        process = subprocess.Popen(
            [sys.executable, "app.py"],
            cwd=project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        # Wait a bit and check if process is still running
        time.sleep(2)
        poll_result = process.poll()
        
        if poll_result is not None:
            # Process died, get error
            stderr_output = process.stderr.read() if process.stderr else ""
            return None, f"Flask failed to start: {stderr_output[:500]}"
        
        # Save run info
        conn = sqlite3.connect('project_builder.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO project_runs (project_id, pid, port, status)
            VALUES (?, ?, ?, 'running')
        ''', (project_id, process.pid, port))
        
        # Update project port and status
        cursor.execute('UPDATE projects SET port = ?, status = ? WHERE id = ?', (port, 'running', project_id))
        conn.commit()
        conn.close()
        
        # Store in session state for quick access
        if 'running_projects' not in st.session_state:
            st.session_state.running_projects = {}
        st.session_state.running_projects[project_id] = {
            'pid': process.pid,
            'port': port,
            'process': process
        }
        
        # Open browser automatically after 1 second
        def open_browser():
            webbrowser.open(f'http://127.0.0.1:{port}')
        
        Timer(1.0, open_browser).start()
        
        return port, None
    except Exception as e:
        return None, f"Error starting Flask: {str(e)}"

# Stop project
def stop_project(project_id):
    # Try session state first (faster)
    if 'running_projects' in st.session_state and project_id in st.session_state.running_projects:
        try:
            proj_info = st.session_state.running_projects[project_id]
            pid = proj_info['pid']
            
            # Try graceful termination
            try:
                os.kill(pid, signal.SIGTERM)
            except:
                # Force kill on Windows
                if sys.platform == 'win32':
                    subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True)
                else:
                    os.kill(pid, signal.SIGKILL)
            
            # Remove from session state
            del st.session_state.running_projects[project_id]
            
            # Update database
            conn = sqlite3.connect('project_builder.db')
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE project_runs 
                SET status = 'stopped', stopped_at = CURRENT_TIMESTAMP
                WHERE pid = ?
            ''', (pid,))
            cursor.execute('UPDATE projects SET status = ? WHERE id = ?', ('stopped', project_id))
            conn.commit()
            conn.close()
            
            return True
        except Exception as e:
            st.error(f"Error stopping from session: {e}")
    
    # Fallback to database lookup
    conn = sqlite3.connect('project_builder.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT pid FROM project_runs 
        WHERE project_id = ? AND status = 'running'
        ORDER BY started_at DESC LIMIT 1
    ''', (project_id,))
    result = cursor.fetchone()
    
    if result:
        pid = result[0]
        try:
            # Try graceful termination
            try:
                os.kill(pid, signal.SIGTERM)
            except:
                # Force kill on Windows
                if sys.platform == 'win32':
                    subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True)
                else:
                    os.kill(pid, signal.SIGKILL)
            
            cursor.execute('''
                UPDATE project_runs 
                SET status = 'stopped', stopped_at = CURRENT_TIMESTAMP
                WHERE pid = ?
            ''', (pid,))
            cursor.execute('UPDATE projects SET status = ? WHERE id = ?', ('stopped', project_id))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            conn.close()
            return False
    conn.close()
    return False

# Export project
def export_project(project_id):
    project = get_project(project_id)
    if not project:
        return None
    
    export_data = {
        "name": project[1],
        "description": project[2],
        "prompt": project[3],
        "backend": project[4],
        "frontend": project[5],
        "framework": project[10],
        "exported_at": datetime.now().isoformat()
    }
    # Also include a simple Dockerfile for the project
    export_data['Dockerfile'] = generate_dockerfile(project)
    return json.dumps(export_data, indent=2)

def generate_dockerfile(project):
    # project is a row from the DB — indices: id, name, description, prompt, backend, frontend, created_at,...
    name = project[1].lower().replace(' ', '_')
    dockerfile = f'''# Simple Dockerfile for Flask app
FROM python:3.12-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir flask flask_cors
ENV PYTHONUNBUFFERED=1
EXPOSE 5000
CMD ["python","app.py"]
'''
    return dockerfile

# Import project
def import_project(json_data):
    try:
        data = json.loads(json_data)
        project_id = save_project(
            data["name"],
            data["description"],
            data.get("prompt", "Imported project"),
            data["backend"],
            data["frontend"],
            data.get("framework", "react")
        )
        return project_id
    except Exception as e:
        return None

# Streamlit UI
def main():
    st.set_page_config(
        page_title="AI Project Builder",
        page_icon="🚀",
        layout="wide"
    )
    
    # Initialize
    init_database()
    
    # Sidebar
    st.sidebar.title("🚀 AI Project Builder")
    # --- Authentication UI ---
    if 'user' not in st.session_state:
        st.session_state.user = None

    if st.session_state.user is None:
        with st.sidebar.expander("Sign in / Sign up", expanded=True):
            auth_tab = st.radio("Action", ["Sign in", "Sign up"], index=0, key='auth_tab')
            email = st.text_input("Email", key='auth_email')
            password = st.text_input("Password", type='password', key='auth_password')
            if auth_tab == 'Sign up':
                if st.button("Create account", key='create_account'):
                    uid = create_user(email, password)
                    if uid:
                        st.success("Account created. Please sign in.")
                    else:
                        st.error("Account already exists or invalid input.")
            else:
                if st.button("Sign in", key='signin'):
                    user = authenticate_user(email, password)
                    if user:
                        token = generate_jwt_token(user['id'], user['email'])
                        st.session_state.user = user
                        st.session_state.token = token
                        st.success(f"Signed in as {user['email']}")
                        st.experimental_rerun()
                    else:
                        st.error("Invalid credentials")
    else:
        st.sidebar.markdown(f"**Signed in as:** {st.session_state.user['email']}")
        if st.sidebar.button("Sign out"):
            st.session_state.user = None
            st.session_state.token = None
            st.experimental_rerun()

    st.sidebar.markdown("---")
    
    menu = st.sidebar.radio(
        "Navigation",
        ["🏠 Home", "➕ Create Project", "📁 My Projects", "⚙️ Settings"]
    )
    
    # Initialize Groq client
    if menu in ["➕ Create Project"]:
        try:
            client = init_groq()
        except:
            st.error("Failed to initialize Groq client. Please check your API key.")
            return
    
    if menu == "🏠 Home":
        st.title("Welcome to AI Project Builder 🚀")
        st.markdown("""
        ### Build Full-Stack Applications with AI in Seconds!
        
        This tool uses **Groq AI** to generate complete full-stack applications based on your prompts.
        
        #### Features:
        - 🤖 **AI-Powered Generation**: Describe your app, get production-ready code
        - 🎯 **Full-Stack**: Flask backend + React/HTML frontend in single files
        - 💾 **Smart Storage**: All projects stored in SQLite database
        - 🚀 **One-Click Deploy**: Run projects instantly on available ports
        - 📝 **Project Management**: Edit, export, import, and version control
        - 🎨 **Modern UI**: Tailwind CSS styling out of the box
        - 🔧 **Developer-Friendly**: Placeholder routes for easy extension
        
        #### Quick Start:
        1. Go to **Create Project**
        2. Describe your application
        3. Click **Generate**
        4. Review and **Run** your project
        5. Access at the provided URL
        
        #### Examples:
        - "Build me a TODO app with categories"
        - "Create a blog platform with posts and comments"
        - "Make a weather dashboard app"
        - "Build an expense tracker with charts"
        """)
        
        if 'project_generated' not in st.session_state:
            st.session_state.project_generated = False
        if 'current_result' not in st.session_state:
            st.session_state.current_result = None
        if 'current_tokens' not in st.session_state:
            st.session_state.current_tokens = 0
        if 'project_saved' not in st.session_state:
            st.session_state.project_saved = False
        if 'saved_project_id' not in st.session_state:
            st.session_state.saved_project_id = None
        # Statistics
        projects = get_all_projects()
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Projects", len(projects))
        with col2:
            running = sum(1 for p in projects if p[8] == 'running')
            st.metric("Running Projects", running)
        with col3:
            recent = sum(1 for p in projects if (datetime.now() - datetime.fromisoformat(p[6])).days < 7)
            st.metric("Created This Week", recent)
    
    elif menu == "➕ Create Project":
        st.title("Create New Project")
        
        with st.form("project_form"):
            prompt = st.text_area(
                "Describe your application",
                placeholder="e.g., Build me a TODO app with priority levels and due dates",
                height=150
            )
            
            col1, col2 = st.columns(2)
            with col1:
                framework = st.selectbox(
                    "Frontend Framework",
                    ["react", "vanilla-js"],
                    help="React uses CDN, vanilla-js is pure JavaScript"
                )
            with col2:
                advanced = st.checkbox("Show advanced options")
            
            if advanced:
                st.markdown("#### Advanced Options")
                include_auth = st.checkbox("Include authentication placeholders")
                include_db = st.checkbox("Include database schema comments")
                include_tests = st.checkbox("Include test examples")
            
            submit = st.form_submit_button("🚀 Generate Project", use_container_width=True)
        


# Your existing code with modifications:
        if submit and prompt:
            if st.session_state.get('user') is None:
                st.error("You must be signed in to generate projects.")
            else:
                with st.spinner("🤖 AI is generating your project..."):
                    # Enhance prompt with advanced options
                    enhanced_prompt = prompt
                    if advanced:
                        if include_auth:
                            enhanced_prompt += "\n- Include authentication route placeholders (login, register, logout)"
                        if include_db:
                            enhanced_prompt += "\n- Add detailed database schema as comments"
                        if include_tests:
                            enhanced_prompt += "\n- Include example unit tests as comments"

                    result, tokens = generate_project_code(client, enhanced_prompt, framework)

                    if result:
                        # Store in session state instead of local variables
                        st.session_state.current_result = result
                        st.session_state.current_tokens = tokens
                        st.session_state.project_generated = True
                        st.session_state.project_saved = False  # Reset saved status
                        st.session_state.saved_project_id = None

        # Display generated project if it exists in session state
        if st.session_state.project_generated and st.session_state.current_result:
            result = st.session_state.current_result
            tokens = st.session_state.current_tokens

            st.success("✅ Project generated successfully!")
            st.info(f"Tokens used: {tokens}")

            # Display generated content
            st.markdown(f"## {result['project_name']}")
            st.markdown(f"*{result['description']}*")

            if 'features' in result:
                st.markdown("### Features:")
                for feature in result['features']:
                    st.markdown(f"- {feature}")

            tab1, tab2, tab3 = st.tabs(["Backend (app.py)", "Frontend (index.html)", "Setup"])
            
            with tab1:
                st.code(result['backend'], language='python')
            
            with tab2:
                st.code(result['frontend'], language='html')
            
            if 'setup_instructions' in result:
                with tab3:
                    st.code(result.get('setup_instructions',""), language='html')
            
            # Save project button
            if not st.session_state.project_saved:
                if st.button("💾 Save Project", key="save_btn"):
                    owner_id = None
                    if st.session_state.get('user'):
                        owner_id = st.session_state['user']['id']
                    project_id = save_project(
                        result['project_name'],
                        result['description'],
                        prompt,
                        result['backend'],
                        result['frontend'],
                        framework,
                        owner_id=owner_id
                    )
                    
                    # Save generation history
                    conn = sqlite3.connect('project_builder.db')
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO generation_history (project_id, prompt, response, tokens_used)
                        VALUES (?, ?, ?, ?)
                    """, (project_id, prompt, json.dumps(result), tokens))
                    conn.commit()
                    conn.close()
                    
                    # Update session state
                    st.session_state.project_saved = True
                    st.session_state.saved_project_id = project_id
                    st.rerun()  # Rerun to update the UI
            
            # Show success message and run button after saving
            if st.session_state.project_saved:
                st.success(f"Project saved with ID: {st.session_state.saved_project_id}")
                st.balloons()
                
                # Option to run immediately
                if st.button("▶️ Run Now", key="run_btn"):
                    port, error = run_project(st.session_state.saved_project_id)
                    if port:
                        st.success(f"🎉 Project running at: http://localhost:{port}")
                    else:
                        st.error(f"Failed to run: {error}")
        
    elif menu == "📁 My Projects":
        st.title("My Projects")
        
        projects = get_all_projects()
        
        if not projects:
            st.info("No projects yet. Create your first project!")
            return
        
        # Search and filter
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            search = st.text_input("🔍 Search projects", placeholder="Search by name or description")
        with col2:
            filter_status = st.selectbox("Status", ["All", "Running", "Stopped"])
        with col3:
            sort_by = st.selectbox("Sort by", ["Recent", "Name", "Oldest"])
        
        # Filter projects
        filtered_projects = projects
        if search:
            filtered_projects = [p for p in filtered_projects if search.lower() in p[1].lower() or search.lower() in (p[2] or '').lower()]
        
        # Display projects
        for project in filtered_projects:
            # projects table now includes owner_id as the last column; unpack accordingly
            project_id, name, description, prompt, backend, frontend, created_at, modified_at, status, port, framework, owner_id = project
            
            with st.expander(f"📦 {name} - ID: {project_id}", expanded=False):
                col1, col2 = st.columns([3, 1])
                
                with col1:
                    st.markdown(f"**Description:** {description or 'No description'}")
                    st.markdown(f"**Created:** {created_at}")
                    st.markdown(f"**Framework:** {framework}")
                    if port:
                        st.markdown(f"**Port:** {port}")
                        st.markdown(f"**URL:** [http://localhost:{port}](http://localhost:{port})")
                
                with col2:
                    status_emoji = "🟢" if status == "running" else "⚪"
                    st.markdown(f"**Status:** {status_emoji} {status}")
                
                # Action buttons
                col1, col2, col3, col4, col5, col6 = st.columns(6)
                
                with col1:
                    if st.button("▶️ Run", key=f"run_{project_id}"):
                        port, error = run_project(project_id)
                        if port:
                            st.success(f"Running at http://localhost:{port}")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error(f"Error: {error}")
                
                with col2:
                    if st.button("⏹️ Stop", key=f"stop_{project_id}"):
                        if stop_project(project_id):
                            st.success("Stopped")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("Failed to stop")
                
                with col3:
                    if st.button("👁️ View", key=f"view_{project_id}"):
                        st.session_state.view_project = project_id
                
                with col4:
                    if st.button("✏️ Edit", key=f"edit_{project_id}"):
                        st.session_state.edit_project = project_id
                
                with col5:
                    if st.button("📤 Export", key=f"export_{project_id}"):
                        export_data = export_project(project_id)
                        st.download_button(
                            "Download JSON",
                            export_data,
                            file_name=f"{name}.json",
                            mime="application/json",
                            key=f"download_{project_id}"
                        )
                
                with col6:
                    if st.button("🗑️ Delete", key=f"delete_{project_id}", type="secondary"):
                        if st.session_state.get(f"confirm_delete_{project_id}"):
                            delete_project(project_id)
                            st.success("Deleted")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.session_state[f"confirm_delete_{project_id}"] = True
                            st.warning("Click again to confirm")
        
        # View project details
        # If editing a project, show code editors and save
        if st.session_state.get('edit_project'):
            edit_id = st.session_state.get('edit_project')
            proj = get_project(edit_id)
            if not proj:
                st.error("Project not found")
            else:
                st.markdown(f"## Editing: {proj[1]} (ID: {edit_id})")
                backend_code = st.text_area("Backend (app.py)", value=proj[4] or '', height=300, key=f'backend_{edit_id}')
                frontend_code = st.text_area("Frontend (index.html)", value=proj[5] or '', height=300, key=f'frontend_{edit_id}')
                colA, colB = st.columns(2)
                with colA:
                    if st.button("Save Changes", key=f'save_project_{edit_id}'):
                        update_project(edit_id, backend_code, frontend_code)
                        st.success("Saved")
                        # clear edit state
                        st.session_state.edit_project = None
                        st.experimental_rerun()
                with colB:
                    if st.button("Cancel", key=f'cancel_edit_{edit_id}'):
                        st.session_state.edit_project = None
                        st.experimental_rerun()
        if 'view_project' in st.session_state:
            project = get_project(st.session_state.view_project)
            if project:
                st.markdown("---")
                st.subheader(f"Viewing: {project[1]}")
                
                tab1, tab2 = st.tabs(["Backend", "Frontend"])
                with tab1:
                    st.code(project[4], language='python')
                with tab2:
                    st.code(project[5], language='html')
                
                if st.button("Close"):
                    del st.session_state.view_project
                    st.rerun()
        
        # Edit project
        if 'edit_project' in st.session_state:
            project = get_project(st.session_state.edit_project)
            if project:
                st.markdown("---")
                st.subheader(f"Editing: {project[1]}")
                
                with st.form("edit_form"):
                    new_backend = st.text_area("Backend Code", project[4], height=300)
                    new_frontend = st.text_area("Frontend Code", project[5], height=300)
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.form_submit_button("💾 Save Changes"):
                            update_project(st.session_state.edit_project, new_backend, new_frontend)
                            st.success("Project updated!")
                            del st.session_state.edit_project
                            time.sleep(1)
                            st.rerun()
                    with col2:
                        if st.form_submit_button("Cancel"):
                            del st.session_state.edit_project
                            st.rerun()
    
    elif menu == "⚙️ Settings":
        st.title("Settings")
        
        tab1, tab2, tab3 = st.tabs(["General", "Import/Export", "Database"])
        
        with tab1:
            st.subheader("Groq API Configuration")
            current = st.session_state.get('GROQ_API_KEY_TEMP', os.environ.get('GROQ_API_KEY', ''))
            api_key = st.text_input("Groq API Key (temporary, session only)", value=current, type="password")
            if st.button("Save temporary key"):
                st.session_state['GROQ_API_KEY_TEMP'] = api_key
                st.success("Temporary API Key saved for this session")
            if st.button("Clear temporary key"):
                st.session_state['GROQ_API_KEY_TEMP'] = ''
                st.info("Temporary API Key cleared")
            
            st.markdown("---")
            st.subheader("Default Settings")
            default_framework = st.selectbox("Default Framework", ["react", "vanilla-js"])
            auto_run = st.checkbox("Auto-run after generation")
            save_history = st.checkbox("Save generation history", value=True)
        
        with tab2:
            st.subheader("Import Project")
            uploaded_file = st.file_uploader("Upload project JSON", type=['json'])
            if uploaded_file:
                json_data = uploaded_file.read().decode('utf-8')
                if st.button("Import"):
                    project_id = import_project(json_data)
                    if project_id:
                        st.success(f"Project imported with ID: {project_id}")
                    else:
                        st.error("Failed to import project")
            
            st.markdown("---")
            st.subheader("Export All Projects")
            if st.button("Export Database"):
                projects = get_all_projects()
                all_projects = []
                for p in projects:
                    all_projects.append({
                        "id": p[0],
                        "name": p[1],
                        "description": p[2],
                        "backend": p[4],
                        "frontend": p[5]
                    })
                st.download_button(
                    "Download All Projects",
                    json.dumps(all_projects, indent=2),
                    file_name="all_projects.json",
                    mime="application/json"
                )
        
        with tab3:
            st.subheader("Database Management")
            
            conn = sqlite3.connect('project_builder.db')
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM projects")
            project_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM project_runs")
            runs_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM generation_history")
            history_count = cursor.fetchone()[0]
            
            conn.close()
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Projects", project_count)
            with col2:
                st.metric("Project Runs", runs_count)
            with col3:
                st.metric("Generation History", history_count)
            
            st.markdown("---")
            st.warning("⚠️ Danger Zone")
            if st.button("🗑️ Clear All Data", type="secondary"):
                if st.session_state.get("confirm_clear"):
                    conn = sqlite3.connect('project_builder.db')
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM projects")
                    cursor.execute("DELETE FROM project_runs")
                    cursor.execute("DELETE FROM generation_history")
                    conn.commit()
                    conn.close()
                    st.success("All data cleared")
                    st.session_state.confirm_clear = False
                else:
                    st.session_state.confirm_clear = True
                    st.error("Click again to confirm deletion")
    
    # Footer
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 📊 Quick Stats")
    projects = get_all_projects()
    st.sidebar.metric("Total Projects", len(projects))
    
    st.sidebar.markdown("---")
    st.sidebar.markdown("Made with ❤️ using Streamlit & Groq AI")

if __name__ == "__main__":
    main()