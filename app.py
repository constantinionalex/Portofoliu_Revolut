import os
import datetime
import requests
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
# IMPORTANT: Daca rulezi in Docker, baza de date e un fisier local.
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///portfolio.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# CONFIGURARE - API NOU
TWELVE_DATA_KEY = "0eef54e01c5b4f6aa18c054d569084de"
TELEGRAM_TOKEN = os.environ.get('TG_TOKEN')
TELEGRAM_ID = os.environ.get('TG_ID')

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(10), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    current_price = db.Column(db.Float, default=0.0) 
    high_price = db.Column(db.Float, default=0.0)    
    last_alert_date = db.Column(db.String(20), default="")

# --- REPARARE AUTOMATA LA START ---
with app.app_context():
    db.create_all()
    # Adaugam coloanele manual in SQL pur daca lipsesc din fisierul vechi
    for col in ["current_price", "high_price"]:
        try:
            db.session.execute(text(f"ALTER TABLE stock ADD COLUMN {col} FLOAT DEFAULT 0.0"))
            db.session.commit()
        except:
            db.session.rollback()

def get_batch_data(symbols):
    if not symbols: return {}
    try:
        sym_str = ",".join(symbols)
        url = f"https://api.twelvedata.com/quote?symbol={sym_str}&apikey={TWELVE_DATA_KEY}"
        resp = requests.get(url, timeout=10).json()
        if isinstance(resp, dict) and resp.get("status") == "error":
            return None
        return {symbols[0]: resp} if len(symbols) == 1 else resp
    except:
        return None

def check_prices():
    with app.app_context():
        stocks = Stock.query.all()
        if not stocks: return
        data = get_batch_data([s.symbol for s in stocks])
        if not data: return
        today = datetime.date.today().isoformat()
        for s in stocks:
            res = data.get(s.symbol)
            if res and "close" in res:
                s.current_price = float(res['close'])
                s.high_price = float(res['high'])
                # Logica Alerte Telegram
                if s.last_alert_date != today:
                    if s.current_price <= s.purchase_price * 0.95:
                        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?chat_id={TELEGRAM_ID}&text=⚠️ {s.symbol} a scazut sub 5%")
                        s.last_alert_date = today
        db.session.commit()

scheduler = BackgroundScheduler()
scheduler.add_job(func=check_prices, trigger="interval", minutes=3)
scheduler.start()

@app.route('/')
def index():
    stocks = Stock.query.all()
    ui_list = []
    for s in stocks:
        profit = ((s.current_price - s.purchase_price) / s.purchase_price * 100) if s.current_price > 0 else 0
        ui_list.append({
            'id': s.id, 'symbol': s.symbol, 'buy': s.purchase_price,
            'current': round(s.current_price, 2), 'high': round(s.high_price, 2),
            'profit': round(profit, 2)
        })
    return render_template('index.html', stocks=ui_list)

@app.route('/search')
def search_stock():
    q = request.args.get('q', '')
    if not q: return jsonify([])
    try:
        # Autocomplete folosind Yahoo Finance
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={q}"
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = r.json().get('quotes', [])
        return jsonify([{'symbol': x['symbol'], 'name': x.get('shortname', '')} for x in data if x.get('quoteType') == 'EQUITY'])
    except:
        return jsonify([])

@app.route('/add', methods=['POST'])
def add_stock():
    sym = request.form.get('symbol', '').upper()
    prc = float(request.form.get('price', 0))
    if sym and not Stock.query.filter_by(symbol=sym).first():
        db.session.add(Stock(symbol=sym, purchase_price=prc))
        db.session.commit()
        check_prices()
    return jsonify({"status": "ok"})

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete_stock(id):
    s = db.session.get(Stock, id)
    if s:
        db.session.delete(s)
        db.session.commit()
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
