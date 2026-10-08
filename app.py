import os
import sqlite3
import urllib.request
import urllib.parse
import json
import stripe
from PIL import Image
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from deepface import DeepFace

app = Flask(__name__)
app.secret_key = 'clave_secreta_super_segura_para_sesiones'

# Configuración de Stripe (Llave de prueba oficial de Stripe)
stripe.api_key = "sk_test_51PlaceholderKeyForTestingPurposesChangeLater"

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

SERPAPI_KEY = "8e1a53a0e5a6d43a6fc394aaedfc4274334054a2c8d2f29352ebd668305f7029"

# Inicializar la base de datos de usuarios y créditos
def init_db():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            credits INTEGER DEFAULT 3
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def upload_to_temp_host(file_path):
    try:
        with open(file_path, 'rb') as f:
            file_data = f.read()
        boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
        body = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="reqtype"\r\n\r\nfileupload\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="fileToUpload"; filename="{os.path.basename(file_path)}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n'
        ).encode('utf-8') + file_data + f'\r\n--{boundary}--\r\n'.encode('utf-8')
        
        req = urllib.request.Request(
            'https://catbox.moe/user/api.php',
            data=body,
            headers={
                'Content-Type': f'multipart/form-data; boundary={boundary}',
                'User-Agent': 'Mozilla/5.0'
            },
            method='POST'
        )
        with urllib.request.urlopen(req) as response:
            public_url = response.read().decode('utf-8').strip()
            if public_url.startswith('http'):
                return public_url
    except Exception as e:
        print("Error subiendo imagen:", e)
    return None

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT credits, email FROM users WHERE id = ?', (session['user_id'],))
    user = cursor.fetchone()
    conn.close()
    
    credits = user[0] if user else 0
    email = user[1] if user else ""
    
    return render_template('index.html', user_email=email, user_credits=credits)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        if not email or not password:
            return render_template('register.html', error="Por favor completa todos los campos")
        
        hashed_password = generate_password_hash(password)
        try:
            conn = sqlite3.connect('database.db')
            cursor = conn.cursor()
            cursor.execute('INSERT INTO users (email, password, credits) VALUES (?, ?, 3)', (email, hashed_password))
            conn.commit()
            conn.close()
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            return render_template('register.html', error="Este correo ya está registrado")
            
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        cursor.execute('SELECT id, password, credits FROM users WHERE email = ?', (email,))
        user = cursor.fetchone()
        conn.close()
        
        if user and check_password_hash(user[1], password):
            session['user_id'] = user[0]
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="Correo o contraseña incorrectos")
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

@app.route('/buy-credits', methods=['POST'])
def buy_credits():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'Paquete de 10 Créditos - Reversely AI',
                    },
                    'unit_amount': 500,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('payment_success', _external=True),
            cancel_url=url_for('index', _external=True),
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/payment-success')
def payment_success():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET credits = credits + 10 WHERE id = ?', (session['user_id'],))
    conn.commit()
    conn.close()
    
    return redirect(url_for('index'))

@app.route('/search', methods=['POST'])
def search():
    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401
        
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT credits FROM users WHERE id = ?', (session['user_id'],))
    user = cursor.fetchone()
    
    if not user or user[0] <= 0:
        conn.close()
        return jsonify({'success': False, 'error': 'Te has quedado sin créditos. Recarga tu cuenta para continuar.'}), 403
        
    cursor.execute('UPDATE users SET credits = credits - 1 WHERE id = ?', (session['user_id'],))
    conn.commit()
    conn.close()

    if 'image' not in request.files:
        return jsonify({'error': 'No se proporcionó imagen'}), 400
    
    file = request.files['image']
    search_type = request.form.get('type', 'face')
    
    if file.filename == '':
        return jsonify({'error': 'Nombre vacío'}), 400

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(filepath)

    try:
        try:
            img = Image.open(filepath).convert('RGB')
            clean_filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'clean_' + file.filename)
            img.save(clean_filepath, 'JPEG')
            filepath = clean_filepath
        except Exception as img_err:
            print("Aviso conversión:", img_err)

        analysis = DeepFace.analyze(
            img_path=filepath, 
            actions=['age', 'gender', 'emotion', 'race'],
            enforce_detection=False
        )
        data = analysis[0] if isinstance(analysis, list) else analysis
        clean_data = {
            'age': float(data.get('age', 0)),
            'dominant_gender': str(data.get('dominant_gender', 'N/A')),
            'dominant_emotion': str(data.get('dominant_emotion', 'N/A')),
            'dominant_race': str(data.get('dominant_race', 'N/A'))
        }
        
        web_matches = []

        if SERPAPI_KEY:
            public_image_url = upload_to_temp_host(filepath)
            if public_image_url:
                try:
                    params = {
                        "engine": "yandex_images",
                        "url": public_image_url,
                        "api_key": SERPAPI_KEY
                    }
                    query_string = urllib.parse.urlencode(params)
                    url = f"https://serpapi.com/search.json?{query_string}"
                    
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req) as response:
                        result_data = json.loads(response.read().decode())
                        
                        matches_list = (
                            result_data.get("image_results", []) or 
                            result_data.get("sites", []) or 
                            result_data.get("visual_matches", [])
                        )
                        
                        for item in matches_list[:6]:
                            web_matches.append({
                                "title": item.get("title", item.get("snippet", "Coincidencia Yandex")),
                                "url": item.get("link", item.get("url", "#")),
                                "match": "Alta similitud",
                                "category": "Búsqueda Visual Yandex"
                            })
                except Exception as api_err:
                    print("Error en Yandex:", api_err)

        if not web_matches:
            web_matches = [
                {"title": "Sin coincidencias visuales exactas en Yandex", "url": "#", "match": "0%", "category": "Aviso"}
            ]

        if os.path.exists(filepath):
            os.remove(filepath)

        return jsonify({
            'success': True, 
            'analysis': clean_data,
            'web_matches': web_matches,
            'search_type': search_type
        })
    except Exception as e:
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)