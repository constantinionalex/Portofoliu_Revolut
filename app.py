import os
import time
import requests
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'instance', 'portfolio.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# CONFIG
TWELVE_DATA_KEY = "0eef54e01c5b4f6aa18c054d569084de"

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(15), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    current_price = db.Column(db.Float, default=0.0)
    last_signal = db.Column(db.String(10), default="AȘTEAPTĂ")
    tech_details = db.Column(db.String(200), default="În curs de actualizare...")

with app.app_context():
    db.create_all()

def fetch_bvb_price(symbol):
    """Interogare Yahoo Finance pentru BVB (ex: H2O.BVB)"""
    # Forțăm terminația .BVB pentru acțiunile RO
    clean_s = symbol.replace(".RO", ".BVB")
    if ".BVB" not in clean_s: clean_s += ".BVB"
    
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={clean_s}"
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        data = r.json()
        result = data['quoteResponse']['result'][0]
        return float(result['regularMarketPrice'])
    except:
        return None

def update_background():
    """Funcția care rulează în fundal fără să blocheze site-ul"""
    with app.app_context():
        stocks = Stock.query.all()
        for s in stocks:
            # --- CAZUL 1: ROMÂNIA (BVB) ---
            if ".RO" in s.symbol or ".BVB" in s.symbol or any(x in s.symbol for x in ["H2O", "SNN", "SNP"]):
                price = fetch_bvb_price(s.symbol)
                if price:
                    s.current_price = price
                    s.tech_details = f"Preț RO: {price} RON (Yahoo)"
                    s.last_signal = "HOLD" # Pe BVB urmărim doar prețul momentan
                db.session.commit()
            
            # --- CAZUL 2: SUA (Twelve Data) ---
            else:
                try:
                    base = "https://api.twelvedata.com"
                    # 1. Preț curent
                    p_res = requests.get(f"{base}/quote?symbol={s.symbol}&apikey={TWELVE_DATA_KEY}").json()
                    if "close" in p_res: s.current_price = float(p_res['close'])
                    
                    # 2. Indicatori Rule #1 (Pauză între apeluri să nu luăm block)
                    time.sleep(8) 
                    ma = requests.get(f"{base}/ma?symbol={s.symbol}&interval=1day&time_period=10&apikey={TWELVE_DATA_KEY}").json()
                    time.sleep(8)
                    macd = requests.get(f"{base}/macd?symbol={s.symbol}&interval=1day&apikey={TWELVE_DATA_KEY}").json()
                    time.sleep(8)
                    stoch = requests.get(f"{base}/stoch?symbol={s.symbol}&interval=1day&fast_k_period=14&slow_k_period=5&slow_d_period=5&apikey={TWELVE_DATA_KEY}").json()
                    
                    # Logica de calcul semnal
                    m = float(ma['values'][0]['ma'])
                    md, ms = float(macd['values'][0]['macd']), float(macd['values'][0]['macd_signal'])
                    sk, sd = float(stoch['values'][0]['slow_k']), float(stoch['values'][0]['slow_d'])
                    
                    c_buy = (s.current_price > m) and (md > ms) and (sk > sd)
                    c_sell = (s.current_price < m) and (md < ms) and (sk < sd)
                    
                    s.tech_details = f"MA:{round(m,1)} | MACD:{round(md,2)}/{round(ms,2)} | ST:{round(sk,1)}/{round(sd,1)}"
                    s.last_signal = "BUY" if c_buy else "SELL" if c_sell else "HOLD"
                except:
                    s.tech_details = "Eroare API / Limită atinsă (TwelveData)"
                
                db.session.commit()
                time.sleep(2) # Pauză finală înainte de următoarea acțiune

# Scheduler pentru update automat la 30 min
sched = BackgroundScheduler()
sched.add_job(update_background, 'interval', minutes=30)
sched.start()

@app.route('/')
def index():
    stocks = Stock.query.all()
    return render_template('index.html', stocks=stocks)

@app.route('/add', methods=['POST'])
def add():
    s = request.form.get('symbol', '').upper().strip()
    p = float(request.form.get('price', 0))
    if s and not Stock.query.filter_by(symbol=s).first():
        db.session.add(Stock(symbol=s, purchase_price=p))
        db.session.commit()
    return jsonify({"status": "ok"})

@app.route('/refresh_manual')
def refresh_manual():
    # NU chemăm funcția direct aici pentru că dă 502 Bad Gateway
    # Scheduler-ul o va rula oricum, sau o pornim într-un thread separat
    import threading
    threading.Thread(target=update_background).start()
    return jsonify({"status": "Update început în fundal..."})

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete(id):
    s = db.session.get(Stock, id)
    if s: db.session.delete(s); db.session.commit()
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
