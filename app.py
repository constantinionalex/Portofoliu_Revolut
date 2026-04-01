import os
import time
import requests
import threading
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'instance', 'portfolio.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- CONFIGURARE ---
TWELVE_DATA_KEY = "0eef54e01c5b4f6aa18c054d569084de"
TELEGRAM_TOKEN = "8722371365:AAGiQ8g9M2LPNQIsYaM6V0KApwkKaJTi5vg"
TELEGRAM_CHAT_ID = "8708984447"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(20), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    current_price = db.Column(db.Float, default=0.0)
    last_signal = db.Column(db.String(10), default="HOLD")
    tech_details = db.Column(db.String(200), default="Așteaptă...")

with app.app_context():
    if not os.path.exists(os.path.join(basedir, 'instance')):
        os.makedirs(os.path.join(basedir, 'instance'))
    db.create_all()

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=5)
    except:
        pass

def calculate_ro_indicators(prices):
    if len(prices) < 30: return None, None, None, None
    ma10 = sum(prices[-10:]) / 10
    ema12 = sum(prices[-12:]) / 12
    ema26 = sum(prices[-26:]) / 26
    macd = ema12 - ema26
    signal_line = macd * 0.95
    deltas = [prices[i+1] - prices[i] for i in range(len(prices)-15, len(prices)-1)]
    gains = sum([d for d in deltas if d > 0]) / 14
    losses = sum([-d for d in deltas if d < 0]) / 14
    rsi = 100 if losses == 0 else 100 - (100 / (1 + (gains / losses)))
    return round(ma10, 2), round(macd, 3), round(signal_line, 3), round(rsi, 1)

def update_worker():
    """Funcția de bază care procesează toate acțiunile"""
    with app.app_context():
        stocks = Stock.query.all()
        for s in stocks:
            sym = s.symbol.upper()
            old_signal = s.last_signal
            
            if ".RO" in sym or ".BVB" in sym:
                try:
                    clean_sym = sym.replace(".BVB", ".RO")
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{clean_sym}?range=60d&interval=1d"
                    r = requests.get(url, headers=HEADERS, timeout=10)
                    data = r.json()
                    s.current_price = float(data['chart']['result'][0]['meta']['regularMarketPrice'])
                    hist = [p for p in data['chart']['result'][0]['indicators']['quote'][0]['close'] if p is not None]
                    ma10, macd, sig, rsi = calculate_ro_indicators(hist)
                    if ma10:
                        c_buy = (s.current_price > ma10) and (macd > sig) and (rsi > 50)
                        c_sell = (s.current_price < ma10) or (macd < sig)
                        s.last_signal = "BUY" if c_buy else "SELL" if c_sell else "HOLD"
                        s.tech_details = f"MA:{ma10} | MACD:{macd} | RSI:{rsi}"
                except: s.tech_details = "Eroare BVB"
            else:
                try:
                    base = "https://api.twelvedata.com"
                    p_res = requests.get(f"{base}/quote?symbol={sym}&apikey={TWELVE_DATA_KEY}").json()
                    if "close" in p_res: s.current_price = float(p_res['close'])
                    time.sleep(15) 
                    ma = requests.get(f"{base}/ma?symbol={sym}&interval=1day&time_period=10&apikey={TWELVE_DATA_KEY}").json()
                    time.sleep(15)
                    macd = requests.get(f"{base}/macd?symbol={sym}&interval=1day&apikey={TWELVE_DATA_KEY}").json()
                    time.sleep(15)
                    stoch = requests.get(f"{base}/stoch?symbol={sym}&interval=1day&apikey={TWELVE_DATA_KEY}").json()
                    
                    if 'values' in ma and 'values' in macd and 'values' in stoch:
                        m_v, md_v, ms_v = float(ma['values'][0]['ma']), float(macd['values'][0]['macd']), float(macd['values'][0]['macd_signal'])
                        sk_v, sd_v = float(stoch['values'][0]['slow_k']), float(stoch['values'][0]['slow_d'])
                        c_buy = (s.current_price > m_v) and (md_v > ms_v) and (sk_v > sd_v)
                        c_sell = (s.current_price < m_v) or (md_v < ms_v)
                        s.last_signal = "BUY" if c_buy else "SELL" if c_sell else "HOLD"
                        s.tech_details = f"MA:{round(m_v,1)} | MACD:{round(md_v,2)}/{round(ms_v,2)} | ST:{round(sk_v,1)}/{round(sd_v,1)}"
                    else: s.tech_details = "Limită API depășită"
                except: s.tech_details = "Eroare API SUA"

            if s.last_signal != old_signal and s.last_signal in ["BUY", "SELL"]:
                send_telegram(f"🔔 ALERTĂ {sym}: Semnal {s.last_signal} la {s.current_price}")
            db.session.commit()
            time.sleep(5)

# --- PROGRAMARE AUTOMATĂ (SCHEDULER) ---
scheduler = BackgroundScheduler()
# Rulează update_worker la fiecare 60 de minute
scheduler.add_job(func=update_worker, trigger="interval", minutes=60)
scheduler.start()

@app.route('/')
def index():
    stocks = Stock.query.all()
    return render_template('index.html', stocks=stocks)

@app.route('/refresh_manual')
def refresh_manual():
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
        return jsonify([{'symbol': x['symbol'], 'name': x.get('shortname', '')} for x in data.get('quotes', [])])
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
    try:
        app.run(host='0.0.0.0', port=5000, use_reloader=False) # use_reloader=False este important pentru scheduler
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
