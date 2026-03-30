import os
import datetime
import requests
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
# Baza de date locala pentru a stoca preturile (Cache)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///portfolio.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# CONFIGURARE
TWELVE_DATA_KEY = "10f7aeb538ed4f709079dbe22841590b"
TELEGRAM_TOKEN = os.environ.get('TG_TOKEN')
TELEGRAM_ID = os.environ.get('TG_ID')

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(10), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    current_price = db.Column(db.Float, default=0.0)  # Salvam pretul aici
    high_price = db.Column(db.Float, default=0.0)     # Salvam maximul aici
    last_alert_date = db.Column(db.String(20), default="")

with app.app_context():
    db.create_all()

def send_telegram_msg(message):
    if not TELEGRAM_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?chat_id={TELEGRAM_ID}&text={message}"
    try: requests.get(url, timeout=10)
    except: print("Eroare Telegram")

def get_batch_data(symbols):
    if not symbols: return {}
    try:
        sym_str = ",".join(symbols)
        url = f"https://api.twelvedata.com/quote?symbol={sym_str}&apikey={TWELVE_DATA_KEY}"
        response = requests.get(url, timeout=15).json()
        
        # Verificam daca API-ul a intors o eroare de limita
        if isinstance(response, dict) and response.get("status") == "error":
            print(f"⚠️ Limita API: {response.get('message')}")
            return None
            
        if len(symbols) == 1:
            return {symbols[0]: response}
        return response
    except:
        return None

def check_prices():
    """Functia care ruleaza la 3 min si actualizeaza Baza de Date"""
    with app.app_context():
        stocks = Stock.query.all()
        if not stocks: return
        
        symbols = [s.symbol for s in stocks]
        data_cloud = get_batch_data(symbols)
        
        if data_cloud is None:
            return # Nu facem update daca API-ul a dat eroare

        today = datetime.date.today().isoformat()
        for stock in stocks:
            res = data_cloud.get(stock.symbol)
            if res and "close" in res:
                # Actualizam valorile in DB (Cache)
                stock.current_price = float(res['close'])
                stock.high_price = float(res['high'])
                
                # Logica de alerte
                if stock.last_alert_date != today:
                    alert_sent = False
                    if stock.current_price <= stock.purchase_price * 0.95:
                        send_telegram_msg(f"⚠️ {stock.symbol}: -5% vs achizitie ({stock.current_price}$)")
                        alert_sent = True
                    elif stock.current_price <= stock.high_price * 0.85:
                        send_telegram_msg(f"📉 {stock.symbol}: -15% vs maxim ({stock.current_price}$)")
                        alert_sent = True
                    
                    if alert_sent:
                        stock.last_alert_date = today
        
        db.session.commit()
        print(f"✅ [{datetime.datetime.now().strftime('%H:%M:%S')}] DB Actualizata.")

# Pornim monitorizarea la 3 minute
scheduler = BackgroundScheduler()
scheduler.add_job(func=check_prices, trigger="interval", minutes=3)
scheduler.start()

@app.route('/')
def index():
    # Citim DOAR din baza de date (fara apeluri API la refresh)
    stocks = Stock.query.all()
    results = []
    for s in stocks:
        profit = ((s.current_price - s.purchase_price) / s.purchase_price * 100) if s.current_price > 0 else 0
        results.append({
            'id': s.id, 'symbol': s.symbol, 'buy': s.purchase_price,
            'current': round(s.current_price, 2), 'high': round(s.high_price, 2),
            'profit': round(profit, 2)
        })
    return render_template('index.html', stocks=results)

@app.route('/search')
def search_stock():
    query = request.args.get('q')
    if not query: return jsonify([])
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query}"
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        return jsonify([{'symbol': q['symbol'], 'name': q.get('shortname', '')} for q in resp.json().get('quotes', []) if q.get('quoteType') == 'EQUITY'])
    except: return jsonify([])

@app.route('/add', methods=['POST'])
def add_stock():
    symbol = request.form.get('symbol', '').upper()
    price = request.form.get('price', 0)
    if symbol and not Stock.query.filter_by(symbol=symbol).first():
        db.session.add(Stock(symbol=symbol, purchase_price=float(price)))
        db.session.commit()
        # Fortam o actualizare imediata pentru noua actiune
        check_prices()
    return jsonify({"status": "ok"})

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete_stock(id):
    # Folosim Session.get() pentru a evita Legacy Warning
    stock = db.session.get(Stock, id)
    if stock:
        db.session.delete(stock)
        db.session.commit()
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
