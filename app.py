import os
import time
import requests
import threading
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'instance', 'portfolio.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# CONFIGURARE
TWELVE_DATA_KEY = "0eef54e01c5b4f6aa18c054d569084de"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(20), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    current_price = db.Column(db.Float, default=0.0)
    last_signal = db.Column(db.String(10), default="AȘTEAPTĂ")
    tech_details = db.Column(db.String(200), default="În curs de actualizare...")

with app.app_context():
    db.create_all()

def calculate_ro_indicators(prices):
    """Calcul local MA10 și MACD simplu pentru BVB"""
    if len(prices) < 26: return None, None, None
    ma10 = sum(prices[-10:]) / 10
    ema12 = sum(prices[-12:]) / 12
    ema26 = sum(prices[-26:]) / 26
    macd = ema12 - ema26
    signal = macd * 0.95 # Linie de semnal estimată
    return round(ma10, 2), round(macd, 3), round(signal, 3)

def update_worker():
    """Procesul de fundal care nu blochează interfața"""
    with app.app_context():
        stocks = Stock.query.all()
        for s in stocks:
            sym = s.symbol.upper()
            
            # --- CAZUL 1: ROMÂNIA (Calcul Simplificat) ---
            if ".RO" in sym or ".BVB" in sym:
                try:
                    # Folosim Chart API pentru preț curent + istoric 60 zile
                    clean_sym = sym.replace(".BVB", ".RO")
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{clean_sym}?range=60d&interval=1d"
                    r = requests.get(url, headers=HEADERS, timeout=10)
                    data = r.json()
                    
                    result = data['chart']['result'][0]
                    s.current_price = float(result['meta']['regularMarketPrice'])
                    
                    # Extragere prețuri închidere pentru indicatori
                    hist_prices = [p for p in result['indicators']['quote'][0]['close'] if p is not None]
                    ma10, macd, sig = calculate_ro_indicators(hist_prices)
                    
                    if ma10:
                        s.tech_details = f"MA10: {ma10} | MACD: {macd}"
                        s.last_signal = "BUY" if (s.current_price > ma10 and macd > sig) else "SELL"
                    else:
                        s.tech_details = "Istoric insuficient pe Yahoo"
                except Exception as e:
                    s.tech_details = "Eroare date RO"
                db.session.commit()

            # --- CAZUL 2: SUA (Analiză Twelve Data) ---
            else:
                try:
                    base = "https://api.twelvedata.com"
                    # 1. Preț Curent
                    p_res = requests.get(f"{base}/quote?symbol={sym}&apikey={TWELVE_DATA_KEY}").json()
                    if "close" in p_res: s.current_price = float(p_res['close'])
                    
                    # 2. Indicatori (Pauză 12s pentru a respecta limita de 8 req/min)
                    time.sleep(12)
                    ma = requests.get(f"{base}/ma?symbol={sym}&interval=1day&time_period=10&apikey={TWELVE_DATA_KEY}").json()
                    time.sleep(12)
                    macd = requests.get(f"{base}/macd?symbol={sym}&interval=1day&apikey={TWELVE_DATA_KEY}").json()
                    time.sleep(12)
                    stoch = requests.get(f"{base}/stoch?symbol={sym}&interval=1day&fast_k_period=14&slow_k_period=5&slow_d_period=5&apikey={TWELVE_DATA_KEY}").json()
                    
                    m_v = float(ma['values'][0]['ma'])
                    md_v, ms_v = float(macd['values'][0]['macd']), float(macd['values'][0]['macd_signal'])
                    sk_v, sd_v = float(stoch['values'][0]['slow_k']), float(stoch['values'][0]['slow_d'])
                    
                    # Logica Phil Town: Preț > MA10 ȘI MACD > Signal ȘI StochK > StochD
                    c_buy = (s.current_price > m_v) and (md_v > ms_v) and (sk_v > sd_v)
                    c_sell = (s.current_price < m_v) and (md_v < ms_v) and (sk_v < sd_v)
                    
                    s.tech_details = f"MA:{round(m_v,1)} | MACD:{round(md_v,2)}/{round(ms_v,2)} | ST:{round(sk_v,1)}/{round(sd_v,1)}"
                    s.last_signal = "BUY" if c_buy else "SELL" if c_sell else "HOLD"
                except:
                    s.tech_details = "Limită API SUA / Simbol invalid"
                
                db.session.commit()
                time.sleep(2)

@app.route('/')
def index():
    stocks = Stock.query.all()
    return render_template('index.html', stocks=stocks)

@app.route('/refresh_manual')
def refresh_manual():
    # Pornim thread-ul și scăpăm de eroarea 502 (Cloudflare timeout)
    threading.Thread(target=update_worker).start()
    return jsonify({"status": "Pornit"})

@app.route('/search')
def search():
    q = request.args.get('q', '').upper()
    if len(q) < 2: return jsonify([])
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={q}"
        r = requests.get(url, headers=HEADERS, timeout=5)
        data = r.json()
        return jsonify([{'symbol': x['symbol'], 'name': x.get('shortname', '')} for x in data.get('quotes', []) if x.get('quoteType') in ['EQUITY', 'ETF']])
    except: return jsonify([])

@app.route('/add', methods=['POST'])
def add():
    s = request.form.get('symbol', '').upper().strip()
    p = float(request.form.get('price', 0))
    if s and not Stock.query.filter_by(symbol=s).first():
        db.session.add(Stock(symbol=s, purchase_price=p))
        db.session.commit()
    return jsonify({"status": "ok"})

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete(id):
    s = db.session.get(Stock, id)
    if s:
        db.session.delete(s)
        db.session.commit()
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
