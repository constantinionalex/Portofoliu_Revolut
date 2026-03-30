import os
import datetime
import requests
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
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
    symbol = db.Column(db.String(10), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    current_price = db.Column(db.Float, default=0.0) 
    high_price = db.Column(db.Float, default=0.0)    
    last_signal = db.Column(db.String(10), default="HOLD")
    tech_details = db.Column(db.String(200), default="-")

with app.app_context():
    db.create_all()

def send_telegram(msg):
    if TELEGRAM_TOKEN and TELEGRAM_ID:
        try: requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?chat_id={TELEGRAM_ID}&text={msg}", timeout=5)
        except: print("⚠️ Eroare Telegram")

def get_indicators(symbol):
    """Sincronizat cu graficul tau: MA10, MACD(12,26,9), STOCH(14,5,5)"""
    base = "https://api.twelvedata.com"
    try:
        # MA 10
        ma = requests.get(f"{base}/ma?symbol={symbol}&interval=1day&time_period=10&apikey={TWELVE_DATA_KEY}").json()
        # MACD
        macd = requests.get(f"{base}/macd?symbol={symbol}&interval=1day&apikey={TWELVE_DATA_KEY}").json()
        # STOCH 14, 5, 5
        stoch = requests.get(f"{base}/stoch?symbol={symbol}&interval=1day&fast_k_period=14&slow_k_period=5&slow_d_period=5&apikey={TWELVE_DATA_KEY}").json()
        
        return {
            "ma10": round(float(ma['values'][0]['ma']), 2),
            "macd": round(float(macd['values'][0]['macd']), 3),
            "sig": round(float(macd['values'][0]['macd_signal']), 3),
            "k": round(float(stoch['values'][0]['slow_k']), 2),
            "d": round(float(stoch['values'][0]['slow_d']), 2)
        }
    except: return None

def check_prices():
    """Actualizeaza doar preturile (Consum mic)"""
    with app.app_context():
        stocks = Stock.query.all()
        if not stocks: return
        syms = [s.symbol for s in stocks]
        url = f"https://api.twelvedata.com/quote?symbol={','.join(syms)}&apikey={TWELVE_DATA_KEY}"
        data = requests.get(url).json()
        
        for s in stocks:
            res = data.get(s.symbol) if len(stocks) > 1 else data
            if res and "close" in res:
                s.current_price = float(res['close'])
                s.high_price = float(res.get('high', 0))
        db.session.commit()

def check_rule_one():
    """Recalculeaza indicatorii (Consum mediu - Rulare rara)"""
    with app.app_context():
        stocks = Stock.query.all()
        for s in stocks:
            t = get_indicators(s.symbol)
            if not t: continue
            
            # Conditii SELL (Pret < MA SI MACD < Signal SI K < D)
            c_sell = (s.current_price < t['ma10']) and (t['macd'] < t['sig']) and (t['k'] < t['d'])
            # Conditii BUY (Pret > MA SI MACD > Signal SI K > D)
            c_buy = (s.current_price > t['ma10']) and (t['macd'] > t['sig']) and (t['k'] > t['d'])

            s.tech_details = f"MA10:{t['ma10']} | MACD:{t['macd']}/{t['sig']} | ST:{t['k']}/{t['d']}"
            
            new_status = "HOLD"
            if c_buy: new_status = "BUY"
            elif c_sell: new_status = "SELL"
            
            if new_status != s.last_signal:
                if new_status in ["BUY", "SELL"]:
                    send_telegram(f"🔔 {s.symbol} {new_status} la ${s.current_price}")
                s.last_signal = new_status
        db.session.commit()

# SCHEDULER OPTIMIZAT
sched = BackgroundScheduler()
sched.add_job(check_prices, 'interval', minutes=5)    # Pretul la 5 min
sched.add_job(check_rule_one, 'interval', minutes=60) # Indicatorii la 60 min (economie credite)
sched.start()

@app.route('/')
def index():
    stocks = Stock.query.all()
    return render_template('index.html', stocks=stocks)

@app.route('/add', methods=['POST'])
def add():
    s, p = request.form.get('symbol').upper(), float(request.form.get('price', 0))
    if not Stock.query.filter_by(symbol=s).first():
        db.session.add(Stock(symbol=s, purchase_price=p))
        db.session.commit()
        # Forțează actualizarea imediată pentru noua acțiune
        check_prices()
        check_rule_one()
    return jsonify({"status": "ok"})

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete(id):
    s = db.session.get(Stock, id)
    if s: db.session.delete(s); db.session.commit()
    return jsonify({"status": "ok"})

@app.route('/search')
def search():
    q = request.args.get('q', '')
    r = requests.get(f"https://query1.finance.yahoo.com/v1/finance/search?q={q}", headers={'User-Agent': 'Mozilla/5.0'})
    return jsonify([{'symbol': x['symbol'], 'name': x.get('shortname', '')} for x in r.json().get('quotes', []) if x.get('quoteType') in ['EQUITY', 'ETF']])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
