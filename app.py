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
    last_signal = db.Column(db.String(10), default="HOLD") # BUY, SELL sau HOLD
    last_alert_date = db.Column(db.String(20), default="")

with app.app_context():
    db.create_all()
    # Migrare automata pentru coloana last_signal
    try:
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE stock ADD COLUMN last_signal STRING DEFAULT 'HOLD'"))
            conn.commit()
    except: pass

def send_telegram(msg):
    if TELEGRAM_TOKEN and TELEGRAM_ID:
        try: requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?chat_id={TELEGRAM_ID}&text={msg}", timeout=5)
        except: print("Eroare trimitere Telegram")

def get_indicators(symbol):
    """Obține indicatorii Rule #1: MA(10), MACD, Stochastic(14,3,3)"""
    base = "https://api.twelvedata.com"
    try:
        # Preț & MA10
        ma_req = requests.get(f"{base}/ma?symbol={symbol}&interval=1day&time_period=10&apikey={TWELVE_DATA_KEY}").json()
        # MACD (Standard 12,26,9)
        macd_req = requests.get(f"{base}/macd?symbol={symbol}&interval=1day&apikey={TWELVE_DATA_KEY}").json()
        # Stochastic (K=14, D=3)
        stoch_req = requests.get(f"{base}/stoch?symbol={symbol}&interval=1day&apikey={TWELVE_DATA_KEY}").json()
        
        return {
            "ma10": float(ma_req['values'][0]['ma']),
            "macd_hist": float(macd_req['values'][0]['macd_hist']),
            "stoch_k": float(stoch_req['values'][0]['slow_k']),
            "stoch_d": float(stoch_req['values'][0]['slow_d'])
        }
    except: return None

def check_prices():
    with app.app_context():
        stocks = Stock.query.all()
        if not stocks: return
        syms = [s.symbol for s in stocks]
        url = f"https://api.twelvedata.com/quote?symbol={','.join(syms)}&apikey={TWELVE_DATA_KEY}"
        data = requests.get(url).json()
        if "status" in str(data).lower() and "error" in str(data).lower(): return
        
        for s in stocks:
            res = data.get(s.symbol) if len(stocks) > 1 else data
            if res and "close" in res:
                s.current_price = float(res['close'])
                s.high_price = float(res.get('high', 0))
        db.session.commit()

def check_rule_one():
    """Verifică semnalele Phil Town o dată pe oră"""
    with app.app_context():
        stocks = Stock.query.all()
        for s in stocks:
            tech = get_indicators(s.symbol)
            if not tech: continue
            
            # Condiții Phil Town: Toți cei 3 indicatori trebuie să confirme
            is_price_above_ma = s.current_price > tech['ma10']
            is_macd_positive = tech['macd_hist'] > 0
            is_stoch_bullish = tech['stoch_k'] > tech['stoch_d']
            
            # Logica Semnal
            new_status = "HOLD"
            if is_price_above_ma and is_macd_positive and is_stoch_bullish:
                new_status = "BUY"
            elif not is_price_above_ma and not is_macd_positive and not is_stoch_bullish:
                new_status = "SELL"
            
            # Alertă Telegram doar la schimbare status (dacă e BUY sau SELL)
            if new_status != s.last_signal:
                if new_status in ["BUY", "SELL"]:
                    send_telegram(f"📢 SEMNAL {new_status}: {s.symbol} la ${s.current_price}")
                s.last_signal = new_status
        db.session.commit()

# SCHEDULER
sched = BackgroundScheduler()
sched.add_job(check_prices, 'interval', minutes=3)
sched.add_job(check_rule_one, 'interval', minutes=60) # Orar pentru indicatori
sched.start()

@app.route('/')
def index():
    stocks = Stock.query.all()
    ui = []
    for s in stocks:
        profit = round(((s.current_price - s.purchase_price) / s.purchase_price * 100), 2) if s.current_price > 0 else 0
        ui.append({'id':s.id, 'symbol':s.symbol, 'buy':s.purchase_price, 'current':s.current_price, 'high':s.high_price, 'signal':s.last_signal, 'profit':profit})
    return render_template('index.html', stocks=ui)

@app.route('/search')
def search():
    q = request.args.get('q', '')
    r = requests.get(f"https://query1.finance.yahoo.com/v1/finance/search?q={q}", headers={'User-Agent': 'Mozilla/5.0'})
    return jsonify([{'symbol': x['symbol'], 'name': x.get('shortname', '')} for x in r.json().get('quotes', []) if x.get('quoteType') in ['EQUITY', 'ETF']])

@app.route('/add', methods=['POST'])
def add():
    s, p = request.form.get('symbol').upper(), float(request.form.get('price'))
    if not Stock.query.filter_by(symbol=s).first():
        db.session.add(Stock(symbol=s, purchase_price=p)); db.session.commit()
        check_prices(); check_rule_one()
    return jsonify({"status": "ok"})

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete(id):
    s = db.session.get(Stock, id)
    if s: db.session.delete(s); db.session.commit()
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
