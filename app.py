"""
Flask backend for the SOP chatbot with Azure AD integration.
"""
import os
import uuid
import sqlite3
import subprocess
import json
import hashlib
import secrets
import re
from datetime import datetime, timedelta
from functools import wraps
from typing import List  # Add this import
from flask import Flask, request, jsonify, render_template, g, session, redirect, url_for, abort, send_from_directory
from dotenv import load_dotenv
import msal
import requests
from urllib.parse import urlencode
import pandas as pd

# Load environment variables
load_dotenv()

import config
from retrieval.retriever import hybrid_retrieve, reset_retriever_cache
from prompts.system_prompt import SYSTEM_PROMPT, build_prompt
import ollama_client

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
# True when served over HTTPS (a tunnel or a reverse proxy). Set
# SESSION_COOKIE_SECURE=1 in .env for those deployments.
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', '0') == '1'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)

DB_PATH = os.path.join(config.BASE_DIR, "database", "chat_history.db")
AUTH_DB_PATH = os.path.join(config.BASE_DIR, "database", "auth.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(os.path.dirname(AUTH_DB_PATH), exist_ok=True)

# Azure AD Configuration
AZURE_TENANT_ID = os.environ.get('AZURE_TENANT_ID')
AZURE_CLIENT_ID = os.environ.get('AZURE_CLIENT_ID')
AZURE_CLIENT_SECRET = os.environ.get('AZURE_CLIENT_SECRET')
AZURE_REDIRECT_URI = os.environ.get('AZURE_REDIRECT_URI', 'http://localhost:5000/login/azure/callback')
AZURE_AUTHORITY = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}" if AZURE_TENANT_ID else None
AZURE_SCOPE = ["User.Read"]

AZURE_CONFIGURED = all([AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET])


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


def get_auth_db():
    if "auth_db" not in g:
        g.auth_db = sqlite3.connect(AUTH_DB_PATH)
        g.auth_db.row_factory = sqlite3.Row
    return g.auth_db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()
    auth_db = g.pop("auth_db", None)
    if auth_db is not None:
        auth_db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            sources TEXT,
            confidence REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def init_auth_db():
    conn = sqlite3.connect(AUTH_DB_PATH)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_login TEXT,
            password_changed TEXT DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1,
            auth_provider TEXT DEFAULT 'local'
        )
    """)
    
    default_email = "admin@annovasolutions.in"
    default_password = "Annova@123"
    
    user = conn.execute("SELECT * FROM users WHERE email = ?", (default_email,)).fetchone()
    if not user:
        salt = secrets.token_hex(32)
        password_hash = hashlib.pbkdf2_hmac(
            'sha256',
            default_password.encode('utf-8'),
            salt.encode('utf-8'),
            100000
        ).hex()
        
        conn.execute(
            "INSERT INTO users (email, password_hash, salt, auth_provider) VALUES (?, ?, ?, 'local')",
            (default_email, password_hash, salt)
        )
        print(f"✅ Default user created: {default_email} / Password: {default_password}")
    
    conn.commit()
    conn.close()


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    ).hex()


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login_page') + '?expired=true')
        return f(*args, **kwargs)
    return decorated_function


def check_index():
    docstore_path = os.path.join(config.VECTORSTORE_DIR, "docstore.json")
    return os.path.exists(docstore_path)


def get_recent_history(session_id, limit=4):
    db = get_db()
    rows = db.execute(
        "SELECT role, content FROM chat_history WHERE session_id = ? ORDER BY id DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    rows = list(reversed(rows))
    return "\n".join(f"{r['role']}: {r['content']}" for r in rows)


def save_turn(session_id, role, content, sources=None, confidence=None):
    db = get_db()
    db.execute(
        "INSERT INTO chat_history (session_id, role, content, sources, confidence) VALUES (?, ?, ?, ?, ?)",
        (session_id, role, content, json.dumps(sources) if sources else None, confidence),
    )
    db.commit()


# ============================================================
# AUTHENTICATION ROUTES
# ============================================================

@app.route("/login")
def login_page():
    if session.get('authenticated'):
        return redirect(url_for('index'))
    return render_template("login.html", azure_configured=AZURE_CONFIGURED)


@app.route("/login/local", methods=["POST"])
def local_login():
    data = request.get_json()
    email = data.get('email', '').strip()
    password = data.get('password', '')
    
    if not email or not password:
        return jsonify({"success": False, "error": "Email and password are required"}), 400
    
    if not email.endswith('@annovasolutions.in'):
        return jsonify({"success": False, "error": "Please use @annovasolutions.in email"}), 400
    
    auth_db = get_auth_db()
    user = auth_db.execute(
        "SELECT * FROM users WHERE email = ? AND is_active = 1",
        (email,)
    ).fetchone()
    
    if not user:
        if password == "Annova@123":
            salt = secrets.token_hex(32)
            password_hash = hash_password("Annova@123", salt)
            auth_db.execute(
                "INSERT INTO users (email, password_hash, salt, auth_provider) VALUES (?, ?, ?, 'local')",
                (email, password_hash, salt)
            )
            auth_db.commit()
            user = auth_db.execute(
                "SELECT * FROM users WHERE email = ? AND is_active = 1",
                (email,)
            ).fetchone()
            print(f"✅ Auto-created user: {email}")
        else:
            return jsonify({"success": False, "error": "User not found. Please use default password for first login."}), 401
    
    computed_hash = hash_password(password, user['salt'])
    if computed_hash != user['password_hash']:
        return jsonify({"success": False, "error": "Invalid password"}), 401
    
    auth_db.execute(
        "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?",
        (user['id'],)
    )
    auth_db.commit()
    
    session.permanent = True
    session['authenticated'] = True
    session['user_email'] = user['email']
    session['user_id'] = user['id']
    session['auth_provider'] = 'local'
    
    return jsonify({
        "success": True,
        "redirect": "/",
        "user": {"email": user['email']}
    })


@app.route("/login/azure")
def azure_login():
    if session.get('authenticated'):
        return redirect(url_for('index'))
    
    if not AZURE_CONFIGURED:
        return jsonify({"error": "Azure AD is not configured."}), 500
    
    session['azure_state'] = secrets.token_hex(16)
    auth_url = f"{AZURE_AUTHORITY}/oauth2/v2.0/authorize"
    params = {
        'client_id': AZURE_CLIENT_ID,
        'response_type': 'code',
        'redirect_uri': AZURE_REDIRECT_URI,
        'scope': ' '.join(AZURE_SCOPE),
        'state': session['azure_state'],
        'response_mode': 'query'
    }
    return redirect(f"{auth_url}?{urlencode(params)}")


@app.route("/login/azure/callback")
def azure_callback():
    state = request.args.get('state')
    if state != session.get('azure_state'):
        return jsonify({"error": "Invalid state parameter"}), 400
    
    code = request.args.get('code')
    if not code:
        error = request.args.get('error')
        return jsonify({"error": f"No authorization code received: {error}"}), 400
    
    try:
        token_url = f"{AZURE_AUTHORITY}/oauth2/v2.0/token"
        token_data = {
            'client_id': AZURE_CLIENT_ID,
            'client_secret': AZURE_CLIENT_SECRET,
            'code': code,
            'redirect_uri': AZURE_REDIRECT_URI,
            'grant_type': 'authorization_code',
            'scope': ' '.join(AZURE_SCOPE)
        }
        token_response = requests.post(token_url, data=token_data)
        if token_response.status_code != 200:
            return jsonify({"error": f"Token exchange failed: {token_response.text}"}), 400
        
        tokens = token_response.json()
        user_info_url = "https://graph.microsoft.com/v1.0/me"
        headers = {'Authorization': f"Bearer {tokens['access_token']}"}
        user_response = requests.get(user_info_url, headers=headers)
        
        if user_response.status_code != 200:
            return jsonify({"error": "Could not fetch user info"}), 400
        
        user_info = user_response.json()
        user_email = user_info.get('mail') or user_info.get('userPrincipalName')
        user_name = user_info.get('displayName', user_email)
        
        if not user_email:
            return jsonify({"error": "Could not retrieve user email"}), 400
        
        if not user_email.endswith('@annovasolutions.com'):
            return jsonify({"error": "Only @annovasolutions.com users are allowed"}), 403
        
        auth_db = get_auth_db()
        user = auth_db.execute(
            "SELECT * FROM users WHERE email = ? AND is_active = 1",
            (user_email,)
        ).fetchone()
        
        if not user:
            salt = secrets.token_hex(32)
            password_hash = hash_password(secrets.token_hex(32), salt)
            auth_db.execute(
                "INSERT INTO users (email, password_hash, salt, auth_provider) VALUES (?, ?, ?, 'azure')",
                (user_email, password_hash, salt)
            )
            auth_db.commit()
        
        session.permanent = True
        session['authenticated'] = True
        session['user_email'] = user_email
        session['user_id'] = user_info.get('id')
        session['auth_provider'] = 'azure'
        session['user_name'] = user_name
        
        return redirect(url_for('index'))
        
    except Exception as e:
        return jsonify({"error": f"Authentication failed: {str(e)}"}), 500


@app.route("/api/auth/check")
def auth_check():
    return jsonify({
        "authenticated": session.get('authenticated', False),
        "user": {
            "email": session.get('user_email'),
            "name": session.get('user_name', session.get('user_email')),
            "provider": session.get('auth_provider', 'unknown')
        } if session.get('authenticated') else None
    })


@app.route("/api/auth/reset-password", methods=["POST"])
def reset_password():
    data = request.get_json()
    email = data.get('email', '').strip()
    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')
    
    if not email or not current_password or not new_password:
        return jsonify({"success": False, "error": "All fields are required"}), 400
    
    if not email.endswith('@annovasolutions.in'):
        return jsonify({"success": False, "error": "Please use @annovasolutions.in email"}), 400
    
    if len(new_password) < 8:
        return jsonify({"success": False, "error": "Password must be at least 8 characters"}), 400
    
    if not any(c.isupper() for c in new_password):
        return jsonify({"success": False, "error": "Password must contain at least one uppercase letter"}), 400
    
    if not any(c.islower() for c in new_password):
        return jsonify({"success": False, "error": "Password must contain at least one lowercase letter"}), 400
    
    if not any(c.isdigit() for c in new_password):
        return jsonify({"success": False, "error": "Password must contain at least one number"}), 400
    
    if not any(c in "!@#$%^&*(),.?\":{}|<>" for c in new_password):
        return jsonify({"success": False, "error": "Password must contain at least one special character"}), 400
    
    auth_db = get_auth_db()
    user = auth_db.execute(
        "SELECT * FROM users WHERE email = ? AND is_active = 1 AND auth_provider = 'local'",
        (email,)
    ).fetchone()
    
    if not user:
        return jsonify({"success": False, "error": "User not found or uses Azure AD"}), 404
    
    computed_hash = hash_password(current_password, user['salt'])
    if computed_hash != user['password_hash']:
        return jsonify({"success": False, "error": "Current password is incorrect"}), 401
    
    if hash_password(new_password, user['salt']) == user['password_hash']:
        return jsonify({"success": False, "error": "New password must be different from current password"}), 400
    
    new_salt = secrets.token_hex(32)
    new_hash = hash_password(new_password, new_salt)
    auth_db.execute(
        "UPDATE users SET password_hash = ?, salt = ?, password_changed = CURRENT_TIMESTAMP WHERE id = ?",
        (new_hash, new_salt, user['id'])
    )
    auth_db.commit()
    
    return jsonify({"success": True, "message": "Password reset successfully"})


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login_page'))


@app.route("/")
@login_required
def index():
    return render_template("index.html", user_email=session.get('user_email'))


@app.route("/api/documents")
@login_required
def documents():
    docstore_path = os.path.join(config.VECTORSTORE_DIR, "docstore.json")
    if not os.path.exists(docstore_path):
        return jsonify({"documents": []})
    with open(docstore_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    names = sorted(set(c["document_name"] for c in chunks))
    return jsonify({"documents": [{"name": name} for name in names]})


# ============================================================
# SOP IMAGE SUPPORT
# ============================================================

WEB_SAFE_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _image_urls_from_retrieved(retrieved):
    """Create safe, browser-accessible URLs for images linked to retrieved topics."""
    image_root = os.path.abspath(getattr(config, "SOP_IMAGE_DIR", os.path.join(config.VECTORSTORE_DIR, "sop_images")))
    urls = []
    seen = set()
    considered = 0
    first_seen = ""

    for chunk in retrieved or []:
        metadata = chunk.get("metadata", {})
        paths = metadata.get("image_paths", []) or []
        for image_path in paths:
            considered += 1
            first_seen = first_seen or str(image_path)
            try:
                # New indexes store paths RELATIVE to the image root
                # ("West/West_0079.png"); older ones stored absolute paths.
                # abspath() on a relative path resolves it against the process
                # working directory, which fails the commonpath check below and
                # silently drops every image.
                if os.path.isabs(image_path):
                    absolute_path = os.path.abspath(image_path)
                else:
                    absolute_path = os.path.abspath(os.path.join(image_root, image_path))

                common = os.path.commonpath([image_root, absolute_path])
                if common != image_root or not os.path.isfile(absolute_path):
                    continue

                # EMF/WMF cannot be rendered by a browser; emitting them
                # produces a broken image showing the alt text.
                if os.path.splitext(absolute_path)[1].lower() not in WEB_SAFE_IMAGE_EXTS:
                    continue

                relative_path = os.path.relpath(absolute_path, image_root).replace(os.sep, "/")
                url = url_for("serve_sop_image", image_path=relative_path)
                if url not in seen:
                    seen.add(url)
                    urls.append({
                        "url": url,
                        "document": metadata.get("document_name", ""),
                        "topic": metadata.get("topic_path") or metadata.get("header", ""),
                        "page": metadata.get("page_start") or metadata.get("page", ""),
                    })
            except (ValueError, OSError):
                continue

    if considered and not urls:
        print(f"🖼️ {considered} image path(s) on the answer topic but none served "
              f"(missing file, outside {image_root}, or non-web format). First: {first_seen}")
    elif urls:
        print(f"🖼️ {len(urls)} image(s) attached from the answer topic")

    return urls[:getattr(config, "MAX_IMAGES_PER_ANSWER", 6)]


@app.route("/api/sop-images/<path:image_path>")
@login_required
def serve_sop_image(image_path):
    """Serve only images extracted into the configured SOP image directory."""
    image_root = os.path.abspath(getattr(config, "SOP_IMAGE_DIR", os.path.join(config.VECTORSTORE_DIR, "sop_images")))
    requested = os.path.abspath(os.path.join(image_root, image_path))
    try:
        if os.path.commonpath([image_root, requested]) != image_root:
            abort(404)
    except ValueError:
        abort(404)
    if not os.path.isfile(requested):
        abort(404)
    return send_from_directory(image_root, image_path)


# ============================================================
# FACILITY DATA ENDPOINT
# ============================================================

# Facility records come from an encrypted database so the spreadsheet does not
# have to be shipped with the code. If the encrypted file is absent the loader
# falls back to the original Excel, so existing installs keep working.
FACILITY_DATA_PATH = os.path.join(config.BASE_DIR, "Facility Data.xlsx")
FACILITY_DB_PATH = os.environ.get(
    "FACILITY_DB_PATH",
    os.path.join(config.BASE_DIR, "database", "facilities.enc"),
)
facility_df = None
facility_source = "none"


def load_facility_data():
    """Load facility records, preferring the encrypted database over Excel."""
    global facility_df, facility_source
    try:
        import facility_store

        facility_df, facility_source = facility_store.load_dataframe(
            FACILITY_DB_PATH, FACILITY_DATA_PATH
        )

        if facility_df is None:
            print("❌ No facility data available.")
            print(f"   Looked for: {FACILITY_DB_PATH}")
            print(f"   and:        {FACILITY_DATA_PATH}")
            print("   Build the encrypted database with:")
            print("     python tools/build_facility_db.py --excel \"<path to xlsx>\"")
            facility_source = "none"
            return False

        label = "encrypted database" if facility_source == "encrypted" else "Excel file"
        print(f"✅ Loaded {len(facility_df)} facility records from {label}")
        print(f"   Columns: {list(facility_df.columns)}")
        if facility_source == "excel":
            print("   ℹ️ Using the spreadsheet. To stop shipping it, run:")
            print("     python tools/build_facility_db.py")
        return True

    except Exception as e:
        print(f"❌ Error loading facility data: {e}")
        import traceback
        traceback.print_exc()
        facility_df = None
        facility_source = "none"
        return False


# Load data on startup
load_facility_data()

@app.route("/api/facilities/search", methods=["GET"])
@login_required
def search_facilities():
    """Search for facilities by name with progressive filtering"""
    try:
        query = request.args.get('q', '').strip()
        
        if not query or facility_df is None or facility_df.empty:
            return jsonify({"facilities": []})
        
        query_lower = query.lower()
        
        # Ensure Facility Name column exists
        if 'Facility Name' not in facility_df.columns:
            return jsonify({"facilities": []})
        
        # Filter facilities where Facility Name contains the query
        matched = facility_df[
            facility_df['Facility Name'].str.lower().str.contains(query_lower, na=False)
        ]
        
        if matched.empty:
            return jsonify({"facilities": []})
        
        # Score and rank results
        results = []
        seen_names = set()
        
        for _, row in matched.iterrows():
            name = str(row.get('Facility Name', ''))
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            
            name_lower = name.lower()
            
            # Calculate relevance score
            score = 0
            
            # Priority 1: Name starts with the query (highest priority)
            if name_lower.startswith(query_lower):
                score += 100
            
            # Priority 2: Name contains the query as a word boundary
            if f' {query_lower}' in name_lower or name_lower.startswith(query_lower):
                score += 50
            
            # Priority 3: Name contains the query anywhere
            if query_lower in name_lower:
                score += 10
            
            # Priority 4: Shorter names get slightly higher priority
            score += max(0, 20 - len(name_lower) // 5)
            
            # Priority 5: Exact match gets bonus
            if name_lower == query_lower:
                score += 50
            
            # Safely get values with fallbacks
            results.append({
                "name": name,
                "score": score,
                "time_zone": str(row.get('Time Zone', '')),
                "address": str(row.get('Address', '')),
                "primary_city": str(row.get('Primary City', '')),
                "primary_state": str(row.get('Primary State', '')),
                "primary_phone": str(row.get('Primary Phone', '')),
                "serve_city": str(row.get('Serve City', '')),
                "serve_state": str(row.get('Serve State', '')),
                "serve_phone": str(row.get('Serve Phone', ''))
            })
        
        # Sort by score (highest first)
        results.sort(key=lambda x: x['score'], reverse=True)
        
        # Return top results (limit to 20)
        return jsonify({"facilities": results[:20]})
        
    except Exception as e:
        print(f"❌ Error in search_facilities: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"facilities": []})

@app.route("/api/facilities/details", methods=["GET"])
@login_required
def get_facility_details():
    """Get full details for a specific facility"""
    try:
        facility_name = request.args.get('name', '').strip()
        
        if not facility_name or facility_df is None or facility_df.empty:
            return jsonify({"error": "Facility not found"}), 404
        
        # Find the facility
        matched = facility_df[
            facility_df['Facility Name'].str.strip() == facility_name
        ]
        
        if matched.empty:
            return jsonify({"error": "Facility not found"}), 404
        
        row = matched.iloc[0]
        return jsonify({
            "name": str(row.get('Facility Name', '')),
            "time_zone": str(row.get('Time Zone', '')),
            "address": str(row.get('Address', '')),
            "primary_city": str(row.get('Primary City', '')),
            "primary_state": str(row.get('Primary State', '')),
            "primary_phone": str(row.get('Primary Phone', '')),
            "serve_city": str(row.get('Serve City', '')),
            "serve_state": str(row.get('Serve State', '')),
            "serve_phone": str(row.get('Serve Phone', ''))
        })
        
    except Exception as e:
        print(f"❌ Error in get_facility_details: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Error fetching facility details"}), 500


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def is_faq_query(query: str) -> bool:
    """Check if the query is asking for FAQ content"""
    query_lower = query.lower()
    
    # Check for explicit FAQ indicators
    faq_indicators = [
        'how to', 'what is', 'when do', 'can we', 'should we', 
        'how can', 'what should', 'where do', 'why do', 'is it',
        'faq', 'question', 'what are', 'how do', 'what does',
        'explain', 'describe', 'tell me about', 'how to check',
        'what should we do', 'how do we', 'when should'
    ]
    
    # Check for question marks (indicating a question)
    has_question_mark = '?' in query
    
    # Check if any indicator matches
    has_indicator = any(indicator in query_lower for indicator in faq_indicators)
    
    # If it has a question mark and is long enough, it's likely a question
    if has_question_mark and len(query) > 15:
        return True
    
    return has_indicator


def extract_matching_faq(text: str, query: str) -> List[str]:
    """
    Extract only the FAQ that matches the query from a FAQ chunk
    """
    query_lower = query.lower()
    lines = text.split('\n')
    
    # Clean and normalize the text
    clean_lines = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith('[') and not line.startswith('#'):
            clean_lines.append(line)
    
    # Find all FAQ entries in the chunk
    faq_entries = []
    current_faq = []
    current_question = ""
    current_answer = []
    in_answer = False
    
    for line in clean_lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        
        # Check if this is a question line (starts with "Q" or contains "Q1.", "Q2.", etc.)
        is_question = False
        question_text = ""
        
        if re.match(r'^Q\d+[\.\)]\s+', line_clean):
            is_question = True
            question_text = line_clean
        elif line_clean.startswith('Q.'):
            is_question = True
            question_text = line_clean
        elif '?' in line_clean and len(line_clean) > 15 and line_clean[0].isupper():
            # This might be a question without Q numbering
            is_question = True
            question_text = line_clean
        
        if is_question:
            # Save previous FAQ if exists
            if current_question and current_answer:
                faq_entries.append({
                    'question': current_question,
                    'content': '\n'.join(current_answer)
                })
            # Start new FAQ
            current_question = question_text
            current_answer = []
            in_answer = True
        elif in_answer and current_question:
            # Collect answer lines
            # Skip separator lines
            if line_clean and not line_clean.startswith('---') and not line_clean.startswith('==='):
                current_answer.append(line_clean)
    
    # Save last FAQ
    if current_question and current_answer:
        faq_entries.append({
            'question': current_question,
            'content': '\n'.join(current_answer)
        })
    
    # If no FAQs found with Q pattern, try to find by keyword in text
    if not faq_entries:
        # Look for lines containing question marks
        for i, line in enumerate(clean_lines):
            if '?' in line and len(line) > 15:
                question = line.strip()
                answer = []
                # Get next few lines as answer
                for j in range(i+1, min(i+8, len(clean_lines))):
                    if clean_lines[j].strip() and not clean_lines[j].strip().endswith('?'):
                        if clean_lines[j].strip() and not clean_lines[j].strip().startswith('---'):
                            answer.append(clean_lines[j].strip())
                    else:
                        break
                if answer:
                    faq_entries.append({
                        'question': question,
                        'content': '\n'.join(answer)
                    })
    
    # Find the best matching FAQ
    best_match = None
    best_score = 0
    
    # Extract key terms from query
    stop_words = {'how', 'to', 'what', 'is', 'the', 'of', 'for', 'in', 'on', 'at', 'with', 'by', 'from', 'up', 'about', 'are', 'do', 'does', 'can', 'we', 'you'}
    query_keywords = [w for w in query_lower.split() if w not in stop_words and len(w) > 2]
    
    for faq in faq_entries:
        question_lower = faq['question'].lower()
        content_lower = faq['content'].lower()
        
        score = 0
        
        # Check if query keywords appear in question
        for keyword in query_keywords:
            if keyword in question_lower:
                score += 15
            if keyword in content_lower[:200]:
                score += 5
        
        # Check for exact phrase match
        if query_lower in question_lower:
            score += 40
        
        # Check for partial matches
        for keyword in query_keywords:
            if len(keyword) > 4 and keyword[:4] in question_lower:
                score += 8
        
        # Check for section numbers
        section_match = re.search(r'Q(\d+)', query)
        if section_match:
            q_num = section_match.group(1)
            if f'Q{q_num}' in faq['question'] or f'Q{q_num}.' in faq['question']:
                score += 60
        
        # Check for any number match
        number_match = re.search(r'(\d+)', query)
        if number_match:
            num = number_match.group(1)
            if f'Q{num}' in faq['question'] or f'Q{num}.' in faq['question']:
                score += 50
        
        if score > best_score:
            best_score = score
            best_match = faq
    
    # Return the best match content
    if best_match and best_score > 0:
        # Clean the content
        content_lines = best_match['content'].split('\n')
        clean_result = []
        for line in content_lines:
            line = line.strip()
            if line and not line.startswith('---') and not line.startswith('==='):
                # Remove any "A>" prefix if present
                line = re.sub(r'^A>\s*', '', line)
                clean_result.append(line)
        return clean_result
    
    return []


# def build_smart_fallback(query: str, chunks: list) -> str:
#     """
#     Build a smart fallback response showing ONLY the most relevant chunk
#     """
#     query_lower = query.lower()
    
#     # Score chunks to find the most relevant one
#     scored_chunks = []
#     for chunk in chunks:
#         text = chunk['text']
#         header = chunk['metadata'].get('header', '')
#         score = 0
        
#         # Topic-specific scoring
#         if 'non compliance' in query_lower or 'noncompliant' in query_lower:
#             if '21.1 Process of Non-compliance' in header or 'Process of Non-compliance' in header:
#                 score += 100
#             if 'Escalation Timeline' in header or 'ANC' in header:
#                 score -= 50
        
#         if 'vpu' in query_lower or 'vtc' in query_lower:
#             if 'VPU/VTC' in header or 'VPU -' in header:
#                 score += 100
#             if 'Trigger' in header or 'Reserve CA' in header:
#                 score -= 50
        
#         if 'offsite' in query_lower:
#             if 'OFFSITE' in header and 'Trigger' not in header:
#                 score += 100
#             if 'Trigger' in header:
#                 score -= 50
        
#         if 'serve new address' in query_lower or 'sna' in query_lower:
#             if 'Serve New Address' in header or 'SNA' in header:
#                 score += 100
#             if 'FACILITY PROOF' in header or 'TRANSFERS' in header:
#                 score -= 50
        
#         # Check for query words in header
#         query_words = [w for w in query_lower.split() if len(w) > 2]
#         for word in query_words:
#             if word in header.lower():
#                 score += 10
        
#         scored_chunks.append((score, chunk, header))
    
#     # Sort by score
#     scored_chunks.sort(key=lambda x: x[0], reverse=True)
    
#     # Get the best chunk
#     best_chunk = scored_chunks[0][1] if scored_chunks else chunks[0]
#     doc_name = best_chunk['metadata'].get('document_name', 'Unknown')
#     header = best_chunk['metadata'].get('header', '')
#     text = best_chunk['text'].strip()
    
#     # Clean the text
#     text = text.replace('[', '').replace(']', '')
#     text = text.replace('**', '')
#     text = text.replace('##', '')
#     text = text.replace('###', '')
    
#     # Build response
#     answer_parts = []
#     answer_parts.append(f"📋 **{header}**")
#     answer_parts.append("")
    
#     # Extract lines
#     lines = text.split('\n')
#     content_lines = []
#     seen_content = set()
    
#     for line in lines:
#         line_clean = line.strip()
#         if not line_clean:
#             continue
#         if line_clean.startswith('[') or line_clean.startswith('#'):
#             continue
#         if line_clean == header:
#             continue
        
#         clean_line = line_clean
#         clean_line = clean_line.replace('**', '').replace('##', '').replace('###', '')
#         clean_line = re.sub(r'^[\s]*[-•*]\s+', '', clean_line)
#         clean_line = re.sub(r'^[\s]*\d+[.)]\s+', '', clean_line)
#         clean_line = ' '.join(clean_line.split())
        
#         if clean_line and len(clean_line) > 3 and clean_line not in ['FAQ:', 'FAQ']:
#             key = clean_line[:50]
#             if key not in seen_content:
#                 seen_content.add(key)
#                 content_lines.append(clean_line)
    
#     # If no content found, use first few lines
#     if not content_lines:
#         for line in lines[:15]:
#             clean_line = line.strip()
#             if clean_line and len(clean_line) > 5:
#                 clean_line = clean_line.replace('**', '').replace('##', '').replace('###', '')
#                 clean_line = re.sub(r'^[\s]*[-•*]\s+', '', clean_line)
#                 clean_line = ' '.join(clean_line.split())
#                 if clean_line and clean_line not in ['FAQ:', 'FAQ']:
#                     content_lines.append(clean_line)
    
#     # Format with step numbers
#     step_num = 1
#     for line in content_lines[:15]:
#         if line and line[0].islower():
#             line = line[0].upper() + line[1:]
#         if not line.endswith('.') and len(line) > 20:
#             line = line + '.'
#         answer_parts.append(f"  {step_num}. {line}")
#         step_num += 1
    
#     if len(answer_parts) <= 2:
#         answer_parts.append("  No specific information found in the documentation.")
    
#     answer_parts.append("")
#     answer_parts.append("---")
#     answer_parts.append(f"*Source: {doc_name}*")
    
#     return '\n'.join(answer_parts)

# def build_smart_fallback(query: str, chunks: list) -> str:
#     """
#     Build a smart fallback response showing ALL relevant content from the best chunk
#     """
#     query_lower = query.lower()
    
#     # Score chunks to find the most relevant one
#     scored_chunks = []
#     for chunk in chunks:
#         text = chunk['text']
#         header = chunk['metadata'].get('header', '')
#         doc_name = chunk['metadata'].get('document_name', '')
#         score = 0
        
#         # Topic-specific scoring
#         if 'offsite' in query_lower:
#             if 'Offsite' in header or 'OFFSITE' in header:
#                 score += 100
#             if 'Close' in header or 'close' in header:
#                 score += 50
        
#         if 'deposition' in query_lower or 'depo' in query_lower:
#             if 'Deposition' in header or 'Depo' in header:
#                 score += 100
        
#         if 'non-compliance' in query_lower or 'noncompliant' in query_lower:
#             if 'Non-Compliance' in header or 'Non-compliance' in header:
#                 score += 100
        
#         if 'vpu' in query_lower or 'vtc' in query_lower:
#             if 'VPU' in header or 'VTC' in header:
#                 score += 100
        
#         if 'serve new address' in query_lower or 'sna' in query_lower:
#             if 'Serve New Address' in header or 'SNA' in header:
#                 score += 100
        
#         if 'invoice' in query_lower:
#             if 'Invoice' in header:
#                 score += 100
        
#         if 'x-ray' in query_lower or 'xray' in query_lower:
#             if 'X-Ray' in header or 'XRAY' in header:
#                 score += 100
        
#         # Check for query words in header
#         query_words = [w for w in query_lower.split() if len(w) > 2]
#         for word in query_words:
#             if word in header.lower():
#                 score += 10
        
#         scored_chunks.append((score, chunk, header, doc_name))
    
#     # Sort by score
#     scored_chunks.sort(key=lambda x: x[0], reverse=True)
    
#     # Get the best chunk
#     best_score, best_chunk, best_header, best_doc = scored_chunks[0] if scored_chunks else (0, chunks[0], '', '')
    
#     # If we have a high-scoring chunk, use it; otherwise use the first one
#     if best_score > 0:
#         chunk = best_chunk
#         doc_name = best_doc
#         header = best_header
#     else:
#         chunk = chunks[0]
#         doc_name = chunk['metadata'].get('document_name', 'Unknown')
#         header = chunk['metadata'].get('header', '')
    
#     text = chunk['text'].strip()
    
#     # Clean the text
#     text = text.replace('**', '')
#     text = text.replace('##', '')
#     text = text.replace('###', '')
    
#     # Remove document prefix if present
#     if 'Document:' in text:
#         lines = text.split('\n')
#         cleaned_lines = []
#         for line in lines:
#             if 'Document:' in line or 'Topic:' in line:
#                 continue
#             cleaned_lines.append(line)
#         text = '\n'.join(cleaned_lines)
    
#     # Build response with ALL content
#     answer_parts = []
#     answer_parts.append(f"📋 **{header}**")
#     answer_parts.append("")
    
#     # Process all lines, keeping the structure
#     lines = text.split('\n')
#     content_lines = []
    
#     for line in lines:
#         line_clean = line.strip()
#         if not line_clean:
#             continue
        
#         # Skip markdown artifacts
#         if line_clean.startswith('[') or line_clean.startswith('#'):
#             continue
#         if line_clean == header:
#             continue
#         if line_clean == '---':
#             continue
        
#         # Clean the line
#         clean_line = line_clean
#         clean_line = clean_line.replace('**', '').replace('##', '').replace('###', '')
#         clean_line = clean_line.replace('`', '')
        
#         # Remove bullet markers but keep the content
#         clean_line = re.sub(r'^[\s]*[-•*]\s+', '', clean_line)
#         clean_line = re.sub(r'^[\s]*\d+[.)]\s+', '', clean_line)
        
#         # Clean up extra spaces
#         clean_line = ' '.join(clean_line.split())
        
#         if clean_line and len(clean_line) > 2:
#             content_lines.append(clean_line)
    
#     # If no content found, use the original lines
#     if not content_lines:
#         for line in lines:
#             clean_line = line.strip()
#             if clean_line and len(clean_line) > 5:
#                 clean_line = clean_line.replace('**', '').replace('##', '').replace('###', '')
#                 clean_line = ' '.join(clean_line.split())
#                 if clean_line and clean_line != header:
#                     content_lines.append(clean_line)
    
#     # Add all content lines
#     for line in content_lines:
#         # Capitalize first letter if it's a complete sentence
#         if line and line[0].islower() and len(line) > 10:
#             line = line[0].upper() + line[1:]
#         answer_parts.append(f"  {line}")
    
#     # If we have very few lines, add a note
#     if len(answer_parts) <= 2:
#         answer_parts.append("  No specific information found in the documentation.")
    
#     answer_parts.append("")
#     answer_parts.append("---")
#     answer_parts.append(f"*Source: {doc_name}*")
    
#     return '\n'.join(answer_parts)

CONTEXT_ARTIFACT_RE = re.compile(
    r"^\s*(?:=== DOCUMENT \d+.*?===|Section:.*|Sub-section:.*|Document:.*|Topic:.*|-{3,})\s*$",
    re.IGNORECASE,
)
STRUCTURE_LINE_RE = re.compile(r"^(?:\d+[.)]|[-\u2022\u25cf\u25aa\u25e6*]\s|o\s|[A-Za-z][.)]\s)")


def strip_context_artifacts(answer: str) -> str:
    """Remove prompt scaffolding ('=== DOCUMENT 1: West_FAQ ===') the model copied."""
    lines = [line for line in (answer or "").splitlines() if not CONTEXT_ARTIFACT_RE.match(line)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _structure_units(text: str):
    """Numbered steps and bullets present in a piece of text."""
    units = []
    for line in (text or "").splitlines():
        line = " ".join(line.split())
        if STRUCTURE_LINE_RE.match(line):
            body = STRUCTURE_LINE_RE.sub("", line).strip().lower()
            if len(body) > 8:
                units.append(body)
    return units


def answer_is_complete(answer: str, chunk: dict, min_coverage: float = 0.8) -> bool:
    """A procedural answer must reproduce the steps present in the source topic."""
    source_units = _structure_units(chunk.get("text", ""))
    if len(source_units) < 3:
        return True
    blob = " ".join((answer or "").lower().split())
    covered = sum(1 for unit in source_units if unit[:25] in blob)
    coverage = covered / len(source_units)
    if coverage < min_coverage:
        print(f"⚠️ Answer covers only {covered}/{len(source_units)} source steps — serving topic verbatim")
    return coverage >= min_coverage


def fix_llm_hallucinations(response: str, query: str) -> str:
    """
    Fix common LLM hallucinations by replacing incorrect terms with correct ones.
    """
    # Map of incorrect terms -> correct terms
    corrections = {
        "Recover Records Available Soon with ETA": "Records Available Shortly with ETA",
        "Recover Records": "Records",
        "Solcom Retrieval Note": "Solcom – Retrieval Note",
        "Solcom Retrival Note": "Solcom – Retrieval Note",  # Also fix misspelling
        "Retrieval Note": "Solcom – Retrieval Note",
    }
    
    # Longest-first with a guard against re-correcting already-correct text.
    # Dict order previously turned "Solcom – Retrieval Note" into
    # "Solcom – Solcom – Retrieval Note".
    for incorrect, correct in sorted(corrections.items(), key=lambda kv: len(kv[0]), reverse=True):
        if incorrect == correct or incorrect not in response:
            continue
        prefix = correct[: correct.find(incorrect)] if incorrect in correct else ""
        pattern = (f"(?<!{re.escape(prefix)})" if prefix else "") + re.escape(incorrect)
        fixed = re.sub(pattern, correct, response)
        if fixed != response:
            print(f"🔧 Fixing terminology: '{incorrect}' -> '{correct}'")
            response = fixed
    
    # Fix VPU section mixing issues
    if 'VPU – Picking Up Records' in response and 'VPU – No X-Rays' in response:
        # Check if the response is mixing sections
        lines = response.split('\n')
        vpu_pickup = False
        vpu_no_xrays = False
        fixed_lines = []
        current_section = None
        
        for line in lines:
            if 'VPU – Picking Up Records' in line:
                vpu_pickup = True
                current_section = 'pickup'
            elif 'VPU – No X-Rays' in line:
                vpu_no_xrays = True
                current_section = 'noxrays'
            
            # If we're in pickup section, remove noxrays content
            if current_section == 'pickup' and 'No X-Rays' in line and 'VPU' not in line:
                continue
                
            fixed_lines.append(line)
        
        if vpu_pickup and vpu_no_xrays:
            response = '\n'.join(fixed_lines)
            print("🔧 Fixed VPU section mixing")
    
    return response

def pick_answer_chunk(query: str, chunks: list) -> dict:
    """
    Choose the single chunk an answer is built from.

    This logic used to live inside build_smart_fallback(), so the answer could
    be taken from chunk #4 while the citation list still showed the retriever's
    order - the heading said "2.3 Central - Facility Maintenance Form" while
    Sources said "1.16 Escalate Review Orders". chat() now calls this first and
    cites the chunk it returns.

    Retrieval rank is the stronger signal; a keyword tally only breaks
    near-ties or overturns a clear mismatch.
    """
    if not chunks:
        return {}

    query_lower = query.lower()
    query_words = [word for word in query_lower.split() if len(word) > 2]

    scored = []
    for position, chunk in enumerate(chunks):
        header = (chunk['metadata'].get('header', '') or '').lower()
        topic_path = (chunk['metadata'].get('topic_path', '') or '').lower()
        text = (chunk.get('text', '') or '').lower()

        score = 0.0
        for word in query_words:
            if word in header or word in topic_path:
                score += 10
            if word in text[:500]:
                score += 3
        if query_lower in header:
            score += 50

        score += max(0, 12 - (position * 4))
        scored.append((score, position, chunk))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][2]


def build_smart_fallback(query: str, chunks: list) -> str:
    """
    Build a smart fallback response showing complete content with preserved structure
    """
    chunk = pick_answer_chunk(query, chunks)
    doc_name = chunk['metadata'].get('document_name', 'Unknown')
    header = chunk['metadata'].get('header', '')

    text = chunk['text'].strip()
    
    # Clean the text but preserve structure
    text = text.replace('**', '').replace('`', '')
    text = text.replace('##', '').replace('###', '')
    
    # Remove document prefix if present
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        if 'Document:' in line or 'Topic:' in line:
            continue
        cleaned_lines.append(line)
    
    # Build response - preserve the original structure
    answer_parts = []
    
    # Add header if not already in content
    header_text = (header or "").replace('*', '').replace('#', '').strip()
    if header_text and header_text not in '\n'.join(cleaned_lines):
        answer_parts.append(f"📋 {header_text}")
        answer_parts.append("")
    
    # Process all lines, preserving structure
    body_lines_written = False
    for line in cleaned_lines:
        line_clean = line.strip()
        if not line_clean:
            answer_parts.append("")
            continue
        
        # Skip markdown artifacts
        if line_clean.startswith('[') or line_clean.startswith('#'):
            continue
        if line_clean.startswith('---'):
            continue
        
        # Preserve the line as-is (don't modify numbering or bullets)
        answer_parts.append(line_clean.replace('**', ''))
        body_lines_written = True
    
    # Fall back to the raw lines ONLY if nothing was written above. The old
    # test counted the header line too, so a single-paragraph topic
    # (header + one body line = 2) re-appended its body and printed twice.
    if not body_lines_written:
        for line in cleaned_lines:
            if line.strip():
                answer_parts.append(line.strip().replace('**', ''))
    
    answer_parts.append("")
    answer_parts.append("---")
    answer_parts.append(f"*Source: {doc_name}*")
    
    return '\n'.join(answer_parts)


# ============================================================
# MAIN CHAT ENDPOINT
# ============================================================

@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    """Handle chat requests with RAG + LLM + FAQ extraction"""
    data = request.get_json(force=True)
    query = (data.get("query") or "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())
    document_filter = (data.get("filter_document") or "").strip() or None

    if not query:
        return jsonify({"error": "query is required"}), 400

    # Check if index exists
    if not check_index():
        return jsonify({
            "answer": "⚠️ The document index has not been built yet. Please run: python -m ingestion.build_index",
            "sources": [],
            "confidence": 0,
            "confidence_label": "Low"
        }), 200

    # Get relevant documents
    try:
        retrieved = hybrid_retrieve(query, document_filter=document_filter, top_k_final=config.TOP_K_FINAL)
    except Exception as e:
        print(f"❌ Retrieval error: {e}")
        return jsonify({
            "answer": "⚠️ I'm having trouble retrieving information. Please try again or rephrase your question.",
            "sources": [],
            "confidence": 0,
            "confidence_label": "Low"
        }), 200

    if not retrieved:
        answer = "I could not find this information in the available SOP documents. Please try rephrasing your question or check if the topic is covered in the documents."
        save_turn(session_id, "user", query)
        save_turn(session_id, "assistant", answer, sources=[], confidence=0.0)
        return jsonify({
            "session_id": session_id,
            "answer": answer,
            "sources": [],
            "confidence": 0,
            "confidence_label": "Low"
        }), 200

    # The chunks that will actually be used.
    used_chunks = retrieved[:config.MAX_RELEVANT_CHUNKS]
    answer_chunk = pick_answer_chunk(query, used_chunks) if used_chunks else None

    # Cite the chunk the answer came from FIRST. Sources were previously in raw
    # retrieval order, so the top citation often did not match the text shown.
    ordered_chunks = ([answer_chunk] + [c for c in used_chunks if c is not answer_chunk]) \
        if answer_chunk else used_chunks

    sources = []
    seen_sources = set()
    for c in ordered_chunks:
        metadata = c.get("metadata", {})
        source = {
            "document": metadata.get("document_name", "Unknown"),
            "section": metadata.get("topic") or metadata.get("header", "General"),
            "sub_header": metadata.get("parent_topic") or metadata.get("sub_header", ""),
            "topic_path": metadata.get("topic_path", ""),
            "page": metadata.get("page_start") or metadata.get("page", ""),
            "page_end": metadata.get("page_end", ""),
        }
        source_key = (source["document"], source["topic_path"] or source["section"], source["page"])
        if source_key not in seen_sources:
            seen_sources.add(source_key)
            sources.append(source)

    # Images belong to the topic the answer came from.
    _image_sources = ([answer_chunk] if (answer_chunk and getattr(config, "IMAGES_FROM_ANSWER_ONLY", True))
                      else used_chunks)
    images = _image_urls_from_retrieved(_image_sources)
    confidence = max((c.get("confidence", 0) for c in used_chunks), default=0.0)

    confidence_label = (
        "High" if confidence >= config.CONFIDENCE_HIGH else
        "Medium" if confidence >= config.CONFIDENCE_MEDIUM else
        "Low"
    )

    # ============================================================
    # CONFIDENCE GATE - decline instead of answering from whatever
    # happened to rank highest on a vague or fragmentary query.
    # ============================================================
    min_confidence = float(getattr(config, "MIN_ANSWER_CONFIDENCE", 0.0))
    if confidence < min_confidence:
        answer = (
            "I could not find this in the SOP documents with enough confidence to answer.\n\n"
            "Try naming the region and the procedure, for example:\n"
            "  - What is the Hartford escalation process?\n"
            "  - Central facility maintenance form\n\n"
            "You can also pick the SOP in the document filter to narrow the search."
        )
        save_turn(session_id, "user", query)
        save_turn(session_id, "assistant", answer, sources=[], confidence=confidence)
        return jsonify({
            "session_id": session_id,
            "answer": answer,
            "sources": [],
            "confidence": confidence,
            "confidence_label": "Low",
            "images": [],
        }), 200

    # ============================================================
    # FAQ EXTRACTION - Check if this is an FAQ query
    # ============================================================
    
    if is_faq_query(query):
        print("📝 FAQ query detected - attempting to extract specific FAQ")
        
        # Look for FAQ chunks in retrieved documents
        faq_chunk = None
        lead_document = used_chunks[0]['metadata'].get('document_name') if used_chunks else None
        for chunk in used_chunks[:2]:
            metadata = chunk['metadata']
            label = f"{metadata.get('header','')} {metadata.get('topic_path','')}".lower()
            if 'faq' not in label and 'frequently asked' not in label:
                continue
            # Never answer from another region's FAQ section.
            if metadata.get('document_name') != lead_document:
                continue
            if document_filter and metadata.get('document_name') != document_filter:
                continue
            faq_chunk = chunk
            break
        
        if faq_chunk:
            print("📝 Found FAQ chunk, extracting matching FAQ")
            faq_lines = extract_matching_faq(faq_chunk['text'], query)
            
            if faq_lines:
                # Build professional response from FAQ
                answer_parts = []
                answer_parts.append("📋 **FAQ Answer**")
                answer_parts.append("")
                
                for line in faq_lines:
                    if line.strip():
                        answer_parts.append(f"  {line}")
                
                answer_parts.append("")
                answer_parts.append("---")
                answer_parts.append(f"*Source: {faq_chunk['metadata']['document_name']}*")
                
                answer = '\n'.join(answer_parts)
                
                # Update sources to only show the FAQ chunk
                sources = [{
                    "document": faq_chunk["metadata"]["document_name"],
                    "section": faq_chunk["metadata"]["header"],
                    "sub_header": faq_chunk["metadata"].get("sub_header", ""),
                    "page": faq_chunk["metadata"].get("page", ""),
                }]
                
                # Save to history
                save_turn(session_id, "user", query)
                save_turn(session_id, "assistant", answer, sources=sources, confidence=confidence)
                
                confidence_label = (
                    "High" if confidence >= config.CONFIDENCE_HIGH else
                    "Medium" if confidence >= config.CONFIDENCE_MEDIUM else
                    "Low"
                )
                
                return jsonify({
                    "session_id": session_id,
                    "answer": answer,
                    "sources": sources,
                    "confidence": confidence,
                    "confidence_label": confidence_label,
                    "images": _image_urls_from_retrieved([faq_chunk]),
                })

    # ============================================================
    # RESPONSE GENERATION - RAG + LLM (for non-FAQ queries)
    # ============================================================
    
    # if config.USE_LLM:
    #     print("🤖 Generating RAG response with LLM...")
    #     try:
    #         # Use only the top chunk for LLM context
    #         llm_answer = ollama_client.generate_rag_response(query, retrieved[:config.MAX_RELEVANT_CHUNKS])
            
    #         # Check if LLM response is valid
    #         if llm_answer and len(llm_answer) > 30 and not any(x in llm_answer for x in ['❌', '⚠️', 'error']):
    #             answer = llm_answer
    #             print("✅ Using LLM response")
    #         else:
    #             print("⚠️ LLM response invalid, using smart fallback")
    #             answer = build_smart_fallback(query, retrieved)
                
    #     except Exception as e:
    #         print(f"❌ LLM error: {e}")
    #         answer = build_smart_fallback(query, retrieved)
    # else:
    #     print("📄 Using smart fallback")
    #     answer = build_smart_fallback(query, retrieved)

    # if config.USE_LLM:
    #     print("🤖 Generating RAG response with LLM...")
    #     try:
    #         # Use ALL relevant chunks for better context
    #         llm_answer = ollama_client.generate_rag_response(query, retrieved[:config.MAX_RELEVANT_CHUNKS])
            
    #         # Check if LLM response is valid
    #         if llm_answer and len(llm_answer) > 30 and not any(x in llm_answer for x in ['❌', '⚠️', 'error']):
    #             answer = llm_answer
    #             print("✅ Using LLM response")
    #         else:
    #             print("⚠️ LLM response invalid, using smart fallback")
    #             answer = build_smart_fallback(query, retrieved)
                
    #     except Exception as e:
    #         print(f"❌ LLM error: {e}")
    #         answer = build_smart_fallback(query, retrieved)
    # else:
    #     print("📄 Using smart fallback")
    #     answer = build_smart_fallback(query, retrieved)

    # if config.USE_LLM:
    #     print("🤖 Generating RAG response with LLM...")
    #     try:
    #         # Use ALL relevant chunks for better context
    #         llm_answer = ollama_client.generate_rag_response(query, retrieved[:config.MAX_RELEVANT_CHUNKS])
            
    #         # Validate LLM response against source content
    #         is_valid = False
    #         if llm_answer and len(llm_answer) > 30:
    #             # Check that the response contains content from the source
    #             source_text = " ".join([chunk['text'][:300] for chunk in retrieved[:2]])
    #             if source_text:
    #                 # Extract key phrases from source
    #                 source_words = set(source_text.lower().split())
    #                 response_words = set(llm_answer.lower().split())
                    
    #                 # If response shares at least 30% of words with source, it's likely valid
    #                 common_words = source_words.intersection(response_words)
    #                 if len(common_words) / max(len(source_words), 1) > 0.15:
    #                     is_valid = True
    #                 else:
    #                     print("⚠️ LLM response doesn't match source content - using fallback")
            
    #         if is_valid and not any(x in llm_answer for x in ['❌', '⚠️', 'error']):
    #             answer = llm_answer
    #             print("✅ Using LLM response")
    #         else:
    #             print("⚠️ LLM response invalid, using smart fallback")
    #             answer = build_smart_fallback(query, retrieved)
                
    #     except Exception as e:
    #         print(f"❌ LLM error: {e}")
    #         answer = build_smart_fallback(query, retrieved)
    # else:
    #     print("📄 Using smart fallback")
    #     answer = build_smart_fallback(query, retrieved)

    top_chunk = answer_chunk or (used_chunks[0] if used_chunks else None)
    structured = bool(top_chunk) and len(_structure_units(top_chunk.get("text", ""))) >= 3
    verbatim_mode = structured and getattr(config, "VERBATIM_PROCEDURE_ANSWERS", True)

    if config.USE_LLM and not verbatim_mode:
        print("🤖 Generating RAG response with LLM...")
        try:
            llm_answer = ollama_client.generate_rag_response(query, used_chunks)

            if llm_answer and len(llm_answer) > 30:
                # Remove any prompt scaffolding the model copied, then fix terms.
                llm_answer = strip_context_artifacts(llm_answer)
                llm_answer = fix_llm_hallucinations(llm_answer, query)

                if top_chunk and not answer_is_complete(llm_answer, top_chunk):
                    answer = build_smart_fallback(query, [top_chunk])
                elif not any(x in llm_answer for x in ['❌', '⚠️']):
                    answer = llm_answer
                    print("✅ Using LLM response (with fixes)")
                else:
                    print("⚠️ LLM response contains errors, using fallback")
                    answer = build_smart_fallback(query, used_chunks)
            else:
                print("⚠️ LLM response invalid, using smart fallback")
                answer = build_smart_fallback(query, used_chunks)

        except Exception as e:
            print(f"❌ LLM error: {e}")
            answer = build_smart_fallback(query, used_chunks)
    elif verbatim_mode:
        # Numbered / bulleted procedure: reproduce the topic exactly. A 1.5B
        # model cannot be trusted to copy an 9-step procedure without dropping
        # or paraphrasing steps.
        print("📄 Structured topic — serving verbatim")
        answer = build_smart_fallback(query, [top_chunk])
    else:
        print("📄 Using smart fallback")
        answer = build_smart_fallback(query, used_chunks)

    # Save to history
    save_turn(session_id, "user", query)
    save_turn(session_id, "assistant", answer, sources=sources, confidence=confidence)

    confidence_label = (
        "High" if confidence >= config.CONFIDENCE_HIGH else
        "Medium" if confidence >= config.CONFIDENCE_MEDIUM else
        "Low"
    )

    return jsonify({
        "session_id": session_id,
        "answer": answer,
        "sources": sources,
        "confidence": confidence,
        "confidence_label": confidence_label,
        "images": images,
    })


@app.route("/api/chat_history/<session_id>")
@login_required
def chat_history(session_id):
    db = get_db()
    rows = db.execute(
        "SELECT role, content, sources, confidence, created_at FROM chat_history WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/rebuild_index", methods=["POST"])
@login_required
def rebuild_index():
    try:
        result = subprocess.run(
            ["python", "-m", "ingestion.build_index"],
            cwd=config.BASE_DIR, capture_output=True, text=True, timeout=1800
        )
        success = result.returncode == 0
        if success:
            reset_retriever_cache()
        return jsonify({
            "success": success,
            "log": result.stdout[-4000:] + result.stderr[-2000:],
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/models")
@login_required
def get_models():
    try:
        resp = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
        models = resp.json().get('models', [])
        return jsonify({
            "models": [m.get('name') for m in models],
            "current_model": config.LLM_MODEL
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    init_db()
    init_auth_db()
    app.run(host="0.0.0.0", port=5000, debug=True)