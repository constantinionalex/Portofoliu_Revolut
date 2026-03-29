import os
import datetime
import requests
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///portfolio.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# CONFIGURARE DATE - Noul tau token
TWELVE_DATA_KEY = "10f7aeb538ed4f709079dbe22841590b"
TELEGRAM_TOKEN = os.environ.get('TG_TOKEN')
TELEGRAM_ID = os.environ.get('TG_ID')

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(10), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    last_alert_date = db.Column(db.String(20), default="")

with app.app_context():
    db.create_all()

def send_telegram_msg(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_ID:
        print("Lipsesc variabilele de mediu pentru Telegram.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?chat_id={TELEGRAM_ID}&text={message}"
    try: requests.get(url, timeout=10)
    except: print("Eroare trimitere Telegram")

def get_batch_data(symbols):
    if not symbols: return {}
    try:
        sym_str = ",".join(symbols)
        # Folosim endpoint-ul 'quote' pentru pret curent si maximul zilei
        url = f"https://api.twelvedata.com/quote?symbol={sym_str}&apikey={TWELVE_DATA_KEY}"
        response = requests.get(url, timeout=15).json()
        
        # Twelve Data returneaza dictionar daca e 1 simbol, sau dictionar de dictionare pentru mai multe
        if len(symbols) == 1:
            return {symbols[0]: response}
        return response
    except Exception as e:
        print(f"Eroare API Batch: {e}")
        return {}

def check_prices():
    with app.app_context():
        stocks = Stock.query.all()
        if not stocks: return
        
        symbols = [s.symbol for s in stocks]
        data_cloud = get_batch_data(symbols)
        today = datetime.date.today().isoformat()
        
        for stock in stocks:
            if stock.last_alert_date == today: continue
            
            res = data_cloud.get(stock.symbol)
            if not res or "close" not in res: continue
            
            current = float(res['close'])
            high = float(res['high'])
            
            alert_triggered = False
            # Alerta -5% vs Achizitie
            if current <= stock.purchase_price * 0.95:
                send_telegram_msg(f"⚠️ {stock.symbol}: -5% vs achizitie\nPret: {current:.2f}$")
                alert_triggered = True
            # Alerta -15% vs Maximul ultimei sesiuni
            elif current <= high * 0.85:
                send_telegram_msg(f"📉 {stock.symbol}: -15% vs maxim zi\nMaxim: {high:.2f}$\nPret: {current:.2f}$")
                alert_triggered = True
                
            if alert_triggered:
                stock.last_alert_date = today
                db.session.commit()

# Monitorizare constanta la fiecare 3 minute (24/7)
scheduler = BackgroundScheduler()
scheduler.add_job(func=check_prices, trigger="interval", minutes=3)
scheduler.start()

@app.route('/')
def index():
    stocks = Stock.query.all()
    symbols = [s.symbol for s in stocks]
    data_cloud = get_batch_data(symbols)
    
    table_results = []
    for s in stocks:
        res = data_cloud.get(s.symbol, {})
        current = float(res.get('close', 0))
        high = float(res.get('high', 0))
        profit = ((current - s.purchase_price) / s.purchase_price * 100) if current > 0 else 0
        
        table_results.append({
            'id': s.id, 'symbol': s.symbol, 'buy': s.purchase_price,
            'current': round(current, 2), 'high': round(high, 2),
            'profit': round(profit, 2), 'date': res.get('datetime', 'N/A')
        })
    return render_template('index.html', stocks=table_results)

@app.route('/search')
def search_stock():
    query = request.args.get('q')
    if not query: return jsonify([])
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query}"
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        data = resp.json()
        return jsonify([{'symbol': q['symbol'], 'name': q.get('shortname', '')} for q in data.get('quotes', []) if q.get('quoteType') in ['EQUITY', 'ETF']])
    except: return jsonify([])

@app.route('/add', methods=['POST'])
def add_stock():
    symbol = request.form.get('symbol', '').upper()
    price = request.form.get('price', 0)
    if symbol and not Stock.query.filter_by(symbol=symbol).first():
        db.session.add(Stock(symbol=symbol, purchase_price=float(price)))
        db.session.commit()
    return jsonify({"status": "ok"})

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete_stock(id):
    stock = Stock.query.get(id)
    if stock:
        db.session.delete(stock)
        db.session.commit()
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
