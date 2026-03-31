import os
import time
import requests
import threading
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
instance_path = os.path.join(basedir, 'instance')
if not os.path.exists(instance_path):
    os.makedirs(instance_path)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(instance_path, 'portfolio.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# CONFIGURARE
TWELVE_DATA_KEY = "0eef54e01c5b4f6aa18c054d569084de"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(15), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    current_price = db.Column(db.Float, default=0.0)
    last_signal = db.Column(db.String(10), default="HOLD")
    tech_details = db.Column(db.String(200), default="Se încarcă...")

with app.app_context():
    db.create_all()

def fetch_bvb_price(symbol):
    """Interogare Yahoo Finance pentru prețuri BVB (ex: H2O.BVB)"""
    # Yahoo folosește .BVB pentru Bursa de Valori București
    clean_s = symbol.upper().replace(".RO", ".BVB")
    if ".BVB" not in clean_s and any(x in clean_s for x in ["H2O", "SNN", "SNP", "TLV", "FP"]):
        clean_s += ".BVB"
    
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={clean_s}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        data = r.json()
        if 'quoteResponse' in data and data['quoteResponse']['result']:
            result = data['quoteResponse']['result'][0]
            return float(result['regularMarketPrice'])
    except Exception as e:
        print(f"Eroare Yahoo BVB pentru {symbol}: {e}")
    return None

def update_background_task():
    """Rulează actualizarea acțiunilor rând pe rând în fundal"""
    with app.app_context():
        stocks = Stock.query.all()
        for s in stocks:
            # --- PIAȚA ROMÂNIA ---
            if ".RO" in s.symbol or ".BVB" in s.symbol or any(x in s.symbol for x in ["H2O", "SNN", "SNP"]):
                price = fetch_bvb_price(s.symbol)
                if price:
                    s.current_price = price
                    s.tech_details = f"Preț: {price} RON (Yahoo)"
                    s.last_signal = "HOLD"
                db.session.commit()
            
            # --- PIAȚA SUA (Rule #1) ---
            else:
                try:
                    base = "https://api.twelvedata.com"
                    # 1. Preț
                    p_res = requests.get(f"{base}/quote?symbol={s.symbol}&apikey={TWELVE_DATA_KEY}").json()
                    if "close" in p_res: s.current_price = float(p_res['close'])
                    
                    # 2. Indicatori (cu pauze pentru limită API)
                    time.sleep(8)
                    ma = requests.get(f"{base}/ma?symbol={s.symbol}&interval=1day&time_period=10&apikey={TWELVE_DATA_KEY}").json()
                    time.sleep(8)
                    macd = requests.get(f"{base}/macd?symbol={s.symbol}&interval=1day&apikey={TWELVE_DATA_KEY}").json()
                    time.sleep(8)
                    stoch = requests.get(f"{base}/stoch?symbol={s.symbol}&interval=1day&fast_k_period=14&slow_k_period=5&slow_d_period=5&apikey={TWELVE_DATA_KEY}").json()
                    
                    # Calcule $MA(10)$, $MACD(12,26,9)$ și $Stochastic(14,5,5)$
                    m = float(ma['values'][0]['ma'])
                    md, ms = float(macd['values'][0]['macd']), float(macd['values'][0]['macd_signal'])
                    sk, sd = float(stoch['values'][0]['slow_k']), float(stoch['values'][0]['slow_d'])
                    
                    c_buy = (s.current_price > m) and (md > ms) and (sk > sd)
                    c_sell = (s.current_price < m) and (md < ms) and (sk < sd)
                    
                    s.tech_details = f"MA:{round(m,1)} | MACD:{round(md,2)}/{round(ms,2)} | ST:{round(sk,1)}/{round(sd,1)}"
                    s.last_signal = "BUY" if c_buy else "SELL" if c_sell else "HOLD"
                except:
                    s.tech_details = "Limită API TwelveData depășită"
                
                db.session.commit()
                time.sleep(2)

# Scheduler
sched = BackgroundScheduler()
sched.add_job(update_background_task, 'interval', minutes=60)
sched.start()

@app.route('/')
def index():
    stocks = Stock.query.all()
    return render_template('index.html', stocks=stocks)

@app.route('/search')
def search():
    q = request.args.get('q', '').upper()
    if len(q) < 2: return jsonify([])
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={q}"
        r = requests.get(url, headers=HEADERS, timeout=5)
        data = r.json()
        output = []
        for x in data.get('quotes', []):
            # Filtrăm pentru a include și BVB și SUA
            if x.get('quoteType') in ['EQUITY', 'ETF']:
                output.append({'symbol': x['symbol'], 'name': x.get('shortname', '')})
        return jsonify(output)
    except:
        return jsonify([])

@app.route('/add', methods=['POST'])
def add():
    s = request.form.get('symbol', '').upper().strip()
    p = float(request.form.get('price', 0))
    if s and not Stock.query.filter_by(symbol=s).first():
        db.session.add(Stock(symbol=s, purchase_price=p))
        db.session.commit()
        # Pornim un update rapid în fundal doar pentru această acțiune dacă e nevoie
    return jsonify({"status": "ok"})

@app.route('/refresh_manual')
def refresh_manual():
    # Pornim thread-ul pentru a evita 502 Bad Gateway
    threading.Thread(target=update_background_task).start()
    return jsonify({"status": "Pornit"})

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete(id):
    s = db.session.get(Stock, id)
    if s:
        db.session.delete(s)
        db.session.commit()
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
