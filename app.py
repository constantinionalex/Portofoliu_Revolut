import os
import time
import requests
import yfinance as yf
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
TELEGRAM_TOKEN = os.environ.get('TG_TOKEN')
TELEGRAM_ID = os.environ.get('TG_ID')

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(15), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    current_price = db.Column(db.Float, default=0.0)
    last_signal = db.Column(db.String(10), default="HOLD")
    tech_details = db.Column(db.String(200), default="Se încarcă...")

with app.app_context():
    db.create_all()

def send_telegram(msg):
    if TELEGRAM_TOKEN and TELEGRAM_ID:
        try: requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?chat_id={TELEGRAM_ID}&text={msg}", timeout=5)
        except: print("⚠️ Eroare Telegram")

def get_twelve_indicators(symbol):
    """Obține indicatorii Rule #1 de la Twelve Data (Piața SUA)"""
    base = "https://api.twelvedata.com"
    try:
        # MA 10
        ma = requests.get(f"{base}/ma?symbol={symbol}&interval=1day&time_period=10&apikey={TWELVE_DATA_KEY}").json()
        time.sleep(8) # Protecție limită API (8 req/min)
        
        # MACD
        macd = requests.get(f"{base}/macd?symbol={symbol}&interval=1day&apikey={TWELVE_DATA_KEY}").json()
        time.sleep(8)
        
        # STOCH (Sincronizat 14, 5, 5)
        stoch = requests.get(f"{base}/stoch?symbol={symbol}&interval=1day&fast_k_period=14&slow_k_period=5&slow_d_period=5&apikey={TWELVE_DATA_KEY}").json()
        
        return {
            "ma": float(ma['values'][0]['ma']),
            "macd": float(macd['values'][0]['macd']),
            "sig": float(macd['values'][0]['macd_signal']),
            "k": float(stoch['values'][0]['slow_k']),
            "d": float(stoch['values'][0]['slow_d'])
        }
    except: return None

def update_all():
    with app.app_context():
        stocks = Stock.query.all()
        for s in stocks:
            # --- CAZUL 1: Bursa de Valori București (BVB) ---
            if ".RO" in s.symbol or ".BVB" in s.symbol:
                try:
                    # Convertim formatul pentru Yahoo Finance (ex: H2O.RO -> H2O.BVB)
                    yf_symbol = s.symbol.replace(".RO", ".BVB")
                    ticker = yf.Ticker(yf_symbol)
                    data = ticker.history(period="1d")
                    if not data.empty:
                        s.current_price = round(data['Close'].iloc[-1], 2)
                        s.tech_details = "Preț preluat din BVB (Yahoo)"
                        s.last_signal = "HOLD" # Indicatorii tehnici BVB necesită calcul manual local
                except Exception as e:
                    s.tech_details = f"Eroare BVB: {str(e)}"

            # --- CAZUL 2: Bursa SUA (Twelve Data) ---
            else:
                # Update Preț curent (Batch logic ar fi mai bun, dar păstrăm simplitatea pentru stabilitate)
                try:
                    price_res = requests.get(f"https://api.twelvedata.com/quote?symbol={s.symbol}&apikey={TWELVE_DATA_KEY}").json()
                    if "close" in price_res:
                        s.current_price = float(price_res['close'])
                except: pass

                # Update Indicatori Rule #1
                t = get_twelve_indicators(s.symbol)
                if t:
                    c_sell = (s.current_price < t['ma']) and (t['macd'] < t['sig']) and (t['k'] < t['d'])
                    c_buy = (s.current_price > t['ma']) and (t['macd'] > t['sig']) and (t['k'] > t['d'])
                    
                    s.tech_details = f"MA:{round(t['ma'],1)} | MACD:{round(t['macd'],2)}/{round(t['sig'],2)} | ST:{round(t['k'],1)}/{round(t['d'],1)}"
                    new_status = "BUY" if c_buy else "SELL" if c_sell else "HOLD"
                    
                    if new_status != s.last_signal and new_status in ["BUY", "SELL"]:
                        send_telegram(f"📢 {s.symbol}: {new_status} la ${s.current_price}")
                    s.last_signal = new_status
                else:
                    s.tech_details = "Limită API TwelveData / Simbol nesuportat"

            db.session.commit()
            time.sleep(2) # Pauză între acțiuni

# SCHEDULER
sched = BackgroundScheduler()
sched.add_job(update_all, 'interval', minutes=60)
sched.start()

@app.route('/')
def index():
    stocks = Stock.query.all()
    return render_template('index.html', stocks=stocks)

@app.route('/search')
def search():
    q = request.args.get('q', '').upper()
    if len(q) < 2: return jsonify([])
    # Căutare prin Yahoo Finance pentru a găsi și simboluri RO (.BVB)
    r = requests.get(f"https://query1.finance.yahoo.com/v1/finance/search?q={q}", headers={'User-Agent': 'Mozilla/5.0'})
    return jsonify([{'symbol': x['symbol'], 'name': x.get('shortname', '')} for x in r.json().get('quotes', [])])

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
    if s: db.session.delete(s); db.session.commit()
    return jsonify({"status": "ok"})

@app.route('/refresh_manual')
def refresh_manual():
    update_all()
    return jsonify({"status": "done"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
